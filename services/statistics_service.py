"""Read-only inventory/flow reporting. Costs are recorded values, never estimated savings."""
import math
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal,ROUND_HALF_UP
from collections import defaultdict,deque
from sqlalchemy import select,func,or_,and_,text
from data import db_session
from data.product import ProductGroup,ProductBatch,ProductMutation
from data.storage import StorageLocation
from services import product_service as stock,family_service as family

MSK=timezone(timedelta(hours=3))
ACTIONS={'add':'Добавлено','import':'Импортировано','use':'Использовано','waste':'Списано','edit':'Исправлено','move':'Перемещено','delete':'Удалено'}
DIMENSIONS={'mass':'Масса','volume':'Объём','pieces':'Штуки','packs':'Упаковки'}
REASONS={'expired':'Истёк срок','spoiled':'Испорчено','other':'Другая причина','unknown':'Причина не указана'}
PRESETS={'7':'7 дней','30':'30 дней','90':'90 дней','month':'Этот месяц','custom':'Свой период'}
LIMIT=30000


class ReportError(ValueError):
    def __init__(self,message,status=400):super().__init__(message);self.status=status


def filters(args):
    today=stock.today();preset=args.get('period','30');scope=args.get('scope','personal');mode=args.get('chart','count')
    if preset not in PRESETS:raise ReportError('Выберите период из списка.')
    if scope not in ('personal','family'):raise ReportError('Выберите личные или общие места.')
    if mode not in ('count','money'):raise ReportError('Выберите показатель графика.')
    end=today
    if preset=='custom':
        try:start=date.fromisoformat(args.get('start',''));end=date.fromisoformat(args.get('end',''))
        except (ValueError,TypeError):raise ReportError('Укажите начало и конец периода.')
        if not date(2000,1,1)<=start<=end<=today:raise ReportError('Начало должно быть не позже конца; будущие даты недоступны.')
    elif preset=='month':start=today.replace(day=1)
    else:start=today-timedelta(days=int(preset)-1)
    days=(end-start).days+1
    if days>366:raise ReportError('За один раз можно показать не более 366 дней.')
    previous_start=start-timedelta(days=days)
    category=args.get('category','')
    if category and category not in stock.CATEGORIES:raise ReportError('Выберите категорию из списка.')
    location=args.get('storage','')
    if location:
        try:
            location=int(location)
            if not 0<location<=9223372036854775807:raise ValueError
        except (ValueError,TypeError):raise ReportError('Место хранения не найдено.',404)
    return dict(period=preset,scope=scope,chart=mode,start=start,end=end,previous_start=previous_start,
        previous_end=start-timedelta(days=1),days=days,category=category,storage=location or '',q=stock.clean(args.get('q',''))[:120])


def timestamp(day):return datetime.combine(day,time.min,tzinfo=MSK).timestamp()


def locations(ses,uid,f):
    if f['scope']=='family':
        try:
            group=family.require_family(ses,uid)
            if not family.entitled(ses,group,uid):raise family.FamilyError('Недостаточно мест в семейном тарифе.')
        except family.FamilyError as exc:raise ReportError(str(exc),403)
        places=[p for _,p,_ in family.accessible_places(ses,uid)]
    else:places=list(ses.scalars(select(StorageLocation).where(StorageLocation.owner_id==uid).order_by(StorageLocation.name_key)))
    if f['storage'] and f['storage'] not in {p.id for p in places}:
        # An owner may still inspect their own historical/deleted location, never another user's.
        if f['scope']!='personal':raise ReportError('Доступ к месту хранения закрыт.',404)
        existing=ses.get(StorageLocation,f['storage'])
        if existing and existing.owner_id!=uid:raise ReportError('Место хранения недоступно.',404)
        known=ses.scalar(select(ProductMutation.id).where(ProductMutation.owner_id==uid,or_(
            ProductMutation.before['storage_id'].as_integer()==f['storage'],ProductMutation.after['storage_id'].as_integer()==f['storage'])).limit(1))
        if not known:raise ReportError('Место хранения не найдено.',404)
    return places


