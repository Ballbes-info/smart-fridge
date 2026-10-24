"""Read-only recipe catalog, kept separate from private account/inventory data."""
from contextlib import contextmanager
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_SOURCE='https://huggingface.co/datasets/d0rj/povarenok_recipes_detail'

class CatalogError(ValueError):pass

def catalog_path():return Path(os.environ.get('RECIPES_DB',str(ROOT/'db'/'recipes.db'))).expanduser().resolve()

def clean(value,limit=12000):
    if value is None:return ''
    if not isinstance(value,(str,int,float)):return ''
    text=html.unescape(str(value))
    text=re.sub(r'<[^>]*>',' ',text)
    return ' '.join(text.split())[:limit]

def safe_url(value):
    if not isinstance(value,str) or len(value)>2000:return ''
    try:
        url=urlparse(value.strip())
        if url.scheme not in ['https','http'] or url.username or url.password or not url.hostname:return ''
        if url.hostname not in ['povarenok.ru','www.povarenok.ru','vkuso.ru','www.vkuso.ru','st.vkuso.ru','huggingface.co'] and not url.hostname.endswith('.povarenok.ru'):return ''
        return url._replace(scheme='https').geturl()
    except ValueError:return ''


def positive(value):
    text=clean(value,50).replace(',','.')
    unicode_fraction=re.fullmatch(r'(\d+)?\s*([½¼¾])',text)
    if unicode_fraction:
        text=str(Decimal(unicode_fraction[1] or 0)+{'½':Decimal('.5'),'¼':Decimal('.25'),'¾':Decimal('.75')}[unicode_fraction[2]])
    # Mixed/simple fractions occur in real ingredient quantities. Ranges remain descriptive.
    try:
        match=re.fullmatch(r'(?:(\d+)\s+)?(\d+)/(\d+)',text)
        number=Decimal(match[1] or 0)+Decimal(match[2])/Decimal(match[3]) if match else Decimal(text)
        return str(number.normalize()) if number.is_finite() and 0<number<=1000000 else None
    except (InvalidOperation,ZeroDivisionError):return None


def minutes(value):
    text=clean(value,120).casefold()
    if not text:return None
    if re.fullmatch(r'\d+',text):return int(text) if 0<int(text)<=43200 else None
    found=re.findall(r'(\d+(?:[.,]\d+)?)\s*(дн|день|дня|сут|час|ч\b|мин|м\b)',text)
    if not found:return None
    result=sum(float(n.replace(',','.'))*(1440 if u.startswith(('д','сут')) else 60 if u.startswith('ч') else 1) for n,u in found)
    return math.ceil(result) if 0<result<=43200 else None


def servings(value):
    text=clean(value,120)
    match=re.fullmatch(r'\s*(\d{1,3})\s*(?:порци\w*)?\s*',text,re.I)
    return int(match[1]) if match and 0<int(match[1])<=100 else None


# The source dataset keeps categories in a free-form tag list, so raw values contain typos,
# Latin look-alikes, word forms and random words. Every category is mapped onto this fixed
# reference list; anything unknown stays empty instead of polluting the category filter.
CATEGORY_TRANSLIT=str.maketrans({'ё':'е','ə':'е',
    'a':'а','e':'е','o':'о','p':'р','c':'с','x':'х','y':'у','k':'к','m':'м','A':'а','B':'в',
    'E':'е','H':'н','M':'м','N':'н','C':'с','T':'т','Y':'у','X':'х'})
# Words that only look like a category by their stem ("грильяж" is not "гриль").
CATEGORY_REJECTS=('печеноч','печенк','пастернак','грильяж','рыбалк','рыбак','морск','супер',
    'тортиль','сыровялен','сырок','сырки','детокс','холодильник','рисунок','рисун','варшавск')
# Deliberately short list: filters stay readable instead of repeating thousands of source tags.
CATEGORIES=('Салаты','Супы','Вторые блюда','Пельмени и вареники','Паста','Пицца','Блины и оладьи',
    'Бутерброды и фастфуд','Шашлык и гриль','Закуски','Гарниры','Соусы','Торты и пирожные',
    'Пироги и пирожки','Печенье и кексы','Хлеб и булочки','Выпечка','Десерты','Заготовки','Напитки',
    'Завтраки','Обеды','Ужины','Перекусы','Детские блюда','Праздничные блюда','Пост и ПП')
