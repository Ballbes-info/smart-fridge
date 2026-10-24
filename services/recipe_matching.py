"""Offline, owner-scoped recipe matching. Never changes stock or spends AI credits.

A derived public SQLite index avoids re-reading recipe instructions per request.
Quantities are allocated once, earliest expiry first, with no density/piece guesses.
"""
from collections import defaultdict
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import unicodedata

from sqlalchemy import select
from data import db_session
from data.product import ProductGroup, ProductBatch
from data.storage import StorageLocation
from services import recipe_catalog as catalog, product_service

INDEX_VERSION = '6'
_INDEX_LOCK = threading.Lock()
PANTRY = {'water': 'Вода', 'salt': 'Соль', 'pepper': 'Чёрный перец'}
STATUS_LABELS = {'enough': 'Хватает', 'missing': 'Нет в запасах', 'short': 'Не хватает',
                 'check': 'Проверьте количество', 'pantry': 'Есть дома'}
# Whole-word patterns, with explicit food compounds before broader nouns.
FOODS = [
 ('condensed_milk', r'сгущенк\w*|молок\w*\s+сгущенн\w*|сгущенн\w*\s+молок\w*'),
 ('milk_powder', r'сух\w*\s+молок\w*|молок\w*\s+сух\w*'),
 ('coconut_milk', r'кокосов\w*\s+молок\w*|молок\w*\s+кокосов\w*'),
 ('soy_sauce', r'соев\w*\s+соус\w*|соус\w*\s+соев\w*'),
 ('tomato_paste', r'томатн\w*\s+паст\w*|паст\w*\s+томатн\w*'),
 ('baking_powder', r'разрыхлител\w*'), ('vanilla_sugar', r'ванильн\w*\s+сахар\w*|сахар\w*\s+ванильн\w*'),
 ('icing_sugar', r'сахарн\w*\s+пудр\w*|пудр\w*\s+сахарн\w*'),
 ('butter', r'сливочн\w*\s+масл\w*|масл\w*\s+сливочн\w*'),
 ('sunflower_oil', r'подсолнечн\w*\s+масл\w*|масл\w*\s+подсолнечн\w*'),
 ('olive_oil', r'оливков\w*\s+масл\w*|масл\w*\s+оливков\w*'),
 ('vegetable_oil', r'растительн\w*\s+масл\w*|масл\w*\s+растительн\w*'),
 ('green_onion', r'зелен\w*\s+лук\w*|лук\w*\s+зелен\w*'),
 ('cauliflower', r'цветн\w*\s+капуст\w*|капуст\w*\s+цветн\w*'),
 ('seaweed', r'морск\w*\s+капуст\w*|капуст\w*\s+морск\w*'),
 ('chicken', r'куриц\w*|курин\w*|курятин\w*'), ('turkey', r'индейк\w*|индюш\w*'),
 ('beef', r'говядин\w*|говяж\w*'), ('pork', r'свинин\w*|свин\w*'), ('lamb', r'баранин\w*|барань\w*'),
 ('salmon', r'лосос\w*|семг\w*'), ('trout', r'форел\w*'), ('cod', r'треск[аиуоеы]?'),
 ('pollock', r'минта[йяюем]+'), ('tuna', r'тунец|тунц\w*'), ('herring', r'сельд\w*|селедк\w*'),
 ('shrimp', r'креветк\w*'), ('squid', r'кальмар\w*'),
 ('sour_cream', r'сметан\w*'), ('cream', r'сливки|сливок|сливками'), ('curd', r'творог\w*'),
 ('kefir', r'кефир\w*'), ('yogurt', r'йогурт\w*'), ('milk', r'молок[оауе]?'),
 ('mozzarella', r'моцарелл\w*'), ('parmesan', r'пармезан\w*'), ('feta', r'фет[аыуе]?|брынз\w*'),
 ('cheese', r'сыр[ауеы]?|сыром'), ('egg_white', r'белок\w*|белки|белков'), ('egg_yolk', r'желтк\w*|желток'),
 ('egg', r'яйц\w*|яиц|яичн\w*'),
 ('potato', r'картофел\w*|картошк\w*'), ('tomato', r'помидор\w*|томат[ыаову]*'),
 ('cucumber', r'огурец|огурц\w*'), ('carrot', r'морков\w*'), ('onion', r'лук[ауе]?|луком'),
 ('garlic', r'чеснок\w*'), ('beet', r'свекл\w*'), ('cabbage', r'капуст\w*'),
 ('broccoli', r'брокколи'), ('zucchini', r'кабач\w*|цукини'), ('eggplant', r'баклажан\w*'),
 ('pumpkin', r'тыкв\w*'), ('sweet_pepper', r'болгарск\w*\s+пер\w*|перец\s+болгарск\w*|перец\s+сладк\w*|сладк\w*\s+пер\w*'),
 ('pepper', r'перец|перц[аеу]|перцем'), ('corn', r'кукуруз[аыуе]|кукурузой'),
 ('peas', r'горошек|горошк\w*|горох\w*'), ('beans', r'фасол\w*'), ('lentil', r'чечевиц\w*'),
 ('mushroom', r'шампиньон\w*'), ('apple', r'яблок\w*'), ('banana', r'банан\w*'),
 ('lemon', r'лимон[аыуе]?|лимонов'), ('orange', r'апельсин\w*'), ('pear', r'груш\w*'),
 ('strawberry', r'клубник\w*'), ('raspberry', r'малин[аыуе]|малиной'), ('raisin', r'изюм\w*'),
 ('dill', r'укроп\w*'), ('parsley', r'петрушк\w*'), ('basil', r'базилик\w*'), ('spinach', r'шпинат\w*'),
 ('rice', r'рис[ауе]?|рисом'), ('buckwheat', r'гречк\w*|гречнев\w*'),
 ('oats', r'овсян\w*|геркулес\w*'), ('pasta', r'макарон\w*|спагетти|пенне|фузилли'),
 ('flour', r'мук[аиуе]|мукой'), ('semolina', r'манк\w*|манн\w*'), ('starch', r'крахмал\w*'),
 ('sugar', r'сахар[ауе]?|сахаром'), ('salt', r'соль|сол[ьи]'), ('water', r'вод[аыуе]|водой'),
 ('honey', r'мед[ауе]?|медом'), ('cocoa', r'какао'), ('chocolate', r'шоколад\w*'),
 ('walnut', r'грецк\w*\s+орех\w*|орех\w*\s+грецк\w*'), ('almond', r'миндал\w*'),
 ('mayonnaise', r'майонез\w*'), ('ketchup', r'кетчуп\w*'), ('mustard', r'горчиц\w*'),
 ('vinegar', r'уксус\w*'), ('soda', r'сод[аыуе]|содой'), ('yeast', r'дрожж\w*'),
 ('gelatin', r'желатин\w*'), ('bread', r'хлеб[ауе]?|хлебом|батон\w*'),
]
FOODS.sort(key=lambda item:0 if item[0] in {'egg_white','egg_yolk','egg','flour','starch','bread'} else 1)
FOOD_PATTERNS = [(key, re.compile(r'\b(?:'+pat+r')\b')) for key, pat in FOODS]
STATES = [('dried', r'сушен|сух(?:ой|ие|их|ая)|вялен'), ('canned', r'консерв|маринов|в собственном соку'),
          ('smoked', r'копчен'), ('salted', r'солен|малосол'), ('cooked', r'варен|отварн|жарен|запечен|тушен'),
          ('pickled', r'квашен'), ('processed', r'плавлен'), ('mince', r'фарш'), ('powder', r'порош'),
          ('wholegrain', r'цельнозернов'), ('rye', r'ржан'), ('corn_type', r'кукурузн'),
          ('rice_type', r'рисов'), ('potato_type', r'картофельн'), ('quail', r'перепелин'), ('korean',r'корейск')]


