"""One shopping workflow over personal and existing family rows; exact stock transfers."""
import re
import secrets
from decimal import Decimal
from urllib.parse import urlsplit
from sqlalchemy import select,func,text
from data import db_session
from data.shopping import ShoppingItem,ShoppingMutation,ShoppingStore
from data.family import FamilyShoppingItem
from data.activity import ActivityEvent
from data.storage import StorageLocation
from services import product_service as stock,product_operations as ops,family_service as family
from services.billing_service import active

FIELDS=('name','note','quantity','unit','brand','category','expected_price','store','priority')
CATEGORIES=stock.CATEGORIES


class ShoppingError(ValueError):
    def __init__(self,message,status=422):super().__init__(message);self.status=status


def ident(raw):
    try:
        result=int(raw)
        if not 0<result<=9223372036854775807:raise ValueError
        return result
    except (ValueError,TypeError):raise ShoppingError('Запись не найдена.',404)


def scope(ses,uid,mode):
    if mode=='personal':
        if not active(family.access_subscription(uid,ses)):raise ShoppingError('Для покупок нужна активная подписка.',403)
        return ShoppingItem,ShoppingItem.owner_id==uid,'p:'+str(uid),None
    if mode!='family':raise ShoppingError('Выберите личный или семейный список.',400)
    try:
        group=family.require_family(ses,uid)
        if not family.entitled(ses,group,uid):raise family.FamilyError('Недостаточно оплаченных мест для семейного доступа.')
    except family.FamilyError as exc:raise ShoppingError(str(exc),403)
    return FamilyShoppingItem,FamilyShoppingItem.family_id==group.id,'f:'+str(group.id),group


def signature(item):
    return ops.digest(dict(id=item.id,key=item.submission_key,revision=item.revision,created=item.created_at))


def get(ses,uid,mode,item_id):
    model,condition,_,_=scope(ses,uid,mode)
    item=ses.scalar(select(model).where(condition,model.id==ident(item_id)))
    if not item:raise ShoppingError('Запись больше не существует.',404)
    return item


def values(form):
    data={k:str(form.get(k,'')).strip() for k in FIELDS}
    for key,limit in [('name',120),('brand',120),('store',120),('note',160)]:
        data[key]=stock.clean(data[key])
        if len(data[key])>limit or any(ord(c)<32 for c in data[key]):raise ShoppingError('Поле «'+{'name':'Продукт','brand':'Марка','store':'Магазин','note':'Заметка'}[key]+'»: до '+str(limit)+' символов.')
    if not data['name']:raise ShoppingError('Укажите название продукта.')
    data['category']=data['category'] or 'other'
    if data['category'] not in CATEGORIES:raise ShoppingError('Выберите категорию.')
    errors={};q=stock.number(data['quantity'],'Количество',errors,'quantity',Decimal('1000000'))
    if errors or q is not None and q<=0:raise ShoppingError('Количество должно быть больше нуля, до 6 знаков после запятой.')
    if q is not None:
        if data['unit'] not in stock.UNITS:raise ShoppingError('Выберите единицу измерения.')
        if data['unit'] in ('pcs','pack') and q!=q.to_integral_value():raise ShoppingError('Штуки и упаковки указываются целым числом.')
        data['quantity']=format(q.normalize(),'f')
    else:data['quantity']='';data['unit']=''
    price=stock.number(data['expected_price'],'Стоимость',errors,'expected_price',Decimal('10000000'))
    if errors or price is not None and price.as_tuple().exponent<-2:raise ShoppingError('Стоимость: до двух знаков после запятой.')
    data['expected_price']=int(price*100) if price is not None else None
    data['priority']=data['priority'] in ('yes','on','true','1')
    return data


def form_values(item):
    return {k:('yes' if item.priority else '') if k=='priority' else
        (format(Decimal(item.expected_price)/100,'f') if item.expected_price is not None else '') if k=='expected_price' else getattr(item,k) for k in FIELDS}