def event_condition(uid,f,places):
    p=ProductMutation
    if f['scope']=='personal':
        result=p.owner_id==uid
        if f['storage']:result=and_(result,or_(p.before['storage_id'].as_integer()==f['storage'],p.after['storage_id'].as_integer()==f['storage']))
        return result
    allowed=[place for place in places if not f['storage'] or place.id==f['storage']]
    # Creation time protects against SQLite ID reuse after a location was deleted.
    by_owner=defaultdict(list)
    for place in allowed:by_owner[place.owner_id].append(place)
    return or_(*[and_(p.owner_id==owner,or_(*[and_(p.created_at>=place.created_at,or_(
        p.before['storage_id'].as_integer()==place.id,p.after['storage_id'].as_integer()==place.id)) for place in group]))
        for owner,group in by_owner.items()]) if allowed else p.id<0


def integer(value):return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None


def event_row(event,uid,f,places):
    before=event.before if isinstance(event.before,dict) else {};after=event.after if isinstance(event.after,dict) else {};allowed={p.id:p for p in places}
    current=after or before
    if f['scope']=='family':
        permitted=lambda x:x.get('storage_id') in allowed and allowed[x['storage_id']].owner_id==event.owner_id and event.created_at>=allowed[x['storage_id']].created_at
        if permitted(after):current=after
        elif permitted(before):current=before
        else:return None
    if f['storage']:
        if before.get('storage_id')==f['storage']:current=before or after
        elif after.get('storage_id')==f['storage']:current=after
    details=current.get('details');details=details if isinstance(details,dict) else {};category=details.get('category','other')
    if category not in stock.CATEGORIES:category='other'
    if f['category'] and category!=f['category']:return None
    name=str(current.get('name') or before.get('name') or 'Продукт')[:120];brand=str(current.get('brand',''))[:120]
    if f['q'] and f['q'].casefold() not in (name+' '+brand).casefold():return None
    dimension=current.get('dimension') or before.get('dimension');dimension=dimension if dimension in DIMENSIONS else None
    if event.action in ('use','waste','move'):
        quantity=integer(after.get('operation_quantity_micro'))
        if quantity is None and event.action!='move':
            left=integer(before.get('quantity_micro'));right=integer(after.get('quantity_micro'))
            quantity=left-right if left is not None and right is not None and left>=right else None
        cost=integer(after.get('operation_cost_kopecks'))
        if 'operation_cost_kopecks' not in after and quantity is not None:
            left=integer(before.get('quantity_micro'));price=integer(before.get('price_kopecks'))
            cost=int((Decimal(price)*quantity/left).quantize(Decimal(1),rounding=ROUND_HALF_UP)) if left and price is not None else None
    else:quantity=integer(current.get('quantity_micro'));cost=integer(current.get('price_kopecks'))
    local=datetime.fromtimestamp(event.created_at,MSK);expiry=None
    try:expiry=date.fromisoformat(str(before.get('expiry_date','')))
    except ValueError:pass
    timely=event.action=='use' and expiry is not None and 0<=(expiry-local.date()).days<=3
    source=before.get('storage_name','');target=after.get('storage_name','')
    if f['scope']=='family':
        source=source if permitted(before) else ''
        target=target if permitted(after) else ''
    return dict(id=event.id,action=event.action,label=ACTIONS.get(event.action,event.action),day=local.date(),stamp=local.strftime('%d.%m.%Y %H:%M'),
        name=name,brand=brand,category=category,quantity=quantity,dimension=dimension,cost=cost,timely=timely,
        place=str(current.get('storage_name',''))[:60],source=source,target=target,reason=after.get('reason') if after.get('reason') in REASONS else 'unknown')


def metric():return dict(count=0,cost=0,unknown=0,quantities={key:0 for key in DIMENSIONS},unknown_quantity=0)


def accumulate(target,row):
    target['count']+=1
    if row['cost'] is None:target['unknown']+=1
    else:target['cost']+=row['cost']
    if row['quantity'] is None or row['dimension'] is None:target['unknown_quantity']+=1
    else:target['quantities'][row['dimension']]+=row['quantity']


def cash(target):
    return None if target['count'] and target['unknown']==target['count'] else target['cost']


