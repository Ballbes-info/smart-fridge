"""Free barcode decoding and read-only product lookup.

Only a validated GTIN is sent to Open Food Facts; user photos never leave our server.
The SQLite cache contains public catalogue responses, never users' inventory.
"""
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parents[1]
CACHE_PATH = BASE / 'instance' / 'product-catalog.sqlite3'
FIELDS = ('code,product_name_ru,product_name,brands,ingredients_text_ru,ingredients_text,'
          'lang,product_quantity,product_quantity_unit,quantity,nutriments,nutrition_data_per,'
          'nutrition_data_prepared_per,serving_size')
NUTRIENTS = {'protein':'proteins_100g','fat':'fat_100g','carbs':'carbohydrates_100g','calories':'energy-kcal_100g'}


def valid_gtin(value):
    if not isinstance(value,str) or not value.strip('0') or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12,14})',value):return False
    return sum(int(d)*(3 if index%2==0 else 1) for index,d in enumerate(reversed(value[:-1])))%10 == (-int(value[-1]))%10


def equivalent(code):
    """UPC-A, EAN-13 and GTIN-14 can encode the same trade item with leading zeroes."""
    return list(dict.fromkeys([code,code.zfill(14),code.zfill(13)] +
                [code[-size:] for size in (13,12) if len(code)>size and not code[:-size].strip('0')]))


def decode(image):
    try:
        import zxingcpp
    except ImportError:
        return []
    values=[]
    for result in zxingcpp.read_barcodes(image,formats=zxingcpp.BarcodeFormat.EAN13 | zxingcpp.BarcodeFormat.EAN8 |
                                       zxingcpp.BarcodeFormat.UPCA | zxingcpp.BarcodeFormat.ITF,
                                       try_rotate=True,try_downscale=True):
        if result.valid and valid_gtin(result.text) and result.text not in values:values.append(result.text)
    return values[:8]


def _cache():
    path=Path(os.environ.get('PRODUCT_CATALOG_CACHE',str(CACHE_PATH)))
    path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(path,timeout=1)
    con.execute('CREATE TABLE IF NOT EXISTS products (code TEXT PRIMARY KEY, payload TEXT, fetched REAL NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS reads (created REAL NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS backoff (id INTEGER PRIMARY KEY, until_time REAL NOT NULL)')
    return con


def unavailable(state='unavailable'):
    try:
        con=_cache()
        with con:con.execute('INSERT OR REPLACE INTO backoff VALUES (1,?)',(time.time()+60,))
    except (OSError,sqlite3.Error):pass
    finally:
        if 'con' in locals():con.close()
    return {},state


def public_product(code, *, allow_network=True):
    """Return (normalized public fields, state). Never raise on an unavailable catalogue."""
    if not valid_gtin(code):return {},'invalid'
    now=time.time()
    try:
        con=_cache()
        with con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT payload,fetched FROM products WHERE code=?',(code,)).fetchone()
            if row and now-row[1]<(7*86400 if row[0]!='{}' else 3600):
                return json.loads(row[0]),'cached' if row[0]!='{}' else 'not_found'
            if not allow_network or os.environ.get('PRODUCT_CATALOG_ONLINE','1')=='0':return {},'offline'
            backoff=con.execute('SELECT until_time FROM backoff WHERE id=1').fetchone()
            if backoff and backoff[0]>now:return {},'unavailable'
            con.execute('DELETE FROM reads WHERE created<?',(now-60,))
            if con.execute('SELECT COUNT(*) FROM reads').fetchone()[0]>=12:return {},'limited'
            con.execute('INSERT INTO reads VALUES (?)',(now,))
    except (OSError,sqlite3.Error,ValueError):return {},'unavailable'
    finally:
        if 'con' in locals():con.close()
    request=Request('https://world.openfoodfacts.org/api/v2/product/'+code+'.json?'+urlencode({'fields':FIELDS}),
                    headers={'User-Agent':'SmartFridge/1.0 (smartfridgebd@gmail.com)', 'Accept':'application/json'})
    try:
        with urlopen(request,timeout=4) as response:
            raw=response.read(512*1024+1)
            if len(raw)>512*1024:return unavailable()
        payload=json.loads(raw)
        if not isinstance(payload,dict):return unavailable()
        if payload.get('status')==0:product={}
        elif payload.get('status')==1 and isinstance(payload.get('product'),dict):
            supplied=str(payload['product'].get('code',''))
            if supplied not in equivalent(code):return unavailable()
            product=normalize_product(payload['product'])
        else:return unavailable()
    except HTTPError as exc:
        return unavailable('limited' if exc.code==429 else 'unavailable')
    except (URLError,TimeoutError,socket.timeout,OSError,ValueError):return unavailable()
    try:
        con=_cache()
        with con:
            con.execute('INSERT OR REPLACE INTO products VALUES (?,?,?)',(code,json.dumps(product,ensure_ascii=False),now))
            # Bound the public cache and its disk use. This is not user inventory.
            con.execute('DELETE FROM products WHERE code IN (SELECT code FROM products ORDER BY fetched DESC LIMIT -1 OFFSET 2000)')
    except (OSError,sqlite3.Error):pass
    finally:
        if 'con' in locals():con.close()
    return product,'found' if product else 'not_found'