def audit(ses,uid,group,action,name):
    label={'add':'Добавлено в покупки','edit':'Изменена покупка','toggle':'Изменена отметка покупки','delete':'Удалено из покупок',
        'clear':'Очищены купленные товары','repeat':'Добавлено в покупки повторно','stock':'Покупка перенесена в запасы','recipe':'Добавлены ингредиенты из рецепта'}[action]
    if group:
        from data.family import FamilyEvent
        ses.add(FamilyEvent(family_id=group.id,actor_id=uid,description=(label+': '+name)[:300]))
    ses.add(ActivityEvent(owner_id=uid,category='shopping',action='shopping_'+action,after={'name':name,'scope':'family' if group else 'personal'}))


def add_in_session(ses,uid,mode,data,*,source=None):
    model,condition,_,group=scope(ses,uid,mode)
    # Merge only fully compatible rows without a price; do not invent a unit price or brand match.
    candidates=list(ses.scalars(select(model).where(condition,model.bought.is_(False),model.stocked.is_(False))))
    candidate=None
    if data['expected_price'] is None:
        for item in candidates:
            if item.expected_price is not None:continue
            if any(str(getattr(item,k)).casefold()!=str(data[k]).casefold() for k in ('name','note','brand','category','store','priority')):continue
            if bool(item.quantity)!=bool(data['quantity']):continue
            if not item.quantity:
                candidate=item;break
            if stock.UNITS[item.unit][1]!=stock.UNITS[data['unit']][1]:continue
            combined=Decimal(item.quantity)+Decimal(data['quantity'])*Decimal(stock.UNITS[data['unit']][2])/Decimal(stock.UNITS[item.unit][2])
            if combined<=1000000 and combined.normalize().as_tuple().exponent>=-6:
                candidate=item;item.quantity=format(combined.normalize(),'f');item.revision+=1;break
    if candidate:return candidate,False
    if ses.scalar(select(func.count()).select_from(model).where(condition))>=500:raise ShoppingError('В списке уже 500 записей. Удалите ненужные.')
    owner={'owner_id':uid} if mode=='personal' else {'family_id':group.id}
    item=model(**owner,**data,source=source or {},submission_key=secrets.token_hex(24),created_by=uid)
    ses.add(item);ses.flush();return item,True


def list_state(ses,uid,mode):
    model,condition,_,_=scope(ses,uid,mode)
    rows=ses.execute(select(model.id,model.revision,model.submission_key,model.bought,model.stocked,model.expected_price).where(condition).order_by(model.id)).all()
    queued=[i for i in rows if not i.bought]
    return dict(version=ops.digest([(i.id,i.revision,i.submission_key) for i in rows]),
        stats=dict(total=len(rows),pending=len(queued),bought=sum(i.bought and not i.stocked for i in rows),stocked=sum(i.stocked for i in rows),
            budget=sum(i.expected_price or 0 for i in queued),unknown=sum(i.expected_price is None for i in queued)))


