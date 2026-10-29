"""Exact quantities, conservative grouping and validation for product entry."""
import calendar
import hashlib
import json
import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, text
from data import db_session
from data.product import ProductGroup, ProductBatch, ProductMutation
from data.storage import StorageLocation

UNITS = {'g': ('г', 'mass', 1), 'kg': ('кг', 'mass', 1000), 'ml': ('мл', 'volume', 1),
         'l': ('л', 'volume', 1000), 'pcs': ('шт.', 'pieces', 1), 'pack': ('уп.', 'packs', 1)}
BASES = {'100g': 'На 100 г', '100ml': 'На 100 мл', 'portion': 'На порцию'}
SHELF_UNITS = {'days': 'дней', 'months': 'месяцев', 'years': 'лет'}
NUTRIENTS = {'calories': 'Калории, ккал', 'protein': 'Белки, г', 'fat': 'Жиры, г', 'carbs': 'Углеводы, г'}
CATEGORIES={'vegetables':'Овощи','fruits':'Фрукты и ягоды','meat':'Мясо и птица','fish':'Рыба и морепродукты',
            'dairy':'Молочные продукты','eggs':'Яйца','grains':'Крупы и макароны','bakery':'Хлеб и выпечка',
            'sweets':'Сладости','ready':'Готовое блюдо','other':'Другое'}
FIELDS = ['category', 'name', 'brand', 'manufacturer', 'quantity', 'unit', 'expiry_date', 'manufactured_date',
          'shelf_value', 'shelf_unit', 'opened_date', 'price', 'note', 'ingredients',
          'nutrition_basis', 'portion', 'calories', 'protein', 'fat', 'carbs', 'other_info', 'barcode', 'catalog_source']


def clean(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split())


def today():
    return datetime.now(timezone(timedelta(hours=3))).date()


def number(raw, label, errors, key, maximum, required=False):
    raw = raw.strip().replace(',', '.')
    if not raw:
        if required: errors[key] = f'Укажите {label}.'
        return None
    if not re.fullmatch(r'\d+(?:\.\d{1,6})?', raw):
        errors[key] = f'{label}: введите число без знака, до 6 знаков после запятой.'
        return None
    try:
        value = Decimal(raw)
        if value > maximum: raise InvalidOperation
        return value
    except InvalidOperation:
        errors[key] = f'{label}: слишком большое значение.'
        return None


