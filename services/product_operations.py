"""Transactional stock edits, exact consumption/moves and expiry notifications."""
import hashlib
import json
import re
import secrets
from decimal import Decimal,ROUND_HALF_UP
from datetime import timedelta
from sqlalchemy import select,text,func
from data import db_session
from data.product import ProductGroup,ProductBatch,ProductMutation,ProductNotificationRead
from data.storage import StorageLocation
from services import product_service as service

ACTIONS={'edit':'Редактировать поступление','use':'Использовать','waste':'Списать','move':'Переместить','delete':'Удалить поступление'}


class OperationError(Exception):
    def __init__(self,message,status=422,field='form'):
        super().__init__(message);self.status=status;self.field=field


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def owned(ses,owner,batch_id):
    if not 0<batch_id<=9223372036854775807:raise OperationError('Поступление не найдено.',404)
    batch=ses.scalar(select(ProductBatch).where(ProductBatch.id==batch_id,ProductBatch.owner_id==owner))
    group=ses.scalar(select(ProductGroup).where(ProductGroup.id==batch.group_id,ProductGroup.owner_id==owner)) if batch else None
    place=ses.scalar(select(StorageLocation).where(StorageLocation.id==group.storage_id,StorageLocation.owner_id==owner)) if group else None
    if not batch or not group or not place:raise OperationError('Поступление не найдено.',404)
    return batch,group,place


def snapshot(batch,group,place):
    return dict(batch_id=batch.id,group_id=group.id,group_created_at=group.created_at,storage_id=place.id,storage_name=place.name,
                name=group.name,brand=group.brand,manufacturer=group.manufacturer,dimension=group.dimension,
                details=group.details,quantity_micro=batch.quantity_micro,entered_quantity=batch.entered_quantity,
                unit=batch.unit,expiry_date=str(batch.expiry_date) if batch.expiry_date else '',
                manufactured_date=str(batch.manufactured_date) if batch.manufactured_date else '',
                shelf_value=batch.shelf_value,shelf_unit=batch.shelf_unit,opened_date=str(batch.opened_date) if batch.opened_date else '',
                price_kopecks=batch.price_kopecks,note=batch.note,added_at=batch.added_at)


def values_for(batch,group):
    values={key:'' for key in service.FIELDS}
    values.update({key:str(val) for key,val in group.details.items() if key in values})
    values.update(name=group.name,brand=group.brand,manufacturer=group.manufacturer,
                  quantity=batch.entered_quantity,unit=batch.unit,note=batch.note,
                  price=format(Decimal(batch.price_kopecks)/100,'f') if batch.price_kopecks is not None else '',
                  shelf_value=str(batch.shelf_value or ''),shelf_unit=batch.shelf_unit or 'days')
    for key in ['expiry_date','manufactured_date','opened_date']:values[key]=getattr(batch,key).isoformat() if getattr(batch,key) else ''
    return values


def update_amount(batch,micro,unit=None):
    unit=unit or batch.unit
    value=Decimal(micro)/Decimal(service.UNITS[unit][2]*1000000)
    # A kg/l batch can have a one-microgram/microlitre remainder after a small operation.
    # Switch to the base unit instead of rounding away stock or producing an uneditable number.
    if value.normalize().as_tuple().exponent < -6:
        unit={'mass':'g','volume':'ml','pieces':'pcs','packs':'pack'}[service.UNITS[unit][1]]
        value=Decimal(micro)/1000000
    batch.quantity_micro=micro;batch.unit=unit;batch.entered_quantity=format(value.normalize(),'f')