# Checked in CATEGORIES order, so a specific dish wins over a general meal of the day.
CATEGORY_RULES={
    'Салаты':('салат','залат','винегрет','цезарь','греческий'),
    'Супы':('суп','борщ','борщ','щи','бульон','солянк','похлёб','похлеб','холодник','окрош','бульон','рагу'),
    'Вторые блюда':('втор','основн','горяч','котлет','биточк','биток','битк','тефтел','гуляш','отбивн',
        'шницел','эскалоп','бефстроганов','голубц','голубец','запеканк','жаркое','плов','азу','шашлык'),
    'Пельмени и вареники':('пельм','вареник','варенич','манты'),
    'Паста':('паста','макарон','спагет','лазан','равиол','каннеллон','тортеллин','феттучин','пенне','ньок',
        'вермишел','лапша','лагман'),
    'Пицца':('пицц','кальцоне'),
    'Блины и оладьи':('блин','олад','ладушк','сырник','ваф','маслениц'),
    'Бутерброды и фастфуд':('бутербр','бутер','сендвич','сэндвич','бургер','хотдог','чизбургер','шаурм',
        'шаверма','фастфуд','наггетс','кириешки'),
    'Шашлык и гриль':('шашлык','шашлычк','гриль','гриле','мангал','барбекю','люля','кебаб','наугле','углях'),
    'Закуски':('закуск','закус','заливн','холодец','тарталетк','канапе','нарезк','паштет','икра','икорн'),
    'Гарниры':('гарнир','пюре','рис','гречк','крупа','крупяная','булгур','кускус','киноа','чечев','фасол','горох'),
    'Соусы':('соус','заправ','подлив','маринад','майонез','кетчуп','сальса','песто','аджик','ткемали','бернез'),
    'Торты и пирожные':('торт','эклер','пирожн','наполеон','муравейник','сметанник','медовик','чизк',
        'макаронс','птифуар','профитрол','меренг','трубочк','кирсанов'),
    'Пироги и пирожки':('пирог','пирожк','расстега','кулебяк','осетин'),
    'Печенье и кексы':('печенье','печенюш','печеньк','пряник','кекс','кейк','маффин','капкейк','бисквит','крекер'),
    'Хлеб и булочки':('хлеб','батон','булочк','булок','бублик','багет','чиабатта','лепешк','лаваш','заквас'),
    'Выпечка':('выпеч','выпек','тесто','сдоб','слойк','слоечк','слоен','слоён','крем','масти','рулет'),
    'Десерты':('десерт','десер','десет','дечерт','дсерт','сладк','конфет','морожен','пломбир','желе','мусс',
        'суфле','пудинг','безе','зефир','пастил','халва','рахат','трюфел','пралин','ириск','сникерс','баунти',
        'раффаелло','тирамису','паннакота','крамбль','трайфл','фондан'),
    'Заготовки':('заготов','консерв','варенье','джем','повидл','засол','посол','малосол','маринов','соленья',
        'соления','замороз'),
    'Напитки':('напит','сок','компот','морс','кисел','взвар','узвар','сбитен','сбитень','лимонад','квас','кофе',
        'коф','чай','чаепит','какао','глинтвейн','пунш','смузи','фрэш','фреш','мохито','махито','коктейл','настойк',
        'ликер','алкогол','водк','коньяк','виски','шампан','игрист','вино','пив','самогон','чача'),
    'Завтраки':('завтрак','завтра','омлет','яичниц','глазун','скрэмбл','мюсл','гранол','каш'),
    'Обеды':('обед',),
    'Ужины':('ужин',),
    'Перекусы':('перекус','прекус','полдник','снэк','ланч','пикник'),
    'Детские блюда':('дет','малыш'),
    'Праздничные блюда':('праздник','празднич','гост','фуршет','пасха','пасxа','кулич','рождеств','новогод',
        'юбиле','деньрождения','валентин','хеллоуин'),
    'Пост и ПП':('пост','диет','похуд','вегет','веган','безгл','безлакт','низкокал','обезжир','зож','диабет',
        'целиаки','глютен','пп'),
}
# Exact-match variants: normalised tag → canonical name (or '' to discard junk).
CATEGORY_VARIANTS={
    # junk / noise words → discard
    '':'','блюдо':'','еда':'','стол':'','вкусно':'','красиво':'','необычно':'','полезно':'',
    'домашнее':'','домашние':'','домашний':'','домашняя':'','оригинальное':'','оригинальные':'','оригинальный':'',
    'прекрасно':'','прекрасное':'','прекрасный':'','отлично':'','замечательно':'','замечательное':'','интересно':'',
    'интересный':'','эффектно':'','нарядно':'','нарядный':'','удивить':'','удивите':'','удачно':'','всегда':'',
    'только':'','так':'','ход':'','пар':'','сон':'','когда':'','под':'','много':'','можно':'','везде':'',
    'условия':'','условиях':'','заголовка':'','количество':'','ингредиент':'','рецепт':'','кулинария':'',
    'з':'','о':'','после':'','даже':'','почти':'','лучше':'','лучший':'','самые':'','самый':'','весь':'',
    'вас':'','вам':'','дам':'','нам':'','них':'','нее':'','него':'','меня':'','мечты':'','мечта':'',
    'длядеток':'','взрослым':'','взрослый':'','взрослых':'','мужу':'','мужа':'','мужчин':'','мужчина':'',
    # meal typos → canonical
    'залат':'Салаты','обег':'Обеды','обеж':'Обеды','обкд':'Обеды','обнд':'Обеды','оюед':'Обеды','оебед':'Обеды',
    'одед':'Обеды','обед':'Обеды','ообед':'Обеды','бед':'Обеды',
    'полдинк':'Перекусы','полник':'Перекусы','полдний':'Перекусы',
    'кжин':'Ужины','жуин':'Ужины','удин':'Ужины','уин':'Ужины','ужир':'Ужины','ужи':'Ужины','ужик':'Ужины',
    # occasion typos
    'прадзник':'Праздничные блюда','прадничное':'Праздничные блюда','прздник':'Праздничные блюда','прздничный':'Праздничные блюда',
    'празлник':'Праздничные блюда','празничное':'Праздничные блюда','праз':'Праздничные блюда',
    'пекник':'Перекусы','природа':'Перекусы','наприроде':'Перекусы',
    'хеллоуин':'Праздничные блюда','юбиле':'Праздничные блюда','деньрождения':'Праздничные блюда','валентин':'Праздничные блюда',
    # diet typos
    'зож':'Пост и ПП','ппост':'Пост и ПП','постое':'Пост и ПП',
    # snack/začuska typos
    'закускв':'Закуски','закаска':'Закуски','закска':'Закуски','зауска':'Закуски','закучка':'Закуски',
    'акуска':'Закуски','зукуска':'Закуски','зхакуска':'Закуски','зкауска':'Закуски','назакуску':'Закуски',
    'холдное':'Закуски','икра':'Закуски','икры':'Закуски','икорный':'Закуски','закусон':'Закуски',
    # hot dishes typos
    'гоячее':'Вторые блюда','горяячее':'Вторые блюда','грячее':'Вторые блюда','нагорячее':'Вторые блюда','блюдогорячее':'Вторые блюда',
    'вотрое':'Вторые блюда','вророе':'Вторые блюда','втррое':'Вторые блюда','аторое':'Вторые блюда','вторе':'Вторые блюда','второгое':'Вторые блюда',
    'перове':'Супы','перпвое':'Супы',
    # sauces typos
    'суос':'Соусы','соусе':'Соусы',
    # drinks typos
    'коктель':'Напитки','мохито':'Напитки','махито':'Напитки','фраппучино':'Напитки','пунш':'Напитки',
    'сок':'Напитки','сока':'Напитки','чай':'Напитки','чаи':'Напитки','чаю':'Напитки','чаем':'Напитки',
    'чайный':'Напитки','чайку':'Напитки','чаепие':'Напитки','кофе':'Напитки','ккофе':'Напитки',
    'чааепитие':'Напитки','чпепитие':'Напитки',
    # desserts typos
    'дсерт':'Десерты','дечерт':'Десерты','лесерт':'Десерты','жесерт':'Десерты','деесрт':'Десерты','десет':'Десерты',
    'десетр':'Десерты','десперт':'Десерты','десрет':'Десерты','десерт':'Десерты','есерт':'Десерты',
    'впечка':'Выпечка','выпекча':'Выпечка','пымечка':'Выпечка',
    'печеье':'Печенье и кексы','печеь':'Печенье и кексы','печоыне':'Печенье и кексы',
    'пиог':'Пироги и пирожки','пирг':'Пироги и пирожки','питог':'Пироги и пирожки','таорог':'Пироги и пирожки',
    'пирожое':'Торты и пирожные','пирожок':'Пироги и пирожки','кек':'Печенье и кексы','кеск':'Печенье и кексы',
    'каккейки':'Печенье и кексы','капкейк':'Печенье и кексы','будочки':'Хлеб и булочки',
    'пхлеб':'Хлеб и булочки','ххлеб':'Хлеб и булочки','заеканка':'Вторые блюда',
    'мульитварка':'','мультиврка':'','вмультиварке':'','шашалык':'Шашлык и гриль',
    'будерброды':'Бутерброды и фастфуд','скоро':'','минутка':'','бстро':'',
    'сметанник':'Торты и пирожные','курятина':'','курзе':'',
    'закуска!!!':'Закуски','закуска-обед-ужин':'Закуски','закускаа':'Закуски',
    'быстрый':'','обед-ужин':'Обеды','второе-ужин':'Ужины','второе-обед-ужин':'Обеды',
    'горячее-второе-обед-ужин':'Вторые блюда','закускa':'Закуски',
    'первое)':'Супы','первое':'Супы','второе)':'Вторые блюда','второе':'Вторые блюда',
    'второео':'Вторые блюда','второеф':'Вторые блюда','второе/':'Вторые блюда',
    'второй':'Вторые блюда','вторым':'Вторые блюда',
    'первое)':'Супы','первое':'Супы','второе':'Вторые блюда',
}