def add_shelf(start, count, unit):
    if unit == 'days': return start + timedelta(days=count)
    months = (start.year * 12 + start.month - 1) + count * (12 if unit == 'years' else 1)
    year, month = divmod(months, 12); month += 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def validate(form, *, allow_zero=False):
    values = {key: form.get(key, '').strip() for key in FIELDS}
    errors = {}; dates = {}; parsed = {}
    for key, limit in [('name',120),('brand',120),('manufacturer',160),('portion',80)]:
        values[key] = clean(values[key])
        if len(values[key]) > limit or any(unicodedata.category(c).startswith('C') for c in values[key]):
            errors[key] = f'Не более {limit} символов, без управляющих знаков.'
    if values['category'] and values['category'] not in CATEGORIES:errors['category']='Выберите категорию из списка.'
    if not values['name']: errors['name'] = 'Укажите название продукта.'
    from services.product_catalog import valid_gtin
    if values['barcode'] and not valid_gtin(values['barcode']):errors['barcode']='Проверьте штрихкод: нужны 8, 12, 13 или 14 цифр и правильная контрольная цифра.'
    if values['catalog_source'] not in ['', 'openfoodfacts']:values['catalog_source']=''
    if not values['barcode']:values['catalog_source']=''
    for key, limit in [('ingredients',3000),('note',1000),('other_info',3000)]:
        if len(values[key]) > limit: errors[key] = f'Не более {limit} символов.'
    for key in ['expiry_date','manufactured_date','opened_date']:
        dates[key] = None
        if values[key]:
            try:
                dates[key] = date.fromisoformat(values[key])
                if not 1900 <= dates[key].year <= 2200: raise ValueError
            except ValueError: errors[key] = 'Введите дату от 1900 до 2200 года.'
    for key in ['manufactured_date','opened_date']:
        if dates[key] and dates[key] > today(): errors[key] = 'Эта дата не может быть в будущем.'
    if dates['manufactured_date'] and dates['opened_date'] and dates['opened_date'] < dates['manufactured_date']:
        errors['opened_date'] = 'Дата вскрытия раньше даты изготовления.'
    quantity = number(values['quantity'], 'Количество', errors, 'quantity', Decimal('1000000'), True)
    if quantity is not None and quantity <= 0 and not allow_zero: errors['quantity'] = 'Количество должно быть больше нуля.'
    if values['unit'] not in UNITS: errors['unit'] = 'Выберите единицу измерения.'
    if quantity is not None and values['unit'] in UNITS:
        parsed['quantity_micro'] = int(quantity * UNITS[values['unit']][2] * 1000000)
        parsed['entered_quantity'] = format(quantity, 'f')
        parsed['dimension'] = UNITS[values['unit']][1]
    price = number(values['price'], 'Цена партии', errors, 'price', Decimal('1000000000'))
    if price is not None and price.as_tuple().exponent < -2: errors['price'] = 'Цена — не более двух знаков после запятой.'
    parsed['price_kopecks'] = int(price * 100) if price is not None else None
    shelf = None
    if values['shelf_value']:
        if not re.fullmatch(r'\d{1,4}', values['shelf_value']) or not 1 <= int(values['shelf_value']) <= 9999:
            errors['shelf_value'] = 'Срок хранения — целое число от 1 до 9999.'
        elif values['shelf_unit'] not in SHELF_UNITS: errors['shelf_value'] = 'Выберите единицу срока хранения.'
        elif not dates['manufactured_date']: errors['manufactured_date'] = 'Для расчёта срока нужна дата изготовления.'
        else:
            shelf = int(values['shelf_value'])
            try:
                calculated = add_shelf(dates['manufactured_date'], shelf, values['shelf_unit'])
                if calculated.year > 2200: raise ValueError
                dates['expiry_date'] = min(dates['expiry_date'], calculated) if dates['expiry_date'] else calculated
            except (ValueError, OverflowError): errors['shelf_value'] = 'Получилась дата за пределами допустимого диапазона.'
    if dates['expiry_date'] and dates['manufactured_date'] and dates['expiry_date'] < dates['manufactured_date']:
        errors['expiry_date'] = 'Срок годности раньше даты изготовления.'
    nutrition = {}
    for key, label in NUTRIENTS.items():
        val = number(values[key], label, errors, key, Decimal('100000'))
        nutrition[key] = format(val.normalize(), 'f') if val is not None else ''
    if any(nutrition.values()):
        if values['nutrition_basis'] not in BASES: errors['nutrition_basis'] = 'Укажите, к какому количеству относятся КБЖУ.'
        if values['nutrition_basis'] == 'portion' and not values['portion']: errors['portion'] = 'Укажите размер порции, например 250 мл.'
        if values['nutrition_basis'] in ['100g','100ml']:
            for key in ['protein','fat','carbs']:
                if nutrition[key] and Decimal(nutrition[key]) > 100: errors[key] = 'На 100 г/мл значение не может превышать 100 г.'
    elif values['nutrition_basis'] and values['nutrition_basis'] not in BASES: errors['nutrition_basis'] = 'Выберите основание КБЖУ.'
    details = {key: values[key] for key in ['ingredients','nutrition_basis','portion','other_info']}
    for key in ['barcode','catalog_source','category']:
        if values[key]:details[key]=values[key]
    details.update(nutrition)
    parsed.update(dates, shelf_value=shelf, shelf_unit=values['shelf_unit'] if shelf else None, details=details)
    if form.get('ocr_used') == 'yes' and form.get('ocr_confirm') != 'yes':
        errors['ocr_confirm'] = 'Подтвердите, что проверили данные с этикетки.'
    return values, parsed, errors


class ProductConflict(Exception): pass
class StorageMissing(Exception): pass


def save(owner_id, storage_id, submission_key, values, parsed):
    with db_session.create_session() as ses:
        ses.execute(text('BEGIN IMMEDIATE'))
        result = save_in_session(ses, owner_id, storage_id, submission_key, values, parsed)
        ses.commit()
        return result


