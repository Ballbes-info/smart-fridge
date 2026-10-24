"""Bounded offline match jobs with private, disposable SQLite result caches."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from services import recipe_matching as matching,recipe_catalog as catalog

_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='recipe-match')
_lock=threading.RLock()
_jobs={}
TTL=900


def fingerprint(owner,args):
    f,error=matching.filters(args);snap=matching.stock_snapshot(owner,args)
    path=catalog.catalog_path()
    sig=matching.signature(path) if path.is_file() else 'empty'
    stock=[(b['id'],b['name'],b['dimension'],str(b['quantity']),str(b['expiry']),b['storage_id'],b['storage']) for b in snap['batches']]
    values={k:v for k,v in f.items() if k not in ['sort','availability','urgent']}
    values.update(storage=snap['selected'],dated=snap['dated'],pantry=snap['pantry'],day=str(matching.product_service.today()))
    payload=(owner,str(path),sig,values,stock,error)
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def start(owner,args):
    args=dict(args);key=fingerprint(owner,args);now=time.time()
    with _lock:
        for k,j in list(_jobs.items()):
            if j['status']!='pending' and (now-j['touched']>TTL or (len(_jobs)>16 and k!=key)):
                try:os.unlink(j['file'])
                except OSError:pass
                del _jobs[k]
        if key in _jobs and _jobs[key]['status']=='failed' and args.get('retry')=='1':
            try:os.unlink(_jobs[key]['file'])
            except OSError:pass
            del _jobs[key]
        if key in _jobs:
            _jobs[key]['touched']=now;return _jobs[key]
        if sum(j['status']=='pending' for j in _jobs.values())>=4:
            return dict(status='busy',message='Подбор занят. Повторите через несколько секунд.')
        fd,name=tempfile.mkstemp(prefix='smartfridge-recipes-',suffix='.sqlite3');os.close(fd)
        job=dict(key=key,owner=owner,status='pending',phase='index',done=0,total=0,file=name,touched=now,result=None)
        _jobs[key]=job
    # Bound the synchronous preview to 128 candidates; it is independent of queue/index work.
    try:
        preview=matching.fast_preview(owner,args)
        if preview:job['partial']=preview
    except (OSError,sqlite3.Error):pass
    def update(phase,done,total,partial=None):
        with _lock:
            job.update(phase=phase,done=done,total=total)
            if partial:job['partial']=partial
    def work():
        try:
            result=matching.browse(owner,args,cache_file=name,progress=update)
            with _lock:
                job.update(status='ready',result=result,touched=time.time());job.pop('partial',None)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Recipe matching failed')
            with _lock:job.update(status='failed',message='Не удалось выполнить подбор. Повторите запрос.',touched=time.time())
    _pool.submit(work)
    return job


def page(job,args):
    result=dict(job['result']);f,error=matching.filters(args);result['filters']=f;result['filter_error']=error
    if not result.get('index_path'):return result
    where=[];params=[]
    if f['urgent']:where.append('s.urgent>0')
    if f['availability']=='ready':where.append('s.state_rank=0')
    elif f['availability']=='check':where.append('s.state_rank=1')
    elif f['availability']=='almost':where.append('s.missing BETWEEN 1 AND 2')
    condition=' WHERE '+' AND '.join(where) if where else ''
    with closing(sqlite3.connect(Path(job['file']).as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        con.execute('ATTACH DATABASE ? AS public_index',(Path(result['index_path']).as_uri()+'?mode=ro',))
        n=con.execute('SELECT count(*) FROM scores s'+condition,params).fetchone()[0];pages=max(1,math.ceil(n/24))
        tally=list(con.execute('SELECT state_rank,missing,count(*) FROM scores'+(' WHERE urgent>0' if f['urgent'] else '')+' GROUP BY state_rank,missing'))
        result['counts']={'ready':sum(r[2] for r in tally if r[0]==0),'check':sum(r[2] for r in tally if r[0]==1),'almost':sum(r[2] for r in tally if 1<=r[1]<=2)}
        try:p=max(1,min(pages,int(args.get('page',1))))
        except (TypeError,ValueError):p=1
        readiness='s.state_rank,s.missing,s.coverage DESC,s.checks'
        tail='e.minutes IS NULL,e.minutes,e.title,e.public_id'
        order={'match':readiness+',s.urgent DESC,'+tail,'expiry':'s.urgent DESC,'+readiness+','+tail,
               'time':'e.minutes IS NULL,e.minutes,'+readiness+',s.urgent DESC,e.title,e.public_id'}[f['sort']]
        cards=[]
        for row in con.execute('SELECT e.*,s.summary,s.needs FROM scores s JOIN public_index.entries e ON e.id=s.id'+condition+' ORDER BY '+order+' LIMIT 24 OFFSET ?',[*params,(p-1)*24]):
            cards.append(dict(key=row['public_id'],title=row['title'],category=row['category'],minutes=row['minutes'],servings=row['servings'],image=row['image'],complete=True,
                ingredients=[dict(name=i['name']) for i in json.loads(row['ingredients'])],needs=json.loads(row['needs']),match=json.loads(row['summary'])))
    result.update(recipes=cards,total=n,pages=pages,page=p)
    return result


def warmup():
    if catalog.catalog_path().is_file():
        def run():
            try:matching.ensure_index()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Recipe index warmup failed')
        _pool.submit(run)


# Remove only stale files from this cache's own namespace after interrupted processes.
for stale in Path(tempfile.gettempdir()).glob('smartfridge-recipes-*.sqlite3'):
    try:
        if time.time()-stale.stat().st_mtime>86400:stale.unlink()
    except OSError:pass