def category_key(value):
    text=clean(value,200).casefold().translate(CATEGORY_TRANSLIT)
    return re.sub(r'[^а-я-]','',text)


def canonical_category(value):
    """Map one raw tag onto the reference category list, or '' when it is not a category."""
    key=category_key(value)
    if not key:return ''
    # Split on hyphen, check each part (joined values like "закускa" are already in VARIANTS)
    parts=[p for p in key.split('-') if p]
    # First pass: exact variant match per part (covers joined values too)
    for part in parts:
        mapped=CATEGORY_VARIANTS.get(part)
        if mapped is not None:return mapped
    # Second pass: prefix match against CATEGORY_RULES in CATEGORIES priority order
    for name in CATEGORIES:
        stems=CATEGORY_RULES[name]
        for part in parts:
            if part.startswith(CATEGORY_REJECTS):continue
            if any(part.startswith(s) for s in stems):return name
    # Third pass: substring for joined words like "блюдо горячее"
    for name in CATEGORIES:
        stems=CATEGORY_RULES[name]
        for part in parts:
            if part.startswith(CATEGORY_REJECTS):continue
            if any(len(s)>=5 and s in part for s in stems):return name
    return ''


def pick_category(*values):
    """Return the highest-priority canonical name among all values."""
    found=[canonical_category(v) for v in values if canonical_category(v)]
    if not found:return ''
    return min(found,key=lambda n:CATEGORIES.index(n))