def target_group(ses,owner,storage_id,values,parsed,source):
    details=parsed['details']
    identity={key:service.clean(values[key]).casefold() for key in ['name','brand','manufacturer']}
    identity.update(dimension=parsed['dimension'],details={key:service.clean(val).casefold() for key,val in details.items() if key!='catalog_source'})
    same=all(getattr(source,key)==values[key] for key in ['name','brand','manufacturer']) and source.dimension==parsed['dimension'] and source.details==details
    if source.storage_id==storage_id and same:return source
    key=digest(identity) if values['brand'] else secrets.token_hex(32)
    group=ses.scalar(select(ProductGroup).where(ProductGroup.owner_id==owner,ProductGroup.storage_id==storage_id,ProductGroup.identity_key==key))
    if group is None:
        group=ProductGroup(owner_id=owner,storage_id=storage_id,name=values['name'],brand=values['brand'],manufacturer=values['manufacturer'],
                           dimension=parsed['dimension'],identity_key=key,details=details,
                           search_key=service.clean(values['name']+' '+values['brand']+' '+values['manufacturer']).casefold())
        ses.add(group);ses.flush()
    elif details.get('catalog_source')=='openfoodfacts' and group.details.get('catalog_source')!='openfoodfacts':
        group.details={**group.details,'catalog_source':'openfoodfacts'}
    return group


