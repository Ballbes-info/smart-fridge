"""Immutable stock timeline presentation, shared filters and safe spreadsheet exports."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from services import product_service as service

ACTIONS={'add':'Добавление','import':'Импорт из файла','edit':'Редактирование','use':'Расход','waste':'Списание','move':'Перемещение','delete':'Удаление'}
MOSCOW=timezone(timedelta(hours=3))
LABELS={'name':'Название','brand':'Марка','manufacturer':'Производитель','storage_name':'Место хранения','quantity_micro':'Количество','unit':'Единица ввода',
 'expiry_date':'Годен до','manufactured_date':'Изготовлено','opened_date':'Вскрыто','shelf_value':'Срок хранения','shelf_unit':'Единица срока',
 'price_kopecks':'Стоимость остатка, ₽','note':'Заметка','ingredients':'Состав','category':'Категория','nutrition_basis':'Основание КБЖУ',
 'portion':'Размер порции','calories':'Калории, ккал','protein':'Белки, г','fat':'Жиры, г','carbs':'Углеводы, г','other_info':'Другие сведения',
 'barcode':'Штрихкод','catalog_source':'Источник карточки'}
REASONS={'expired':'Истёк срок','spoiled':'Испортилось','other':'Другое'}

def flattened(snapshot):
    return {**snapshot,**snapshot.get('details',{})}


def display(key,value,snapshot):
    if value is None or value=='':return '—'
    if key=='quantity_micro':return service.quantity_text(value,snapshot['dimension'])
    if key=='price_kopecks':return format(Decimal(value)/100,'.2f').replace('.',',')
    if key in ['expiry_date','manufactured_date','opened_date']:
        try:return date.fromisoformat(value).strftime('%d.%m.%Y')
        except ValueError:return str(value)
    if key=='category':return service.CATEGORIES.get(value,value)
    if key=='nutrition_basis':return service.BASES.get(value,value)
    if key=='shelf_unit':return service.SHELF_UNITS.get(value,value)
    if key=='unit':return service.UNITS.get(value,(value,))[0]
    if key=='catalog_source':return 'Open Food Facts' if value=='openfoodfacts' else str(value)
    return str(value)


def view(event):
    before=event.before or {};after=event.after or {};current=after or before
    left=flattened(before);right=flattened(after)
    changes=[]
    for key,label in LABELS.items():
        if left.get(key)!=right.get(key) or (key=='quantity_micro' and before.get('dimension')!=after.get('dimension')):
            changes.append(dict(label=label,before=display(key,left.get(key),before),after=display(key,right.get(key),after)))
    dimension=before.get('dimension') or after.get('dimension')
    return dict(id=event.id,action=event.action,label=ACTIONS.get(event.action,event.action),created_at=event.created_at,
        timestamp=datetime.fromtimestamp(event.created_at,MOSCOW).strftime('%d.%m.%Y %H:%M:%S МСК'),
        day=datetime.fromtimestamp(event.created_at,MOSCOW).strftime('%d.%m.%Y'),
        name=current.get('name') or before.get('name','Продукт'),old_name=before.get('name',''),
        place=before.get('storage_name') or after.get('storage_name','—'),destination=after.get('storage_name',''),
        batch_id=event.batch_id,group_id=event.result_group_id,before=before,after=after,changes=changes,
        quantity=service.quantity_text(after['operation_quantity_micro'],dimension) if after.get('operation_quantity_micro') else display('quantity_micro',current.get('quantity_micro'),current),
        reason=REASONS.get(after.get('reason'),''),note=after.get('operation_note',''),filename=after.get('import_filename',''))


def safe_cell(value):
    text=str(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')) else text