def save_in_session(ses, owner_id, storage_id, submission_key, values, parsed, *, source='add', source_note=''):
    """Caller owns the transaction; shared by single entry and atomic file imports."""
    details = parsed['details']
    identity = {key: clean(values[key]).casefold() for key in ['name','brand','manufacturer']}
    identity.update(dimension=parsed['dimension'], details={key: clean(val).casefold() for key,val in details.items() if key!='catalog_source'})
    digest = lambda payload: hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    payload_hash = digest(dict(storage_id=storage_id, values=values, parsed=parsed))
    if not ses.scalar(select(StorageLocation.id).where(StorageLocation.id==storage_id, StorageLocation.owner_id==owner_id)):
        raise StorageMissing
    old = ses.scalar(select(ProductBatch).where(ProductBatch.owner_id==owner_id, ProductBatch.submission_key==submission_key))
    if old:
        if old.payload_hash != payload_hash: raise ProductConflict
        return old.group_id, False
    if ses.scalar(select(ProductMutation.id).where(ProductMutation.owner_id==owner_id,ProductMutation.removed_submission_key==submission_key)):
        raise ProductConflict
    key = digest(identity) if values['brand'] else secrets.token_hex(32)
    group = ses.scalar(select(ProductGroup).where(ProductGroup.owner_id==owner_id, ProductGroup.storage_id==storage_id, ProductGroup.identity_key==key))
    if not group:
        group=ProductGroup(owner_id=owner_id, storage_id=storage_id, name=values['name'], brand=values['brand'],
                           manufacturer=values['manufacturer'], dimension=parsed['dimension'], identity_key=key,
                           search_key=clean(values['name']+' '+values['brand']+' '+values['manufacturer']).casefold(), details=details)
        ses.add(group); ses.flush()
    elif details.get('catalog_source')=='openfoodfacts' and group.details.get('catalog_source')!='openfoodfacts':
        group.details={**group.details,'catalog_source':'openfoodfacts'}
    batch=ProductBatch(owner_id=owner_id, group_id=group.id, submission_key=submission_key, payload_hash=payload_hash,
                       quantity_micro=parsed['quantity_micro'], entered_quantity=parsed['entered_quantity'], unit=values['unit'],
                       expiry_date=parsed['expiry_date'], manufactured_date=parsed['manufactured_date'],
                       shelf_value=parsed['shelf_value'], shelf_unit=parsed['shelf_unit'], opened_date=parsed['opened_date'],
                       price_kopecks=parsed['price_kopecks'], note=values['note'])
    ses.add(batch);ses.flush()
    from services.product_operations import snapshot
    place=ses.get(StorageLocation,storage_id)
    after=snapshot(batch,group,place)
    if source=='import':after['import_filename']=source_note[:160]
    event_key=hashlib.sha256(('product-add:'+submission_key).encode()).hexdigest()[:48]
    ses.add(ProductMutation(owner_id=owner_id,batch_id=batch.id,action='import' if source=='import' else 'add',
                            submission_key=event_key,payload_hash=payload_hash,before={},after=after,result_group_id=group.id))
    ses.flush()
    return group.id, True


def quantity_text(micro, dimension):
    value = Decimal(micro) / 1000000
    unit = {'mass':'г','volume':'мл','pieces':'шт.','packs':'уп.'}[dimension]
    if dimension in ['mass','volume'] and value >= 1000:
        value /= 1000; unit = 'кг' if dimension == 'mass' else 'л'
    return format(value.normalize(),'f').replace('.',',')+' '+unit


def expiry_status(batches, on_date):
    """Only stock still present affects a product's expiry colour."""
    active = [batch for batch in batches if batch.quantity_micro > 0]
    if not active:
        return dict(state='finished', label='Израсходовано', date=None, missing=False)
    dates = [batch.expiry_date for batch in active if batch.expiry_date is not None]
    missing = len(dates) != len(active)
    if not dates:
        return dict(state='unknown', label='Срок не указан', date=None, missing=True)
    nearest = min(dates)
    days = (nearest - on_date).days
    state = 'expired' if days < 0 else 'soon' if days <= 3 else 'fresh'
    label = 'Просрочен' if days < 0 else 'Срок истекает сегодня' if days == 0 else 'Скоро истекает' if days <= 3 else 'Годен до'
    return dict(state=state, label=label, date=nearest, missing=missing)