def clean(value,limit):
    return re.sub(r'<[^>]*>','',re.sub(r'\s+',' ',str(value or ''))).strip()[:limit]


def number(value,maximum=100):
    if isinstance(value,bool):return None
    try:
        val=float(str(value).replace(',','.'))
        return format(val,'.6f').rstrip('0').rstrip('.') if math.isfinite(val) and 0<=val<=maximum else None
    except (ValueError,TypeError):return None


def normalize_product(product):
    """Copy declared values only. Prepared-food and portion columns are not substituted."""
    p={}
    for key,candidates,limit in [('name',['product_name_ru','product_name'],120),('brand',['brands'],120),
                                ('ingredients',['ingredients_text_ru','ingredients_text'],3000)]:
        for candidate in candidates:
            value=clean(product.get(candidate),limit)
            if value:p[key]=value;break
    # OFF uses _100g keys for both per100g and per100ml. Require an explicit data basis;
    # refine the 100g/ml sentinel using the declared package unit for drinks.
    quantity_unit=clean(product.get('product_quantity_unit'),12).lower()
    if not quantity_unit and isinstance(product.get('quantity'),str):
        unit_match=re.search(r'(kg|ml|g|l|кг|мл|г|л)\s*$',product['quantity'],re.I)
        if unit_match:quantity_unit={'кг':'kg','мл':'ml','г':'g','л':'l'}.get(unit_match.group(1).lower(),unit_match.group(1).lower())
    declared=clean(product.get('nutrition_data_per'),40).lower().replace(' ','')
    if declared in ['100ml','100мл']:basis='100ml'
    elif declared in ['100g','100г']:
        basis='100ml' if quantity_unit in ['ml','l','cl','dl','мл','л'] else '100g'
    else:basis=None
    nutriments=product.get('nutriments')
    if basis and isinstance(nutriments,dict):
        for key,source in NUTRIENTS.items():
            value=number(nutriments.get(source),1000 if key=='calories' else 100)
            modifier=nutriments.get(source.replace('_100g','_modifier'))
            if value is not None:
                p[key]=value
                if modifier in ['<','>','≤','≥']:
                    p['other_info']=(p.get('other_info','')+'\n'+key+' '+modifier+' '+value).strip()
        if any(k in p for k in NUTRIENTS):p['nutrition_basis']=basis
    amount=number(product.get('product_quantity'),1000000)
    units={'g':('g',1),'kg':('kg',1),'ml':('ml',1),'l':('l',1),'cl':('ml',10),'dl':('ml',100)}
    if amount and float(amount)>0 and quantity_unit in units:
        unit,multiplier=units[quantity_unit];p['quantity']=number(float(amount)*multiplier,1000000);p['unit']=unit
    elif isinstance(product.get('quantity'),str):
        match=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l|кг|г|мл|л)\s*',product['quantity'],re.I)
        if match:
            amount=number(match.group(1),1000000)
            if amount and float(amount)>0:p.update(quantity=amount,unit={'кг':'kg','г':'g','мл':'ml','л':'l'}.get(match.group(2).lower(),match.group(2).lower()))
    return p


def saved_product(user_id,code):
    from sqlalchemy import select
    from data import db_session
    from data.product import ProductGroup
    try:
        with db_session.create_session() as ses:
            group=ses.scalar(select(ProductGroup).where(ProductGroup.owner_id==user_id,
                ProductGroup.details['barcode'].as_string().in_(equivalent(code))).order_by(ProductGroup.id.desc()).limit(1))
            if not group:return {}
            p={key:getattr(group,key) for key in ['name','brand','manufacturer'] if getattr(group,key)}
            for key in ['ingredients','nutrition_basis','portion','calories','protein','fat','carbs','other_info','catalog_source']:
                value=group.details.get(key)
                if value:p[key]=str(value)
            return p
    except (ValueError,TypeError):return {}