def normalize(raw,*,row_number=None):
    if not isinstance(raw,dict):raise CatalogError('Запись рецепта должна быть объектом.')
    title=clean(raw.get('title') or raw.get('name'),240)
    if not title:raise CatalogError('Нет названия.')
    if not re.search('[А-Яа-яЁё]',title):raise CatalogError('Название не на русском.')
    ingredients=[]
    def append_ing(name,count='',unit='',note='',group=''):
        name=clean(name,200)
        if not name:return
        count=clean(count,160);unit=clean(unit,40)
        quantity=positive(count)
        if not quantity and count:
            m=re.fullmatch(r'(\d+(?:[.,]\d+)?|\d+(?:\s+\d+)?/\d+)\s+(.{1,35})',count)
            if m:quantity=positive(m[1]);unit=unit or m[2]
        original=(count+(' '+unit if unit and not count.endswith(unit) else '')).strip()
        ingredients.append(dict(name=name,amount=quantity,unit=unit,original=original,note=clean(note,400),group=clean(group,120)))
    source_ing=raw.get('ingredients') or []
    if isinstance(source_ing,str):
        if len(source_ing)>200000:raise CatalogError('Слишком большой состав.')
        try:source_ing=json.loads(source_ing)
        except ValueError:
            import ast
            try:source_ing=ast.literal_eval(source_ing)
            except (ValueError,SyntaxError,RecursionError):raise CatalogError('Не удалось прочитать ингредиенты.')
    if isinstance(source_ing,dict):
        for name,count in source_ing.items():append_ing(name,count)
    elif isinstance(source_ing,list):
        for item in source_ing:
            if not isinstance(item,dict):continue
            if isinstance(item.get('list'),list):
                for ing in item['list']:
                    if isinstance(ing,dict):append_ing(ing.get('name'),ing.get('value') or ing.get('amount'),ing.get('type'),ing.get('notes'),item.get('name'))
            else:append_ing(item.get('name') or item.get('ingredient'),item.get('count') or item.get('quantity') or item.get('amount') or item.get('value'),item.get('unit') or item.get('type'),item.get('note') or item.get('notes'),item.get('group'))
    if not ingredients:raise CatalogError('Нет ингредиентов.')
    if len(ingredients)>150:raise CatalogError('Слишком много ингредиентов.')
    steps=[]
    source_steps=raw.get('cooking_steps') or raw.get('instruction') or raw.get('steps') or []
    if isinstance(source_steps,str):
        try:source_steps=json.loads(source_steps)
        except ValueError:source_steps=[source_steps]
    images=raw.get('cooking_steps_images_urls') or []
    if isinstance(source_steps,list):
        for i,item in enumerate(source_steps):
            text=clean(item.get('text') or item.get('instruction'),8000) if isinstance(item,dict) else clean(item,8000)
            image=safe_url(item.get('image')) if isinstance(item,dict) else safe_url(images[i]) if isinstance(images,list) and i<len(images) else ''
            if text:steps.append(dict(text=text,image=image))
    if len(steps)>150:raise CatalogError('Слишком много шагов.')
    url=safe_url(raw.get('url') or raw.get('source') or raw.get('link') or raw.get('recipe_url'))
    identifier=raw.get('recipe_id') or raw.get('id')
    match=re.search(r'povarenok\.ru/recipes/show/(\d+)',url)
    if match:key='povarenok:'+match[1]
    elif str(identifier or '').isdigit():key='povarenok:'+str(identifier);url=url or 'https://www.povarenok.ru/recipes/show/'+str(identifier)+'/'
    elif url and 'huggingface.co' not in url:key='url:'+hashlib.sha256(url.encode()).hexdigest()
    else:
        signature=title.casefold()+'|'+json.dumps([(i['name'].casefold(),i['original']) for i in ingredients],ensure_ascii=False)+'|'+json.dumps([s['text'] for s in steps],ensure_ascii=False)
        key='content:'+hashlib.sha256(signature.encode()).hexdigest()
    tags=[]
    source_tags=raw.get('tags') or raw.get('kroshki') or []
    if isinstance(source_tags,str):
        try:source_tags=json.loads(source_tags)
        except ValueError:source_tags=[source_tags]
    if not isinstance(source_tags,list):source_tags=[]
    for tag in source_tags[:100]:
        tag=clean(tag.get('name') or tag.get('text') or tag.get('title'),120) if isinstance(tag,dict) else clean(tag,120)
        if tag and tag not in tags and tag.casefold() not in ['главная','рецепты',title.casefold()]:tags.append(tag)
    category=pick_category(raw.get('category'),*tags)
    # Breadcrumbs are categories only, never dietary assurances.
    portions=servings(raw.get('portions_count') or raw.get('servings'))
    if not portions:
        for group in source_ing if isinstance(source_ing,list) else []:
            if isinstance(group,dict):
                m=re.search(r'на\s+(\d+)\s+порци',str(group.get('name','')),re.I)
                if m:portions=servings(m[1]);break
    prep=minutes(raw.get('preparetime'));cook=minutes(raw.get('cooking_time') or raw.get('cooktime') or raw.get('total_time'))
    total=(prep or 0)+(cook or 0) or None
    result=dict(source_key=key,title=title,description=clean(raw.get('description'),8000),category=category,cuisine=clean(raw.get('cuisine'),120),
        image=safe_url(raw.get('main_image_url') or raw.get('poster') or raw.get('image')),source_url=url or DEFAULT_SOURCE,
        minutes=total,servings=portions,ingredients=ingredients,steps=steps,tags=tags[:60],note=clean(raw.get('note'),2000),complete=bool(steps))
    result['key']=hashlib.sha256(key.encode()).hexdigest()[:24]
    result['search_text']=' '.join([title,category,result['cuisine'],*tags,*[i['name'] for i in ingredients]]).casefold().replace('ё','е')
    return result