def apply(owner,batch_id,action,key,expected,params):
    if action not in ACTIONS or not re.fullmatch('[a-f0-9]{48}',key):raise OperationError('Форма устарела. Откройте её заново.',400)
    payload=digest(dict(batch_id=batch_id,action=action,expected=expected,params=params))
    with db_session.create_session() as ses:
        ses.execute(text('BEGIN IMMEDIATE'))
        old=ses.scalar(select(ProductMutation).where(ProductMutation.owner_id==owner,ProductMutation.submission_key==key))
        if old:
            if old.payload_hash!=payload:raise OperationError('Эта форма уже отправлена с другими данными. Откройте новую.',409)
            return old.result_group_id,False
        batch,group,place=owned(ses,owner,batch_id);before=snapshot(batch,group,place)
        if expected!=digest(before):raise OperationError('Поступление уже изменилось в другой вкладке. Обновите страницу перед действием.',409)
        original_group=group.id;removed=None;after={};result_group=None
        if action=='delete':
            if params.get('confirm')!='yes':raise OperationError('Подтвердите удаление поступления.')
            removed=batch.submission_key
            seen=ses.get(ProductNotificationRead,(owner,batch.id))
            if seen:ses.delete(seen)
            ses.delete(batch)
        elif action=='edit':
            values,parsed=params['values'],params['parsed'];destination=params['storage_id']
            newplace=ses.scalar(select(StorageLocation).where(StorageLocation.id==destination,StorageLocation.owner_id==owner))
            if not newplace:raise OperationError('Место хранения уже удалено. Выберите другое.',409,'storage_id')
            dest=target_group(ses,owner,destination,values,parsed,group)
            batch.group_id=dest.id;update_amount(batch,parsed['quantity_micro'],values['unit'])
            for name in ['expiry_date','manufactured_date','opened_date','shelf_value','shelf_unit','price_kopecks']:setattr(batch,name,parsed[name])
            if batch.quantity_micro==0 and batch.price_kopecks is not None:batch.price_kopecks=0
            batch.note=values['note'];after=snapshot(batch,dest,newplace);result_group=dest.id
        else:
            errors={};unit=params.get('unit','');amount=service.number(params.get('quantity',''),'Количество',errors,'quantity',Decimal('1000000'),True)
            if amount is None or errors or amount<=0:raise OperationError('Введите количество больше нуля, до 6 знаков после запятой.',field='quantity')
            if unit not in service.UNITS or service.UNITS[unit][1]!=group.dimension:raise OperationError('Единица измерения не соответствует продукту.',field='unit')
            micro=int(amount*service.UNITS[unit][2]*1000000)
            if micro>batch.quantity_micro:raise OperationError('Указано больше текущего остатка.',field='quantity')
            if micro<=0:raise OperationError('Количество слишком мало.',field='quantity')
            if action=='waste' and params.get('reason') not in ['expired','spoiled','other']:raise OperationError('Выберите причину списания.',field='reason')
            note=params.get('note','').strip()
            if len(note)>1000:raise OperationError('Не более 1000 символов.',field='note')
            cost=None if batch.price_kopecks is None else int((Decimal(batch.price_kopecks)*micro/batch.quantity_micro).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            if action=='move':
                target=params.get('storage_id')
                dest_place=ses.scalar(select(StorageLocation).where(StorageLocation.id==target,StorageLocation.owner_id==owner))
                if not dest_place:raise OperationError('Выберите своё место хранения.',field='storage_id')
                if target==place.id:raise OperationError('Продукт уже находится в этом месте.',field='storage_id')
                vals=values_for(batch,group);parsed={'details':group.details,'dimension':group.dimension}
                dest=target_group(ses,owner,target,vals,parsed,group)
                if micro==batch.quantity_micro:
                    batch.group_id=dest.id;moved=batch
                else:
                    moved=ProductBatch(owner_id=owner,group_id=dest.id,quantity_micro=micro,entered_quantity='0',unit=batch.unit,
                                       expiry_date=batch.expiry_date,manufactured_date=batch.manufactured_date,shelf_value=batch.shelf_value,shelf_unit=batch.shelf_unit,
                                       opened_date=batch.opened_date,price_kopecks=cost,note=batch.note,added_at=batch.added_at,
                                       submission_key=key+'-move',payload_hash=digest(dict(move=key)))
                    update_amount(moved,micro);ses.add(moved);ses.flush()
                    update_amount(batch,batch.quantity_micro-micro)
                    if cost is not None:batch.price_kopecks-=cost
                after=snapshot(moved,dest,dest_place);result_group=dest.id
            else:
                update_amount(batch,batch.quantity_micro-micro)
                if cost is not None:batch.price_kopecks-=cost
                after=snapshot(batch,group,place);result_group=group.id
            after={**after,'operation_quantity_micro':micro,'operation_cost_kopecks':cost,'operation_note':note,'reason':params.get('reason','')}
        ses.flush()
        if not ses.scalar(select(ProductBatch.id).where(ProductBatch.group_id==original_group).limit(1)):
            ses.delete(group)
        ses.add(ProductMutation(owner_id=owner,batch_id=batch_id,action=action,submission_key=key,payload_hash=payload,
                                before=before,after=after,result_group_id=result_group,removed_submission_key=removed))
        ses.commit();return result_group,True


def notifications(owner):
    today=service.today();until=today+timedelta(days=3)
    with db_session.create_session() as ses:
        rows=ses.execute(select(ProductBatch,ProductGroup,StorageLocation).join(ProductGroup,ProductGroup.id==ProductBatch.group_id)
                         .join(StorageLocation,StorageLocation.id==ProductGroup.storage_id).where(ProductBatch.owner_id==owner,
                         ProductGroup.owner_id==owner,StorageLocation.owner_id==owner,ProductBatch.quantity_micro>0,
                         ProductBatch.expiry_date<=until).order_by(ProductBatch.expiry_date,ProductBatch.id)).all()
        seen={r.batch_id:r.signature for r in ses.scalars(select(ProductNotificationRead).where(ProductNotificationRead.owner_id==owner))}
    items=[]
    for batch,group,place in rows:
        days=(batch.expiry_date-today).days;state='expired' if days<0 else 'today' if days==0 else 'soon'
        signature=digest(dict(date=batch.expiry_date,state=state))
        items.append(dict(batch_id=batch.id,group_id=group.id,name=group.name,place=place.name,date=batch.expiry_date.strftime('%d.%m.%Y'),
                          text='Срок истёк' if days<0 else 'Срок истекает сегодня' if days==0 else 'Срок истекает через '+str(days)+' дн.',
                          state=state,signature=signature,unread=seen.get(batch.id)!=signature))
    return dict(items=items[:50],unread=sum(i['unread'] for i in items),total=len(items),all_items=items)


def mark_notifications(owner,expected):
    # Serialize against stock mutations so a stale notification cannot mark a new expiry as read.
    with db_session.create_session() as ses:
        ses.execute(text('BEGIN IMMEDIATE'))
        # notifications() reads an independent session; BEGIN IMMEDIATE prevents stock writers.
        signatures={item['batch_id']:item['signature'] for item in expected}
        for item in notifications(owner)['all_items']:
            if signatures.get(item['batch_id'])!=item['signature']:continue
            row=ses.get(ProductNotificationRead,(owner,item['batch_id']))
            if row is None:row=ProductNotificationRead(owner_id=owner,batch_id=item['batch_id']);ses.add(row)
            row.signature=item['signature']
        ses.commit()