def lookup(code,user_id):
    if not valid_gtin(code):return {'proposals':{},'confidence':{},'dates':[],'text':'','warnings':[],'catalog_state':'invalid','sources':{}}
    own=saved_product(user_id,code)
    public,state=public_product(code,allow_network=not bool(own))
    if own:state='saved'
    p={**public,**own}
    p={**p,'barcode':code}
    if public:p['catalog_source']='openfoodfacts'
    return {'proposals':p,'confidence':{key:.86 if key!='barcode' else 1 for key in p},'dates':[],'text':'',
            'warnings':[],'catalog_state':state,'sources':{key:('saved' if key in own else 'openfoodfacts') if key!='barcode' else 'barcode' for key in p},
            'catalog_url':'https://world.openfoodfacts.org/product/'+code if public or own.get('catalog_source')=='openfoodfacts' else None}


def enrich(label,catalog):
    """Catalogue fills gaps; a clear contradiction on the photo is left for review."""
    result={**label,'proposals':dict(label['proposals']),'confidence':dict(label.get('confidence',{})),
            'warnings':list(label.get('warnings',[])),'sources':{k:'label' for k in label['proposals']},
            'alternatives':dict(label.get('alternatives',{})),'amount_options':[]}
    p=result['proposals'];scores=result['confidence'];cp=catalog.get('proposals',{})
    # Quantity and unit form one fact. Never offer independent choices such as 900 kg.
    units={'g':('mass',1),'kg':('mass',1000),'ml':('volume',1),'l':('volume',1000),'pcs':('pieces',1),'pack':('packs',1)}
    if all(key in p and key in cp for key in ['quantity','unit']):
        left,right=units.get(p['unit']),units.get(cp['unit'])
        if left and right:
            same=left[0]==right[0] and math.isclose(float(p['quantity'])*left[1],float(cp['quantity'])*right[1],rel_tol=0,abs_tol=.000001)
            if same:p['quantity']=cp['quantity'];p['unit']=cp['unit']
            elif min(scores.get('quantity',0),scores.get('unit',0))>=.8:
                result['amount_options']=[{'quantity':p['quantity'],'unit':p['unit']},{'quantity':cp['quantity'],'unit':cp['unit']}]
    for key,value in cp.items():
        if key=='catalog_source':continue
        if key in ['quantity','unit'] and result['amount_options']:
            scores[key]=0;continue
        if key in p and p[key]!=value:
            old_score=scores.get(key,0)
            # Readable nutrition and quantity disagreements must not be overwritten by stale catalogue data.
            sensitive=key in ['quantity','unit','nutrition_basis','calories','protein','fat','carbs']
            if sensitive and old_score>=.8:
                result['alternatives'][key]=list(dict.fromkeys([p[key],value]));scores[key]=0
                continue
            if key=='ingredients' and old_score>=.9:continue
            if key=='name' and re.search('[а-яё]',p[key],re.I) and not re.search('[а-яё]',value,re.I) and old_score>=.8:continue
        p[key]=value;scores[key]=catalog.get('confidence',{}).get(key,.86);result['sources'][key]=catalog.get('sources',{}).get(key,'openfoodfacts')
    # If nutrition bases differ, never assemble a table from two incompatible columns.
    if label['proposals'].get('nutrition_basis') and cp.get('nutrition_basis') and label['proposals']['nutrition_basis']!=cp['nutrition_basis']:
        result['warnings'].append('В карточке и на фото разные основания КБЖУ. Выберите данные одной таблицы.')
        for key in ['calories','protein','fat','carbs','nutrition_basis']:
            if key in p:scores[key]=0
    if cp.get('catalog_source')=='openfoodfacts':p['catalog_source']='openfoodfacts';scores['catalog_source']=1
    for key in ['catalog_state','catalog_url']:result[key]=catalog.get(key)
    from services.label_extraction import validate_nutrition
    validate_nutrition(result)
    result['warnings']=list(dict.fromkeys(result['warnings']))
    return result


def identify(raw,user_id):
    from services.label_ocr import prepare_image
    codes=decode(prepare_image(raw))
    result={'proposals':{},'confidence':{},'dates':[],'text':'','warnings':[],'sources':{},'barcodes':codes}
    if len(codes)==1:
        result.update(lookup(codes[0],user_id));result['barcodes']=codes
    elif len(codes)>1:
        result['warnings']=['На фото несколько штрихкодов. Выберите код одного продукта.']
        result['catalog_state']='multiple'
    else:result['catalog_state']='no_barcode'
    return result