def report(uid,f,*,tab='inventory',page=1,export=False):
    with db_session.create_session() as ses:
        # One read snapshot for permissions, current inventory and the event stream.
        ses.execute(text('BEGIN'))
        places=locations(ses,uid,f);chosen=[p for p in places if not f['storage'] or p.id==f['storage']]
        place_ids=[p.id for p in chosen]
        inventory_query=select(ProductGroup,ProductBatch,StorageLocation).join(ProductBatch,ProductBatch.group_id==ProductGroup.id).join(StorageLocation,StorageLocation.id==ProductGroup.storage_id).where(
            ProductGroup.storage_id.in_(place_ids),ProductGroup.owner_id==StorageLocation.owner_id,ProductBatch.owner_id==ProductGroup.owner_id,ProductBatch.quantity_micro>0)
        if f['category']:inventory_query=inventory_query.where(ProductGroup.details['category'].as_string()==f['category']) if f['category']!='other' else inventory_query.where(or_(ProductGroup.details['category'].as_string().is_(None),ProductGroup.details['category'].as_string().not_in([key for key in stock.CATEGORIES if key!='other'])))
        if f['q']:inventory_query=inventory_query.where(ProductGroup.search_key.contains(f['q'].casefold(),autoescape=True))
        size=ses.scalar(select(func.count()).select_from(inventory_query.subquery()))
        if size>LIMIT:raise ReportError('Слишком много поступлений. Выберите место хранения или уточните поиск.',413)
        if export and tab=='inventory' and size>5000:raise ReportError('В экспорт попадает больше 5000 поступлений. Уточните место или поиск.',413)
        inventory=metric();inventory['groups']=0;inventory['locations']=0;groups=set();occupied=set()
        expiry={key:metric() for key in ('expired','today','soon','fresh','unknown')};categories=defaultdict(metric)
        current_rows=[];risk=[];today=stock.today()
        try:page=max(1,int(page))
        except (ValueError,TypeError):page=1
        inventory_pages=max(1,(size+24)//25);inventory_page=min(page,inventory_pages)
        for index,(group,batch,place) in enumerate(ses.execute(inventory_query.order_by(ProductBatch.expiry_date.is_(None),ProductBatch.expiry_date,ProductGroup.name,ProductBatch.id)).yield_per(500)):
            details=group.details if isinstance(group.details,dict) else {}
            category=details.get('category','other');category=category if category in stock.CATEGORIES else 'other'
            row=dict(id=batch.id,group_id=group.id,owner_id=group.owner_id,name=group.name,brand=group.brand,place=place.name,storage_id=place.id,
                category=category,quantity=integer(batch.quantity_micro),dimension=group.dimension if group.dimension in DIMENSIONS else None,cost=integer(batch.price_kopecks),date=batch.expiry_date)
            if batch.expiry_date is None:state='unknown'
            else:
                days=(batch.expiry_date-today).days
                state='expired' if days<0 else 'today' if days==0 else 'soon' if days<=3 else 'fresh'
            row['state']=state;accumulate(inventory,row);accumulate(expiry[state],row);accumulate(categories[category],row)
            groups.add(group.id);occupied.add(place.id)
            if state in ('today','soon') and len(risk)<10:risk.append(row)
            if export and tab=='inventory' or tab=='inventory' and (inventory_page-1)*25<=index<inventory_page*25:current_rows.append(row)
        inventory['groups']=len(groups);inventory['locations']=len(occupied)
        if export and tab=='inventory':return {'inventory_rows':current_rows}
        current={key:metric() for key in ('add','use','waste','timely')};previous={key:metric() for key in current}
        reasons={key:metric() for key in REASONS};product_totals={};corrections=defaultdict(int)
        bucket_days=max(1,math.ceil(f['days']/7));buckets=[]
        day=f['start']
        while day<=f['end']:
            last=min(f['end'],day+timedelta(days=bucket_days-1));buckets.append(dict(start=day,end=last,add=metric(),use=metric(),waste=metric(),timely=metric()));day=last+timedelta(days=1)
        query=select(ProductMutation).where(event_condition(uid,f,places),ProductMutation.created_at>=timestamp(f['previous_start']),ProductMutation.created_at<timestamp(f['end']+timedelta(days=1)))
        if f['q']:
            needle=f['q'].casefold()
            names=[]
            for column in (ProductMutation.before,ProductMutation.after):
                names.append(func.unicode_casefold(func.coalesce(column['name'].as_string(),'')+' '+func.coalesce(column['brand'].as_string(),'')).contains(needle,autoescape=True))
            query=query.where(or_(*names))
        if f['category']:
            checks=[]
            for column in (ProductMutation.before,ProductMutation.after):
                category_value=column['details']['category'].as_string()
                checks.append(or_(category_value.is_(None),category_value.not_in([key for key in stock.CATEGORIES if key!='other'])) if f['category']=='other' else category_value==f['category'])
            query=query.where(or_(*checks))
        count=ses.scalar(select(func.count()).select_from(query.subquery()))
        if count>LIMIT:raise ReportError('За период слишком много операций. Сократите период или выберите место хранения.',413)
        event_rows=[];event_count=0;export_rows=[];tail=deque(maxlen=25)
        for event in ses.scalars(query.order_by(ProductMutation.created_at.desc(),ProductMutation.id.desc())).yield_per(500):
            row=event_row(event,uid,f,places)
            if row is None:continue
            selected=row['day']>=f['start'];period=current if selected else previous
            key='add' if row['action'] in ('add','import') else row['action']
            if key in period:accumulate(period[key],row)
            if row['timely']:accumulate(period['timely'],row)
            if not selected:continue
            if key not in ('add','use','waste'):corrections[key]+=1
            else:
                bucket=buckets[min((row['day']-f['start']).days//bucket_days,len(buckets)-1)];accumulate(bucket[key],row)
                if row['timely']:accumulate(bucket['timely'],row)
            if key=='waste':accumulate(reasons[row['reason']],row)
            if key in ('use','waste'):
                product_key=(row['name'].casefold(),row['brand'].casefold(),row['dimension'])
                if product_key not in product_totals:product_totals[product_key]=dict(name=row['name'],brand=row['brand'],dimension=row['dimension'],use=metric(),waste=metric())
                accumulate(product_totals[product_key][key],row)
            wanted=key in ('use','waste') if tab=='inventory' else key==tab if tab in ('use','waste') else row['timely'] if tab=='savings' else False
            if wanted:
                tail.append(row)
                if export:
                    if len(export_rows)>=5000:raise ReportError('В экспорт попадает больше 5000 операций. Сузьте период.',413)
                    export_rows.append(row)
                if (page-1)*25<=event_count<page*25:event_rows.append(row)
                event_count+=1
        event_pages=max(1,(event_count+24)//25)
        if tab!='inventory' and page>event_pages:
            page=event_pages
            event_rows=list(tail)[-(event_count%25 or 25):] if event_count else []
        series=('use','waste') if tab=='inventory' else ('use',) if tab=='use' else ('timely',) if tab=='savings' else ('waste',)
        maximum=max((b[key]['cost'] if f['chart']=='money' else b[key]['count'] for b in buckets for key in series),default=0)
        for b in buckets:
            for key in series:b[key]['height']=round(100*(b[key]['cost'] if f['chart']=='money' else b[key]['count'])/maximum,2) if maximum else 0
        risk_total=metric()
        for key in ('today','soon'):
            for field in ('count','cost','unknown','unknown_quantity'):risk_total[field]+=expiry[key][field]
            for dimension in DIMENSIONS:risk_total['quantities'][dimension]+=expiry[key]['quantities'][dimension]
        ratios=[]
        for dimension in DIMENSIONS:
            used=current['use']['quantities'][dimension];lost=current['waste']['quantities'][dimension]
            if used+lost:ratios.append(dict(dimension=dimension,used=used,waste=lost,percent=round(100*lost/(used+lost),1)))
        ranking=sorted([row for row in product_totals.values() if row['waste' if tab=='waste' else 'use']['count']>0],key=lambda x:(-(x['waste' if tab=='waste' else 'use']['count']),x['name']))[:10]
        return dict(filters=f,places=places,inventory=inventory,expiry=expiry,categories=dict(categories),current=current,previous=previous,reasons=reasons,
            inventory_rows=current_rows,inventory_page=inventory_page,inventory_pages=inventory_pages,event_rows=event_rows,event_count=event_count,event_page=page,event_pages=event_pages,
            risk=risk,risk_total=risk_total,ratios=ratios,ranking=ranking,buckets=buckets,series=series,maximum=maximum,corrections=dict(corrections),exports=export_rows,today=today)


def return_path(raw):
    """Only an internal statistics URL can replace a product page's Back destination."""
    if not raw or len(raw)>2000:return None
    from urllib.parse import urlsplit,unquote
    try:decoded=unquote(raw);parts=urlsplit(decoded)
    except ValueError:return None
    if parts.scheme or parts.netloc or '\\' in decoded or any(ord(c)<32 for c in decoded):return None
    if parts.path not in ('/home/statistics','/home/statistics/inventory','/home/statistics/usage','/home/statistics/waste','/home/statistics/savings'):return None
    return raw
