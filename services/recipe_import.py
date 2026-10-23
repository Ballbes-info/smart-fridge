"""Offline/remote recipe ingestion. Run: python -m services.recipe_import --download"""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime,timezone
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import requests
from services import recipe_catalog as catalog

DATASET='d0rj/povarenok_recipes_detail'
VIEWER='https://datasets-server.huggingface.co'
HUB='https://huggingface.co'
MAX_RECORD=2*1024*1024
MAX_RECIPES=1000000


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    handle=open(str(path)+'.import.lock','a+b')
    try:
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise catalog.CatalogError('Другой импорт уже запущен. Дождитесь его завершения.')
        yield
    finally:
        # Closing releases the OS lock even after an interrupted import. Do not unlink a live lock inode.
        handle.close()


def read_json_array(stream):
    decoder=json.JSONDecoder();buf='';started=False;done=False;eof=False;expect_item=True;after_comma=False
    while not done:
        if not buf.strip() and not eof:
            chunk=stream.read(65536);buf+=chunk;eof=not chunk
        buf=buf.lstrip('\ufeff \n\r\t')
        if not started:
            if not buf.startswith('['):raise catalog.CatalogError('JSON должен содержать массив рецептов. Для JSONL — один объект на строку.')
            buf=buf[1:];started=True;continue
        if expect_item:
            buf=buf.lstrip()
            if buf.startswith(']'):
                if after_comma:raise catalog.CatalogError('После запятой JSON ожидается рецепт.')
                buf=buf[1:];done=True;break
            try:item,end=decoder.raw_decode(buf)
            except json.JSONDecodeError:
                if eof:raise catalog.CatalogError('JSON обрывается или содержит ошибку.')
                chunk=stream.read(65536);eof=not chunk;buf+=chunk
                if len(buf)>MAX_RECORD:raise catalog.CatalogError('Один рецепт превышает 2 МБ.')
                continue
            if end>MAX_RECORD:raise catalog.CatalogError('Один рецепт превышает 2 МБ.')
            yield item;buf=buf[end:];expect_item=False;after_comma=False
        else:
            if not buf and not eof:continue
            if buf.startswith(','):buf=buf[1:];expect_item=True;after_comma=True
            elif buf.startswith(']'):buf=buf[1:];done=True
            else:
                if not eof:
                    chunk=stream.read(65536);eof=not chunk;buf+=chunk;continue
                raise catalog.CatalogError('Между рецептами JSON ожидается запятая.')
    if buf.strip():raise catalog.CatalogError('После массива JSON есть посторонние данные.')
    while True:
        tail=stream.read(65536)
        if not tail:break
        if tail.strip():raise catalog.CatalogError('После массива JSON есть посторонние данные.')


def local_rows(path):
    path=Path(path);suffix=path.suffix.casefold()
    base_suffix=Path(path.stem).suffix.casefold() if suffix=='.gz' else suffix
    if base_suffix not in ['.json','.jsonl','.ndjson','.csv']:
        raise catalog.CatalogError('Поддерживаются JSON (массив), JSONL и CSV, в том числе .gz. Parquet преобразуйте в JSONL или используйте --download.')
    opener=gzip.open if suffix=='.gz' else open
    with opener(path,'rt',encoding='utf-8-sig',newline='') as stream:
        if base_suffix=='.csv':
            csv.field_size_limit(200000)
            sample=stream.read(16000);stream.seek(0)
            try:dialect=csv.Sniffer().sniff(sample,delimiters=',;\t')
            except csv.Error:raise catalog.CatalogError('Не удалось определить разделитель CSV.')
            yield from csv.DictReader(stream,dialect=dialect)
        elif base_suffix=='.json':yield from read_json_array(stream)
        else:
            while True:
                line=stream.readline(MAX_RECORD+1)
                if not line:break
                if len(line)>MAX_RECORD:raise catalog.CatalogError('Строка JSONL превышает 2 МБ.')
                if line.strip():yield json.loads(line)


def request_json(client,url,**params):
    for attempt in range(5):
        try:
            r=client.get(url,params=params,timeout=(15,90))
            if r.status_code in [429,500,502,503,504]:
                if attempt==4:raise catalog.CatalogError(f'Сервис базы временно недоступен (HTTP {r.status_code}). Повторите команду: загрузка продолжится.')
                retry=r.headers.get('Retry-After','')
                wait=min(30,int(retry)) if retry.isdigit() else min(20,2**attempt)
                time.sleep(wait);continue
            if r.status_code in [401,403]:raise catalog.CatalogError('Hugging Face не разрешил доступ. Проверьте доступность страницы датасета и его условия использования. Загрузка из локального файла также доступна.')
            if r.status_code>=400:raise catalog.CatalogError(f'Не удалось получить данные Hugging Face (HTTP {r.status_code}). Проверьте страницу датасета.')
            return r.json()
        except (requests.RequestException,ValueError) as exc:
            if attempt==4:raise catalog.CatalogError('Не удалось скачать базу. Проверьте доступ к huggingface.co и datasets-server.huggingface.co. Повтор той же команды продолжит загрузку.') from exc
            time.sleep(min(16,2**attempt))