def mutate(uid,mode,action,key,params):
    if not re.fullmatch('[a-f0-9]{48}',str(key)):raise ShoppingError('Форма устарела. Откройте её заново.',400)
    fingerprint=ops.digest({'actor':uid,'action':action,'params':params})
    with db_session.create_session() as ses:
        ses.execute(text('BEGIN IMMEDIATE'));model,condition,scope_key,group=scope(ses,uid,mode)
        receipt=ses.scalar(select(ShoppingMutation).where(ShoppingMutation.scope==scope_key,ShoppingMutation.submission_key==key))
        if receipt:
            if receipt.payload_hash!=fingerprint:raise ShoppingError('Эта форма уже использована для другого действия.',409)
            return receipt.result
        result={}
        before_version=list_state(ses,uid,mode)['version'] if action=='toggle' else None
        if action=='add':
            data=values(params);item,created=add_in_session(ses,uid,mode,data);result={'item_id':item.id,'created':created}
            audit(ses,uid,group,action,item.name)
        elif action=='clear':
            if params.get('confirm')!='yes':raise ShoppingError('Подтвердите удаление купленных записей.')
            shown=params.get('shown',[])
            if not isinstance(shown,list) or len(shown)>500:raise ShoppingError('Обновите список.')
            removed=0
            for row in shown:
                if not isinstance(row,dict):raise ShoppingError('Обновите список.')
                item=ses.scalar(select(model).where(condition,model.id==ident(row.get('id'))))
                if item and item.bought and signature(item)==row.get('expected'):
                    ses.delete(item);removed+=1
            result={'removed':removed}
            if removed:audit(ses,uid,group,action,str(removed))
        elif action=='recipe':
            # Recompute against current stock in the same write transaction, never trust submitted names/amounts.
            preview=recipe_preview(uid,mode,params.get('recipe_key',''),params.get('servings',''),ses=ses)
            if params.get('preview_signature')!=preview['signature']:raise ShoppingError('Запасы или рецепт изменились. Обновите подбор перед добавлением.',409)
            chosen=params.get('selected',[])
            if not isinstance(chosen,list) or not chosen or len(chosen)>100:raise ShoppingError('Выберите ингредиенты.')
            if len(set(chosen))!=len(chosen):raise ShoppingError('Ингредиент выбран несколько раз.')
            picks={r['key']:r for r in preview['rows']};added=0
            for pick in chosen:
                if pick not in picks:raise ShoppingError('Список ингредиентов изменился. Обновите страницу.')
                row=picks[pick];data=values(row['values']);add_in_session(ses,uid,mode,data,source={'recipe':preview['recipe']['title'],'key':preview['recipe']['key']});added+=1
            result={'added':added};audit(ses,uid,group,action,preview['recipe']['title'])
        else:
            item=get(ses,uid,mode,params.get('item_id'))
            if signature(item)!=params.get('expected'):raise ShoppingError('Запись изменилась в другой вкладке. Обновите страницу перед действием.',409)
            name=item.name
            if action=='edit':
                if item.stocked:raise ShoppingError('Покупка уже в запасах. Для следующей покупки нажмите «Купить ещё».',409)
                data=values(params)
                for k,v in data.items():setattr(item,k,v)
                item.revision+=1
            elif action=='toggle':
                if item.stocked:raise ShoppingError('Покупка уже перенесена в запасы. Используйте «Купить ещё».',409)
                item.bought=not item.bought;item.revision+=1
            elif action=='delete':
                if params.get('confirm')!='yes':raise ShoppingError('Подтвердите удаление записи.')
                ses.delete(item)
            elif action=='repeat':
                data=values(form_values(item));new,_=add_in_session(ses,uid,mode,data,source=item.source);result['item_id']=new.id
            elif action=='stock':
                if not item.bought:raise ShoppingError('Сначала отметьте покупку купленной.')
                if item.stocked:raise ShoppingError('Эта покупка уже добавлена в запасы.',409)
                storage_id=ident(params.get('storage_id'));place=ses.get(StorageLocation,storage_id)
                if not place:raise ShoppingError('Место хранения удалено.',409)
                if place.owner_id!=uid:
                    try:family.storage_access(ses,uid,place.id,write=True)
                    except family.FamilyError as exc:raise ShoppingError(str(exc),403)
                product,parsed,errors=stock.validate(params)
                if errors:raise ShoppingError(' '.join(errors.values()))
                product_key=ops.digest({'shopping_scope':scope_key,'item':item.submission_key,'transfer':key})[:48]
                group_id,created=stock.save_in_session(ses,place.owner_id,place.id,product_key,product,parsed)
                item.stocked=True;item.revision+=1;result={'storage_id':place.id,'owner_id':place.owner_id,'group_id':group_id}
                shared_group=family.family_for(ses,uid)
                from data.family import FamilyStorage
                shared=ses.get(FamilyStorage,place.id)
                if created and shared_group and shared and shared.family_id==shared_group.id:
                    family.event(ses,shared_group,uid,'Покупка добавлена в '+place.name+': '+product['name'],storage_id=place.id)
            else:raise ShoppingError('Неизвестное действие.',400)
            audit(ses,uid,group,action,name)
        if action=='toggle':
            ses.flush();state=list_state(ses,uid,mode)
            result.update(item_id=item.id,name=item.name,bought=item.bought,expected=signature(item),before_version=before_version,**state)
        ses.add(ShoppingMutation(scope=scope_key,submission_key=key,payload_hash=fingerprint,result=result));ses.commit();return result