SCHEMA='''
CREATE TABLE IF NOT EXISTS recipes(
 id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, public_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 category TEXT NOT NULL, cuisine TEXT NOT NULL, minutes INTEGER, servings INTEGER,
 complete INTEGER NOT NULL, search_text TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_recipe_category ON recipes(category,complete);
CREATE INDEX IF NOT EXISTS ix_recipe_minutes ON recipes(complete,minutes);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''


def initialize(path):
    con=sqlite3.connect(str(path));con.row_factory=sqlite3.Row
    con.executescript(SCHEMA)
    try:con.execute('CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(search_text)');con.execute("INSERT OR REPLACE INTO metadata VALUES('fts','1')")
    except sqlite3.OperationalError:con.execute("INSERT OR REPLACE INTO metadata VALUES('fts','0')")
    con.commit();return con


@contextmanager
def connection():
    path=catalog_path()
    if not path.is_file():yield None;return
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10);con.row_factory=sqlite3.Row
    try:yield con
    finally:con.close()


def info(con):
    if con is None:return dict(count=0,complete=0,incomplete=0,source='',loaded_at='',revision='',fts=False)
    meta=dict(con.execute('SELECT key,value FROM metadata'))
    counts=con.execute('SELECT count(*),coalesce(sum(complete),0) FROM recipes').fetchone()
    return dict(count=counts[0],complete=counts[1],incomplete=counts[0]-counts[1],source=meta.get('source',''),loaded_at=meta.get('loaded_at',''),revision=meta.get('revision',''),fts=meta.get('fts')=='1')


def browse(args):
    query=clean(args.get('q',''),120);category=clean(args.get('category',''),120);sort=args.get('sort','name');content=args.get('content','complete');limit=args.get('time','')
    if sort not in ['name','time']:sort='name'
    if content not in ['complete','all','incomplete']:content='complete'
    if limit not in ['','15','30','60','120']:limit=''
    with connection() as con:
        status=info(con);rows=[];total=0;page=1;pages=1
        categories=[tuple(r) for r in con.execute('SELECT category,count(*) FROM recipes WHERE category<>\'\' GROUP BY category ORDER BY category')] if con else []
        if con:
            where=[];params=[]
            if content!='all':where.append('r.complete=?');params.append(1 if content=='complete' else 0)
            if category:where.append('r.category=?');params.append(category)
            if limit:where.append('r.minutes<=?');params.append(int(limit))
            tokens=re.findall(r'[^\W_]+',query.casefold().replace('ё','е'))[:8]
            if tokens:
                if status['fts']:
                    where.append('r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)');params.append(' AND '.join('"'+t+'"*' for t in tokens))
                else:
                    for token in tokens:where.append('r.search_text LIKE ?');params.append('%'+token+'%')
            sql=' WHERE '+' AND '.join(where) if where else ''
            total=con.execute('SELECT count(*) FROM recipes r'+sql,params).fetchone()[0];pages=max(1,math.ceil(total/24))
            try:page=max(1,min(pages,int(args.get('page','1'))))
            except (ValueError,TypeError):pass
            order='r.minutes IS NULL,r.minutes,r.title,r.id' if sort=='time' else 'r.title,r.id'
            rows=[json.loads(r['data'])|{'id':r['id']} for r in con.execute('SELECT r.id,r.data FROM recipes r'+sql+' ORDER BY '+order+' LIMIT 24 OFFSET ?',[*params,(page-1)*24])]
    return dict(recipes=rows,total=total,page=page,pages=pages,catalog=status,categories=categories,filters=dict(q=query,category=category,sort=sort,content=content,time=limit))


def detail(recipe_id,requested_servings=None):
    with connection() as con:
        row=con.execute('SELECT id,data FROM recipes WHERE public_id=?',(recipe_id,)).fetchone() if con else None
    if row is None:return None
    recipe=json.loads(row['data']);recipe['id']=row['id'];base=recipe['servings'];chosen=base;error=''
    if requested_servings is not None:
        try:
            chosen=int(requested_servings)
            if not 1<=chosen<=100 or not base:raise ValueError
        except (ValueError,TypeError):chosen=base;error='Укажите от 1 до 100 порций.' if base else 'В источнике не указано число порций: пересчёт недоступен.'
    for item in recipe['ingredients']:
        item['scaled']=False;item['display']=item['original'] or 'Количество не указано'
        if base and chosen and item['amount']:
            amount=Decimal(item['amount'])*chosen/base
            # Avoid a large change from rounding tiny amounts. Three significant decimal places suffice for display.
            shown=amount.quantize(Decimal('.001'),rounding=ROUND_HALF_UP)
            item['display']=('менее 0,001' if shown==0 else format(shown.normalize(),'f').replace('.',','))+(' '+item['unit'] if item['unit'] else '')
            item['scaled']=chosen!=base
    recipe.update(chosen_servings=chosen,portion_error=error)
    return recipe