def download(cache):
    """Viewer API returns ordinary JSON: no PyArrow/Torch dependency on Windows/Python 3.15."""
    cache.mkdir(parents=True,exist_ok=True)
    target=cache/'povarenok-detail.jsonl';checkpoint=cache/'povarenok-detail.state.json'
    with requests.Session() as client:
        client.headers['User-Agent']='SmartFridge-recipe-import/1.0'
        meta=request_json(client,HUB+'/api/datasets/'+DATASET)
        revision=meta.get('sha')
        if not isinstance(revision,str) or not revision:raise catalog.CatalogError('Сервис не вернул версию датасета. Без неё безопасное продолжение загрузки невозможно.')
        splits=request_json(client,VIEWER+'/splits',dataset=DATASET).get('splits',[])
        split=next((s for s in splits if s.get('split')=='train'),None)
        if not split:raise catalog.CatalogError('В датасете не найден раздел train. Скачайте JSONL и импортируйте через --file.')
        config=split.get('config','default');state={};offset=0
        try:state=json.loads(checkpoint.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        if state.get('revision')==revision and state.get('config')==config and target.exists():
            offset=int(state.get('offset',0));size=int(state.get('bytes',0))
            if size<0 or target.stat().st_size<size:raise catalog.CatalogError('Кэш загрузки повреждён. Удалите только resources/recipes/downloads и повторите команду.')
            with target.open('r+b') as file:file.truncate(size)
        elif target.exists():
            # Changed upstream revision must never be mixed with cached rows from an older version.
            target.unlink();checkpoint.unlink(missing_ok=True)
        if state.get('done') and offset and state.get('revision')==revision and state.get('config')==config:
            print(f'Используется полностью загруженный кэш: {offset} записей.',flush=True)
            return target,revision
        print(f'Загрузка {DATASET}. Начальная позиция: {offset}.',flush=True)
        total=None
        while total is None or offset<total:
            reply=request_json(client,VIEWER+'/rows',dataset=DATASET,config=config,split='train',offset=offset,length=100)
            records=reply.get('rows');total=reply.get('num_rows_total')
            if not isinstance(records,list) or type(total) is not int or not 0<total<=MAX_RECIPES:raise catalog.CatalogError('Неожиданный ответ датасета. Старый каталог не изменён.')
            if not records:raise catalog.CatalogError('Датасет вернул пустую страницу до конца загрузки. Повторите команду.')
            if len(records)>100 or any(type(r.get('row_idx')) is not int or r['row_idx']!=offset+i or not isinstance(r.get('row'),dict) for i,r in enumerate(records)):
                raise catalog.CatalogError('Порядок строк изменился. Старый каталог не изменён.')
            if offset+len(records)>total:raise catalog.CatalogError('Ответ датасета выходит за заявленный объём. Каталог не изменён.')
            payload=b''.join((json.dumps(r['row'],ensure_ascii=False)+'\n').encode('utf-8') for r in records)
            with target.open('ab') as file:file.write(payload);file.flush();os.fsync(file.fileno());size=file.tell()
            offset+=len(records)
            state=dict(revision=revision,config=config,offset=offset,bytes=size,done=False)
            tmp=checkpoint.with_suffix('.tmp');tmp.write_text(json.dumps(state),encoding='utf-8');os.replace(tmp,checkpoint)
            if offset%1000==0 or offset==total:print(f'Скачано {offset:,} / {total:,}'.replace(',',' '),flush=True)
        end_meta=request_json(client,HUB+'/api/datasets/'+DATASET)
        if end_meta.get('sha')!=revision:raise catalog.CatalogError('Во время загрузки источник обновился. Повторите команду: будет загружена новая версия целиком.')
        state['done']=True;checkpoint.write_text(json.dumps(state),encoding='utf-8')
        return target,revision


def build(rows,destination,*,source='',revision='',merge=False):
    """A failure leaves the existing catalog untouched. No user DB is ever opened."""
    destination=Path(destination).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
    if destination==catalog.ROOT/'db'/'smartfridge.db':raise catalog.CatalogError('Это основная БД аккаунтов. Для рецептов нужен отдельный файл.')
    if destination.exists():
        with sqlite3.connect(destination.as_uri()+'?mode=ro',uri=True) as check:
            tables={row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and 'recipes' not in tables:raise catalog.CatalogError('Этот файл не является каталогом рецептов. Он не будет заменён.')
            if tables & {'users','subscriptions','product_groups'}:raise catalog.CatalogError('Нельзя импортировать рецепты в БД аккаунтов.')
    temp=destination.with_name(destination.name+'.'+str(os.getpid())+'.incoming')
    temp.unlink(missing_ok=True);con=None;report=dict(read=0,accepted=0,skipped=0,duplicates=0,errors=[])
    try:
        if merge and destination.exists():
            with sqlite3.connect(str(destination)) as old,sqlite3.connect(str(temp)) as new:old.backup(new)
        con=catalog.initialize(temp)
        for index,raw in enumerate(rows,1):
            report['read']=index
            if index>MAX_RECIPES:raise catalog.CatalogError('Превышен предел: 1 000 000 записей за импорт.')
            try:recipe=catalog.normalize(raw,row_number=index)
            except catalog.CatalogError as exc:
                report['skipped']+=1
                if len(report['errors'])<10:report['errors'].append(dict(row=index,error=str(exc)))
                continue
            old=con.execute('SELECT id,complete FROM recipes WHERE source_key=?',(recipe['source_key'],)).fetchone()
            if old:
                report['duplicates']+=1
                # An incomplete index cannot overwrite a full recipe.
                if old['complete'] and not recipe['complete']:continue
                rowid=old['id']
                con.execute('UPDATE recipes SET title=?,category=?,cuisine=?,minutes=?,servings=?,complete=?,search_text=?,data=? WHERE id=?',
                    (recipe['title'],recipe['category'],recipe['cuisine'],recipe['minutes'],recipe['servings'],int(recipe['complete']),recipe['search_text'],json.dumps(recipe,ensure_ascii=False),rowid))
            else:
                cursor=con.execute('INSERT INTO recipes(source_key,public_id,title,category,cuisine,minutes,servings,complete,search_text,data) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (recipe['source_key'],recipe['key'],recipe['title'],recipe['category'],recipe['cuisine'],recipe['minutes'],recipe['servings'],int(recipe['complete']),recipe['search_text'],json.dumps(recipe,ensure_ascii=False)))
                rowid=cursor.lastrowid
            if con.execute("SELECT value FROM metadata WHERE key='fts'").fetchone()[0]=='1':
                con.execute('DELETE FROM recipes_fts WHERE rowid=?',(rowid,));con.execute('INSERT INTO recipes_fts(rowid,search_text) VALUES(?,?)',(rowid,recipe['search_text']))
            report['accepted']+=1
            if index%1000==0:con.commit();print(f'Обработано {index:,} записей'.replace(',',' '),flush=True)
        if not report['accepted'] and not report['duplicates']:raise catalog.CatalogError('Подходящих русскоязычных рецептов не найдено. Каталог не изменён.')
        stats=catalog.info(con);report.update(count=stats['count'],complete=stats['complete'],incomplete=stats['incomplete'])
        for key,value in dict(source=source,revision=revision,loaded_at=datetime.now(timezone.utc).isoformat(),report=json.dumps(report,ensure_ascii=False)).items():
            con.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)',(key,value))
        con.commit();con.execute('PRAGMA optimize');con.close();con=None
        for retry in range(10):
            try:os.replace(temp,destination);break
            except PermissionError:
                if retry==9:raise catalog.CatalogError('Файл каталога занят. Закройте сайт и повторите импорт.')
                time.sleep(.5)
        return report
    finally:
        if con:con.close()
        temp.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description='Загрузка русскоязычной базы рецептов. Основная БД аккаунтов не изменяется.')
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--download',action='store_true',help='Скачать подробную базу Поварёнка с Hugging Face, продолжая прерванную загрузку')
    source.add_argument('--file',type=Path,help='Импорт JSONL/JSON/CSV, также .gz')
    parser.add_argument('--merge',action='store_true',help='Дополнить каталог вместо замены, обновляя совпадающие ссылки')
    parser.add_argument('--db',type=Path,help='Файл каталога (по умолчанию db/recipes.db)')
    args=parser.parse_args()
    # Load the same local .env as the website without importing Flask or starting payment workers.
    from dotenv import load_dotenv
    load_dotenv(catalog.ROOT/'.env')
    destination=args.db or catalog.catalog_path()
    try:
        with lock(destination):
            if args.download:path,revision=download(catalog.ROOT/'resources'/'recipes'/'downloads');source_name=catalog.DEFAULT_SOURCE
            else:path=args.file;revision='';source_name='Локальная выгрузка: '+path.name
            report=build(local_rows(path),destination,source=source_name,revision=revision,merge=args.merge)
        print(f"Готово. В каталоге: {report['count']}. Со шагами: {report['complete']}. Без шагов: {report['incomplete']}. Пропущено: {report['skipped']}.",flush=True)
        if not report['complete']:print('В выгрузке нет шагов приготовления. Записи доступны через фильтр «Без шагов», но не выдаются за полные рецепты.')
        print('Откройте раздел «Рецепты» на сайте. Перезапуск сайта не нужен.')
    except (catalog.CatalogError,OSError,ValueError,csv.Error,sqlite3.Error) as exc:
        print('Ошибка импорта: '+str(exc),file=sys.stderr);return 1
    except KeyboardInterrupt:print('Импорт прерван. Каталог не заменён. Повтор команды продолжит скачивание.',file=sys.stderr);return 130
    return 0

if __name__=='__main__':raise SystemExit(main())