def text(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold().replace('ё', 'е')
    return ' '.join(re.sub(r'[^a-zа-я0-9%]+', ' ', value).split())


@lru_cache(maxsize=24000)
def identity(name):
    raw = text(name)
    # Explicit alternatives are not silently turned into a definite ingredient.
    if re.search(r'\bили\b', raw): return ('literal:'+raw, ())
    if re.search(r'\b(?:бульон|котлет|суп\b|сок\b|пюре|паштет|колбас|сосиск|наггетс|пельмен|пицц|салат|тесто|печенье|крекер|кекс|эссенц|тушенк|жир\b)', raw):
        return ('literal:'+raw, ())
    head = next((key for key, pat in FOOD_PATTERNS if pat.search(raw)), 'literal:'+raw)
    states = {key for key, pat in STATES if re.search(r'\b(?:'+pat+r')', raw)}
    if head in {'chicken', 'turkey', 'beef', 'pork', 'lamb', 'salmon', 'trout', 'cod', 'pollock', 'tuna'}:
        for key, pat in [('fillet',r'филе'),('breast',r'грудк'),('thigh',r'бедр'),('leg',r'голен'),('wing',r'крыл'),('liver',r'печен'),('heart',r'сердц'),('gizzard',r'желудк'),('tenderloin',r'вырезк'),('loin',r'корейк'),('ribs',r'ребр'),('shoulder',r'лопатк')]:
            if re.search(r'\b'+pat, raw): states.add(key)
        if 'fillet' in states: states.discard('breast')
    # Distinguish vanilla/sweetened/flavoured products from the unflavoured base.
    if re.search(r'\b(?:ваниль|клубнич|шоколадн|фруктов|сладк)', raw) and head in {'milk','yogurt','curd','cream','kefir'}:
        states.add('flavoured')
    if head=='egg' and re.search(r'\bшоколад',raw):return ('literal:'+raw, ())
    if head=='cheese' and re.search(r'\b(?:творожн|сливочн|плесен)',raw):return ('literal:'+raw, ())
    if head=='milk' and re.search(r'\b(?:соев|овсян|миндальн|рисов)',raw):
        return ('literal:'+raw, ())
    if head=='onion' and re.search(r'\bпорей',raw):states.add('leek')
    if head=='onion' and re.search(r'\bкрасн',raw):states.add('red_onion')
    if head=='cabbage' and re.search(r'\bкраснокоч',raw):states.add('red_cabbage')
    if head in {'tuna','salmon','herring','beans','peas','corn'} and re.search(r'\bв масле\b',raw):states.add('canned')
    if head=='yeast' and re.search(r'\bбыстродейств',raw):states.add('dried')
    if head=='beans' and re.search(r'\bстручков',raw):states.add('green_bean')
    if head == 'pepper':
        if re.search(r'\b(?:чили|остр|красн)',raw): states.add('chili')
        elif re.search(r'\bбел',raw): states.add('white')
    if head == 'vinegar':
        m=re.search(r'(\d+)\s*%', raw)
        if m: states.add('percent:'+m[1])
        if re.search(r'\bяблоч',raw):states.add('apple')
        if re.search(r'\bбальзам',raw):states.add('balsamic')
    return head, tuple(sorted(states))


def compatible(required, available):
    rh, rs = required; ah, ass = available
    # A generic vegetable oil accepts sunflower and olive oil, never butter.
    if rh != ah and not (rh == 'vegetable_oil' and ah in {'sunflower_oil','olive_oil'}): return False
    rs, ass = set(rs), set(ass)
    # All processing states must agree; a raw food cannot be supplied by its prepared version.
    strict = {key for key,_ in STATES} | {'flavoured','chili','white','apple','balsamic','leek','green_bean','red_onion','red_cabbage'}
    strict |= {'liver','heart','gizzard'}
    if (rs & strict) != (ass & strict): return False
    if {s for s in rs if s.startswith('percent:')} != {s for s in ass if s.startswith('percent:')}:return False
    return rs <= ass


def measure(unit):
    u = text(unit).replace(' ', '')
    table = {'г':('mass',1),'гр':('mass',1),'грамм':('mass',1),'граммов':('mass',1),'грамма':('mass',1),'g':('mass',1),
             'кг':('mass',1000),'килограмм':('mass',1000),'килограмма':('mass',1000),'килограммов':('mass',1000),'kg':('mass',1000),
             'мл':('volume',1),'ml':('volume',1),'миллилитров':('volume',1),'миллилитр':('volume',1),'миллилитра':('volume',1),
             'л':('volume',1000),'литр':('volume',1000),'литра':('volume',1000),'литров':('volume',1000),'l':('volume',1000),
             'шт':('pieces',1),'штук':('pieces',1),'штука':('pieces',1),'штуки':('pieces',1),
             'уп':('packs',1),'упак':('packs',1),'упаковка':('packs',1),'упаковки':('packs',1),'упаковок':('packs',1),
             'чл':('volume',5),'чайнаяложка':('volume',5),'чайныхложек':('volume',5),'чайнойложки':('volume',5),
             'стл':('volume',15),'столоваяложка':('volume',15),'столовыхложек':('volume',15),'столовойложки':('volume',15)}
    return table.get(u)


def compile_ingredient(ingredient):
    head, states = identity(ingredient['name'])
    m = measure(ingredient.get('unit',''))
    amount = catalog.positive(ingredient.get('amount'))
    original = ingredient.get('original','')
    return dict(name=ingredient['name'], head=head, states=states, amount=amount, measure=m,
                taste=bool(re.search(r'по\s+вкусу|щепот', original, re.I)), original=original,
                optional=bool(re.search(r'\b(?:по желанию|необязательно|для украшения)\b', ingredient.get('note',''), re.I)))


def stock_snapshot(owner_id, args):
    now = product_service.today()
    with db_session.create_session() as ses:
        storages = list(ses.scalars(select(StorageLocation).where(StorageLocation.owner_id==owner_id).order_by(StorageLocation.name_key)))
        ids = {str(s.id) for s in storages}
        selected = str(args.get('storage',''))
        # A foreign/nonexistent place selects nothing, never falls back to all stock.
        valid = not selected or selected in ids
        pairs = ses.execute(select(ProductGroup,ProductBatch).join(ProductBatch,ProductBatch.group_id==ProductGroup.id)
            .where(ProductGroup.owner_id==owner_id,ProductBatch.owner_id==owner_id,ProductBatch.quantity_micro>0)).all()
    batches=[]; expired=unknown=0
    for g,b in pairs:
        if not valid or (selected and str(g.storage_id)!=selected): continue
        if b.expiry_date and b.expiry_date < now:
            expired+=1;continue
        if not b.expiry_date:
            unknown+=1
            if args.get('dated')=='1': continue
        batches.append(dict(id=b.id,group_id=g.id,name=g.name,storage_id=g.storage_id,
            storage=next(s.name for s in storages if s.id==g.storage_id),identity=identity(g.name),
            dimension=g.dimension,quantity=Decimal(b.quantity_micro)/1000000,
            expiry=b.expiry_date,urgent=bool(b.expiry_date and b.expiry_date<=now+timedelta(days=3))))
    batches.sort(key=lambda b:(b['expiry'] is None,b['expiry'] or now+timedelta(days=99999),b['id']))
    return dict(batches=batches,storages=storages,selected=selected,valid=valid,expired=expired,unknown=unknown,
                pantry=[key for key in PANTRY if args.get('pantry_'+key)=='1'],dated=args.get('dated')=='1')


def display_quantity(amount,dimension):
    return product_service.quantity_text(int(amount*1000000),dimension)


def assess(ingredients, snapshot, base_servings=None, chosen=None):
    """Reserve within this recipe only, so repeated ingredient lines cannot double count stock."""
    pool=snapshot['batches'];match_cache=snapshot.setdefault('_matches',{});remaining={b['id']:b['quantity'] for b in pool}
    ratio=Decimal(chosen)/Decimal(base_servings) if chosen and base_servings else Decimal(1)
    results={};urgent=set();unknown=set()
    # Specific ingredients first; a generic chicken line must not consume the required fillet first.
    order=sorted(enumerate(ingredients),key=lambda pair:(pair[1]['optional'],pair[1]['head']=='vegetable_oil',-len(pair[1]['states']),not bool(pair[1]['amount'] and pair[1]['measure']),pair[0]))
    for pos,ing in order:
        ident=(ing['head'],tuple(ing['states']))
        if ident not in match_cache:match_cache[ident]=[b for b in pool if compatible(ident,b['identity'])]
        matches=match_cache[ident]
        required=Decimal(ing['amount'])*ratio if ing['amount'] else None
        unit=ing['measure'];need=required*Decimal(unit[1]) if required and unit else None
        row=dict(name=ing['name'],status='missing',label=STATUS_LABELS['missing'],available='',shortage='',sources=[],
                 original=ing['original'],optional=ing['optional'],urgent=False,unknown_date=False)
        if ing['head'] in snapshot['pantry'] and not ing['states']:
            row.update(status='pantry',label=STATUS_LABELS['pantry'])
        elif matches:
            by_dimension=[b for b in matches if unit and b['dimension']==unit[0]]
            sources=[]
            if need is not None:
                available=sum((remaining[b['id']] for b in by_dimension),Decimal(0))
                row['available']=display_quantity(available,unit[0])
                to_take=need
                for b in by_dimension:
                    take=min(to_take,remaining[b['id']])
                    if take>0:
                        remaining[b['id']]-=take;to_take-=take;sources.append(b)
                if available>=need:
                    row['status']='enough'
                elif by_dimension:
                    # Other units may cover the remainder, but require the user's measurement.
                    row['status']='check' if any(b['dimension']!=unit[0] for b in matches) else 'short'
                    row['shortage']=display_quantity(max(Decimal(0),need-available),unit[0])
                else: row['status']='check'
            else:
                sources=[b for b in matches if remaining[b['id']]>0]
                row['status']='enough' if ing['taste'] and sources else 'check'
            if row['status']=='check':
                # Explain incompatible/unspecified measures with actual stock, no density assumptions.
                sources=sources or [b for b in matches if remaining[b['id']]>0]
                if not row['available'] or not by_dimension:
                    totals=defaultdict(Decimal)
                    for b in matches:totals[b['dimension']]+=remaining[b['id']]
                    row['available']=' + '.join(display_quantity(v,d) for d,v in totals.items() if v>0)
            if not sources and row['status']=='check' and not row['available']:row['status']='short'
            for b in sources:
                if b['urgent']:urgent.add(b['id']);row['urgent']=True
                if b['expiry'] is None:unknown.add(b['id']);row['unknown_date']=True
                row['sources'].append(dict(group_id=b['group_id'],name=b['name'],storage=b['storage'],expiry=b['expiry']))
            row['label']=STATUS_LABELS[row['status']]
        results[pos]=row
    rows=[results[i] for i in range(len(ingredients))]
    required_rows=[r for r in rows if not r['optional']]
    missing=sum(r['status'] in {'missing','short'} for r in required_rows)
    check=sum(r['status']=='check' for r in required_rows)
    found=sum(r['status'] not in {'missing','short'} for r in required_rows)
    matched=sum(r['status']!='missing' for r in required_rows)
    total=len(required_rows)
    state='ready' if not missing and not check else 'check' if not missing else 'almost' if missing<=2 else 'missing'
    return dict(rows=rows,total=total,missing=missing,check=check,found=found,matched=matched,coverage=round(100*found/total) if total else 0,
                state=state,urgent=len(urgent),unknown=len(unknown),ready=state=='ready')


def signature(path):
    st=path.stat()
    return f'{INDEX_VERSION}:{st.st_mtime_ns}:{st.st_size}:{st.st_ino}'


def ensure_index():
    source=catalog.catalog_path()
    if not source.is_file():return None
    target=source.with_name(source.stem+'.matching.sqlite3')
    def current(sig):
        if not target.is_file():return False
        try:
            with closing(sqlite3.connect(target.as_uri()+'?mode=ro',uri=True)) as c:
                return c.execute('SELECT value FROM metadata WHERE key="signature"').fetchone()==(sig,)
        except sqlite3.Error:return False
    sig=signature(source)
    if current(sig):return target
    with _INDEX_LOCK:
        if current(sig):return target
        fd,tmp=tempfile.mkstemp(prefix='recipes-match-',suffix='.incoming',dir=source.parent);os.close(fd)
        try:
            with closing(sqlite3.connect(tmp)) as out,catalog.connection() as con:
                out.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
                  CREATE TABLE entries(id INTEGER PRIMARY KEY,public_id TEXT,title TEXT,category TEXT,minutes INTEGER,
                    servings INTEGER,complete INTEGER,image TEXT,ingredients TEXT,search_text TEXT);
                  CREATE TABLE terms(term TEXT,recipe_id INTEGER,PRIMARY KEY(term,recipe_id)) WITHOUT ROWID;''')
                for row in con.execute('SELECT id,public_id,title,category,minutes,servings,complete,data FROM recipes'):
                    data=json.loads(row['data']);ings=[compile_ingredient(i) for i in data['ingredients']]
                    out.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)',(*tuple(row)[:7],data.get('image',''),json.dumps(ings,ensure_ascii=False,separators=(',',':')),text(row['title']+' '+' '.join(i['name'] for i in ings))))
                    out.executemany('INSERT OR IGNORE INTO terms VALUES(?,?)',[(i['head'],row['id']) for i in ings])
                out.execute('INSERT INTO metadata VALUES(?,?)',('signature',sig))
                out.execute('CREATE INDEX ix_match_category ON entries(category)')
                out.commit()
            if signature(source)!=sig:raise catalog.CatalogError('База рецептов обновляется. Повторите подбор после завершения импорта.')
            os.replace(tmp,target)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return target


def filters(args):
    def choice(key,allowed,default):
        v=args.get(key,default);return v if v in allowed else default
    servings=str(args.get('servings','')).strip();error=''
    if servings:
        try:
            if not 1<=int(servings)<=100:raise ValueError
            servings=str(int(servings))
        except (ValueError,TypeError):servings='';error='Укажите от 1 до 100 порций.'
    return dict(q=catalog.clean(args.get('q',''),120),category=catalog.clean(args.get('category',''),120),
                time=choice('time',['','15','30','60','120'],''),availability=choice('availability',['all','ready','almost','check'],'all'),
                sort=choice('sort',['match','expiry','time'],'match'),servings=servings,urgent=args.get('urgent')=='1'),error


def browse(owner_id,args):
    f,error=filters(args);snap=stock_snapshot(owner_id,args)
    with catalog.connection() as base:
        status=catalog.info(base)
        categories=[tuple(r) for r in base.execute("SELECT category,count(*) FROM recipes WHERE category<>'' GROUP BY category ORDER BY category")] if base else []
    result=dict(filters=f,filter_error=error,snapshot=snap,catalog=status,categories=categories,recipes=[],total=0,page=1,pages=1,
                counts={'ready':0,'almost':0,'check':0},unscaled=0)
    if not status['count'] or not snap['valid'] or error:return result
    terms={b['identity'][0] for b in snap['batches']}|set(snap['pantry'])
    if terms & {'sunflower_oil','olive_oil'}:terms.add('vegetable_oil')
    if not terms:return result
    path=ensure_index()
    chosen=int(f['servings']) if f['servings'] else None
    total=0
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA temp_store=FILE')
        con.execute('CREATE TEMP TABLE stock_terms(term TEXT PRIMARY KEY)')
        con.executemany('INSERT INTO stock_terms VALUES(?)',[(term,) for term in terms])
        where=['e.complete=1','e.id IN (SELECT recipe_id FROM terms WHERE term IN (SELECT term FROM stock_terms))'];params=[]
        if f['category']:where.append('e.category=?');params.append(f['category'])
        if f['time']:where.append('e.minutes<=?');params.append(int(f['time']))
        # Name/ingredient search in the compact index, without reading instructions or limiting candidates.
        for token in re.findall(r'[^\W_]+',f['q'].casefold().replace('ё','е'))[:8]:
            where.append('e.search_text LIKE ?');params.append('%'+token+'%')
        # Scores live only in this request's TEMP database, not in the shared/public index.
        # This keeps pagination exact without retaining 150,000 recipe cards in Python memory.
        con.execute('CREATE TEMP TABLE scores(id INTEGER PRIMARY KEY,state_rank INTEGER,missing INTEGER,coverage INTEGER,checks INTEGER,urgent INTEGER,summary TEXT,needs TEXT)')
        pending=[]
        for row in con.execute('SELECT * FROM entries e WHERE '+' AND '.join(where),params):
            ingredients=json.loads(row['ingredients'])
            assessment=assess(ingredients,snap,row['servings'],chosen)
            if not assessment['matched']:continue
            if f['urgent'] and not assessment['urgent']:continue
            if chosen and not row['servings']:result['unscaled']+=1
            if assessment['state'] in result['counts']:result['counts'][assessment['state']]+=1
            if f['availability']!='all' and assessment['state']!=f['availability']:continue
            total+=1
            summary={k:v for k,v in assessment.items() if k!='rows'}
            needs=[r['name'] for r in assessment['rows'] if not r['optional'] and r['status'] in {'missing','short'}][:3]
            pending.append((row['id'],0 if assessment['ready'] else 1 if assessment['state']=='check' else 2,
                assessment['missing'],assessment['coverage'],assessment['check'],assessment['urgent'],
                json.dumps(summary,separators=(',',':')),json.dumps(needs,ensure_ascii=False)))
            if len(pending)>=500:
                con.executemany('INSERT INTO scores VALUES(?,?,?,?,?,?,?,?)',pending);pending=[]
        if pending:con.executemany('INSERT INTO scores VALUES(?,?,?,?,?,?,?,?)',pending)
        pages=max(1,math.ceil(total/24))
        try:page=max(1,min(pages,int(args.get('page','1'))))
        except (TypeError,ValueError):page=1
        readiness='s.state_rank,s.missing,s.coverage DESC,s.checks'
        tail='e.minutes IS NULL,e.minutes,e.title,e.public_id'
        order={'match':readiness+',s.urgent DESC,'+tail,
               'expiry':'s.urgent DESC,'+readiness+','+tail,
               'time':'e.minutes IS NULL,e.minutes,'+readiness+',s.urgent DESC,e.title,e.public_id'}[f['sort']]
        cards=[]
        for row in con.execute('SELECT e.*,s.summary,s.needs FROM scores s JOIN entries e ON e.id=s.id ORDER BY '+order+' LIMIT 24 OFFSET ?',((page-1)*24,)):
            cards.append(dict(key=row['public_id'],title=row['title'],category=row['category'],minutes=row['minutes'],
                servings=row['servings'],image=row['image'],complete=True,ingredients=[dict(name=i['name']) for i in json.loads(row['ingredients'])],
                needs=json.loads(row['needs']),match=json.loads(row['summary'])))
    result.update(total=total,page=page,pages=pages,recipes=cards)
    return result


def recipe_assessment(recipe,snapshot):
    return assess([compile_ingredient(i) for i in recipe['ingredients']],snapshot,recipe['servings'],recipe['chosen_servings'])