def stock_places(ses,uid):
    personal=list(ses.scalars(select(StorageLocation).where(StorageLocation.owner_id==uid).order_by(StorageLocation.name_key)))
    shared=[p for _,p,_ in family.accessible_places(ses,uid,write=True) if p.owner_id!=uid]
    from data.product import ProductPreferences
    pref=ses.get(ProductPreferences,uid)
    return sorted(personal+shared,key=lambda place:(place.id!=(pref.default_storage_id if pref else None),place.name_key,place.id))


def recipe_preview(uid,mode,key,servings='',*,ses=None):
    from services import recipe_library as library,recipe_matching as matching
    if not re.fullmatch('[a-f0-9]{24}',str(key)):raise ShoppingError('Рецепт не найден.',404)
    recipe=library.recipe(uid,key,servings or None)
    if not recipe:raise ShoppingError('Рецепт не найден.',404)
    if recipe.get('portion_error'):raise ShoppingError(recipe['portion_error'])
    snapshot=matching.stock_snapshot(uid,{'shared_only':'1'} if mode=='family' else {})
    assessment=matching.recipe_assessment(recipe,snapshot);compiled=[matching.compile_ingredient(i) for i in recipe['ingredients']]
    rows=[]
    for index,(row,ing) in enumerate(zip(assessment['rows'],compiled)):
        if row['status'] in ('enough','pantry'):continue
        amount='';unit='';note=''
        if row['status']=='short' and row['shortage']:
            match=re.fullmatch(r'([\d.,]+) (г|кг|мл|л|шт\.|уп\.)',row['shortage'])
            if match:amount=match[1].replace(',','.');unit={'г':'g','кг':'kg','мл':'ml','л':'l','шт.':'pcs','уп.':'pack'}[match[2]]
        elif row['status']=='missing' and ing['amount'] and ing['measure']:
            ratio=Decimal(recipe['chosen_servings'])/Decimal(recipe['servings']) if recipe['servings'] and recipe['chosen_servings'] else Decimal(1)
            amount=format((Decimal(ing['amount'])*Decimal(ing['measure'][1])*ratio).normalize(),'f')
            unit={'mass':'g','volume':'ml','pieces':'pcs','packs':'pack'}.get(ing['measure'][0],'')
        if not amount or not unit:
            amount='';unit='';note=('Уточнить количество. '+row['original'])[:160]
        if row['optional']:note=('По желанию. '+note)[:160]
        data=dict(name=row['name'][:120],quantity=amount,unit=unit,note=note,category='other')
        try:values(data)
        except ShoppingError:
            data.update(quantity='',unit='',note=('Уточнить количество. '+row['original'])[:160])
        rows.append(dict(key=str(index),name=row['name'],status=row['label'],optional=row['optional'],values=data,original=row['original']))
    signature=ops.digest({'recipe':recipe['key'],'servings':recipe['chosen_servings'],'rows':rows})
    return {'recipe':recipe,'rows':rows,'signature':signature}


def store_url(raw):
    raw=raw.strip()
    try:parts=urlsplit(raw);host=parts.hostname;port=parts.port
    except ValueError:raise ShoppingError('Введите адрес сайта магазина.')
    if len(raw)>600 or parts.scheme!='https' or not host or parts.username or parts.password or any(ord(c)<33 for c in raw) or '\\' in raw:
        raise ShoppingError('Укажите полный адрес HTTPS без логина и пароля.')
    return raw
