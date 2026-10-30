"""Owner-scoped nutrition operations. Missing nutrients never become zero."""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from sqlalchemy import select, text
from data import db_session
from data.nutrition import NutritionProfile, NutritionEntry, NutritionMeasure, NutritionMenu
from data.product import ProductBatch, ProductGroup, ProductMutation
from data.activity import ActivityEvent
from services import product_service as stock, product_operations as operations
from services import recipe_library as library, recipe_catalog as catalog, recipe_matching as matching

MEALS = {'breakfast':'Завтрак','lunch':'Обед','dinner':'Ужин','snack':'Перекус'}
NAMES = {'calories':'Ккал','protein':'Белки','fat':'Жиры','carbs':'Углеводы'}
UNITS = {'g':'г','ml':'мл','pcs':'шт.','portion':'порц.'}
ACTIVITIES = {'1.2':'Низкая','1.375':'Небольшая','1.55':'Средняя','1.725':'Высокая'}
GOALS = {'maintain':'Поддержание веса','lose':'Снижение веса','gain':'Набор массы'}
DEFAULTS = dict(mode='manual', goal='maintain', calories='', protein='', fat='', carbs='', water='2000',
                sex='', age='', height='', weight='', activity='1.375', excluded='', preferences='', target_weight='')


def number(raw, label, minimum=0, maximum=100000):
    try:
        value=Decimal(str(raw).strip().replace(',','.'))
        if not value.is_finite() or not minimum<=value<=maximum or abs(value.as_tuple().exponent)>6:raise ValueError
        return value
    except (InvalidOperation,ValueError,TypeError):raise ValueError(f'{label}: укажите число от {minimum} до {maximum}.')


def shown(value):return format(Decimal(str(value)).quantize(Decimal('.1'),rounding=ROUND_HALF_UP).normalize(),'f')


def day_value(raw, *, future=False):
    try:
        value=date.fromisoformat(str(raw))
        if value<date(2000,1,1) or value>stock.today()+timedelta(days=366 if future else 0):raise ValueError
        return value
    except (ValueError,TypeError):raise ValueError('Выберите дату с 2000 года'+(' не позднее сегодняшней.' if not future else ' в пределах следующего года.'))


def token(raw):
    if not re.fullmatch('[a-f0-9]{48}',str(raw)):raise ValueError('Форма устарела. Откройте её заново.')
    return raw


def ident(raw):
    if not re.fullmatch('[0-9]{1,18}',str(raw)):raise ValueError('Запись не найдена.')
    return int(raw)


def event(s,owner,action,name):
    s.add(ActivityEvent(owner_id=owner,category='nutrition',action='nutrition_'+action,after={'name':name}))


def profile(owner):
    with db_session.create_session() as s:
        row=s.get(NutritionProfile,owner)
        return DEFAULTS|((row.values or {}) if row else {}), row.revision if row else 0


def save_profile(owner,form):
    values={k:str(form.get(k,'')).strip() for k in DEFAULTS}
    if values['mode'] not in ['manual','auto'] or values['goal'] not in GOALS:raise ValueError('Выберите способ расчёта и цель.')
    if values['sex'] not in ['', 'male','female'] or values['activity'] not in ACTIVITIES:raise ValueError('Проверьте пол и активность.')
    if len(values['excluded'])>500 or len(values['preferences'])>500:raise ValueError('Предпочтения и исключения — до 500 символов.')
    values['water']=shown(number(values['water'],'Вода',0,6000))
    for field,lo,hi in [('age',1,120),('height',80,250),('weight',20,400),('target_weight',20,400)]:
        if values[field]:values[field]=shown(number(values[field],field,lo,hi))
    if values['mode']=='auto':
        if values['sex'] not in ['female','male'] or values['activity'] not in ACTIVITIES:raise ValueError('Укажите пол и активность для расчёта.')
        age=number(values['age'],'Возраст',18,100);height=number(values['height'],'Рост',100,250);weight=number(values['weight'],'Вес',30,400)
        base=10*weight+Decimal('6.25')*height-5*age+(5 if values['sex']=='male' else -161)
        factor={'lose':Decimal('.9'),'gain':Decimal('1.1'),'maintain':Decimal(1)}[values['goal']]
        cal=(base*Decimal(values['activity'])*factor).quantize(Decimal('1'))
        if not 1200<=cal<=6000:raise ValueError('Расчёт вне диапазона 1200–6000 ккал. Укажите согласованные с вами нормы вручную.')
        values.update(calories=str(cal),protein=shown(cal*Decimal('.2')/4),fat=shown(cal*Decimal('.3')/9),carbs=shown(cal*Decimal('.5')/4))
    else:
        for k in NAMES:
            values[k]=shown(number(values[k],NAMES[k],1 if k=='calories' else 0,10000 if k=='calories' else 1000)) if values[k] else ''
    with db_session.create_session() as s:
        s.execute(text('BEGIN IMMEDIATE'));row=s.get(NutritionProfile,owner)
        if str(row.revision if row else 0)!=str(form.get('revision','')):raise ValueError('Цели уже изменились в другой вкладке. Обновите страницу.')
        if row:row.values=values;row.revision+=1
        else:s.add(NutritionProfile(owner_id=owner,values=values))
        event(s,owner,'goals','Цели питания обновлены');s.commit()


def stock_options(owner,q=''):
    with db_session.create_session() as s:
        query=select(ProductBatch,ProductGroup).join(ProductGroup,ProductGroup.id==ProductBatch.group_id).where(
            ProductBatch.owner_id==owner,ProductGroup.owner_id==owner,ProductBatch.quantity_micro>0,
            (ProductBatch.expiry_date.is_(None)) | (ProductBatch.expiry_date>=stock.today()))
        if q:query=query.where(ProductGroup.search_key.contains(stock.clean(q).casefold(),autoescape=True))
        rows=s.execute(query.order_by(ProductGroup.name,ProductBatch.expiry_date.is_(None),ProductBatch.expiry_date,ProductBatch.id).limit(50)).all()
        return [dict(id=b.id,name=g.name,brand=g.brand,quantity=b.entered_quantity,unit=stock.UNITS[b.unit][0],date=b.expiry_date) for b,g in rows]


def source_values(owner,kind,key):
    if kind=='manual':return dict(name='',quantity='100',unit='g',basis='100g',**{k:'' for k in NAMES}),{}
    if kind=='stock':
        with db_session.create_session() as s:
            b,g,p=operations.owned(s,owner,ident(key))
            if b.quantity_micro<=0 or b.expiry_date and b.expiry_date<stock.today():raise ValueError('У продукта нет остатка или его срок истёк.')
            d=g.details;unit={'mass':'g','volume':'ml','pieces':'pcs','packs':'portion'}[g.dimension]
            values=dict(name=g.name,quantity='100' if unit in ['g','ml'] else '1',unit=unit,basis=d.get('nutrition_basis','100g'),**{k:str(d[k]) if d.get(k) is not None else '' for k in NAMES})
            if values['basis']=='portion':values['basis']='serving'
            if values['basis'] not in ['100g','100ml','serving']:values['basis']='total'
            portion=str(d.get('portion','') or '')
            match=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(г|мл|g|ml)\s*',portion,re.I)
            values['basis_quantity']=match.group(1).replace(',','.') if match and ((unit=='g' and match.group(2).lower() in ['г','g']) or (unit=='ml' and match.group(2).lower() in ['мл','ml'])) else ''
            return values,dict(kind='stock',key=str(b.id),label='Из запасов',stock_name=g.name)
    if kind=='recipe':
        if not re.fullmatch('[a-f0-9]{24}',str(key)):raise ValueError('Рецепт не найден.')
        r=library.recipe(owner,key)
        if not r:raise ValueError('Рецепт не найден.')
        personal=library.state(owner,key);n=library.nutrition(owner,r,personal['nutrition'])
        vals=n['per_portion'] if n['complete'] and n['per_portion'] else None
        if vals is None and n['complete'] and not r.get('servings'):vals=n['totals']
        return dict(name=r['title'],quantity='1',unit='portion',basis='serving',**{k:str(vals[k]).replace(',','.') if vals else '' for k in NAMES}),dict(kind='recipe',key=key,label='На порцию' if r.get('servings') else 'На весь исходный рецепт',estimated=True)
    raise ValueError('Выберите способ добавления.')


def entry_values(form):
    values={k:str(form.get(k,'')).strip() for k in ['name','quantity','unit','basis','basis_quantity','note','meal','day',*NAMES]}
    if not 1<=len(values['name'])<=160:raise ValueError('Название — от 1 до 160 символов.')
    if len(values['note'])>1000:raise ValueError('Заметка — до 1000 символов.')
    if values['meal'] not in MEALS or values['unit'] not in UNITS:raise ValueError('Выберите приём пищи и единицу.')
    day=day_value(values['day']);quantity=number(values['quantity'],'Количество',Decimal('.001'),100000)
    basis=values['basis']
    if basis not in ['100g','100ml','serving','total']:raise ValueError('Выберите основание КБЖУ.')
    if basis=='100g' and values['unit']!='g' or basis=='100ml' and values['unit']!='ml':raise ValueError('Для КБЖУ на 100 г/мл количество нужно указать в той же единице.')
    if basis=='serving' and values['unit'] in ['g','ml']:
        denominator=number(values['basis_quantity'],'Масса или объём одной порции',Decimal('.001'),100000)
    else:denominator=Decimal(100) if basis in ['100g','100ml'] else Decimal(1)
    if basis in ['100g','100ml']:
        for k in NAMES:
            if values[k]:number(values[k],NAMES[k],0,1000 if k=='calories' else 100)
        if all(values[k] for k in ['protein','fat','carbs']) and sum(number(values[k],NAMES[k]) for k in ['protein','fat','carbs'])>Decimal('100.5'):raise ValueError('Сумма БЖУ на 100 г/мл превышает 100 г. Проверьте основание расчёта.')
    ratio=Decimal(1) if basis=='total' else quantity/denominator
    nutrients={k:shown(number(values[k],NAMES[k],0,10000 if k=='calories' else 1000)*ratio) if values[k] else None for k in NAMES}
    return values,day,nutrients


def save_entry(owner,form,entry_id=None):
    values,day,nutrients=entry_values(form);key=token(form.get('submission_key',''))
    kind=form.get('source_kind','manual');source_key=form.get('source_key','')
    fingerprint=operations.digest(dict(values=values,source_kind=kind,source_key=source_key,deduct=form.get('deduct','')))
    if not entry_id:
        with db_session.create_session() as lookup:
            previous=lookup.scalar(select(NutritionEntry).where(NutritionEntry.owner_id==owner,NutritionEntry.submission_key==key))
            if previous:
                if previous.source.get('submission_hash')!=fingerprint:raise ValueError('Эта форма уже отправлена с другими значениями. Откройте новую запись.')
                return previous.id
    _,source=source_values(owner,kind,source_key) if not entry_id else ({},{})
    with db_session.create_session() as s:
        s.execute(text('BEGIN IMMEDIATE'))
        if entry_id:
            row=s.get(NutritionEntry,entry_id)
            if not row or row.owner_id!=owner or row.deleted:raise ValueError('Запись не найдена.')
            if str(row.revision)!=str(form.get('revision')):raise ValueError('Запись уже изменена. Откройте её заново.')
            if row.stock_deducted and (row.quantity!=shown(number(values['quantity'],'Количество')) or row.unit!=values['unit'] or row.name!=values['name']):raise ValueError('Количество списано из запасов. Для исправления количества удалите запись и добавьте заново без повторного списания; остаток исправьте в продукте.')
            row.revision+=1
        else:
            old=s.scalar(select(NutritionEntry).where(NutritionEntry.owner_id==owner,NutritionEntry.submission_key==key))
            if old:
                if old.source.get('submission_hash')!=fingerprint:raise ValueError('Эта форма уже отправлена с другими значениями. Откройте новую запись.')
                return old.id
            if s.scalar(select(NutritionEntry.id).where(NutritionEntry.owner_id==owner,NutritionEntry.day==day,NutritionEntry.deleted.is_(False)).offset(499).limit(1)):raise ValueError('На эту дату уже есть 500 записей.')
            row=NutritionEntry(owner_id=owner,submission_key=key,source=source,stock_deducted=False)
            if form.get('deduct')=='yes':
                if kind!='stock':raise ValueError('Списывать можно только выбранное поступление из запасов.')
                b,g,p=operations.owned(s,owner,ident(source_key));before=operations.snapshot(b,g,p)
                if b.expiry_date and b.expiry_date<stock.today():raise ValueError('Срок продукта истёк. Выберите другое поступление.')
                mapping={'g':'mass','ml':'volume','pcs':'pieces','portion':'packs'}
                if mapping[values['unit']]!=g.dimension:raise ValueError('Единица не соответствует поступлению из запасов.')
                micro=int(number(values['quantity'],'Количество')*1000000)
                if micro<=0 or micro>b.quantity_micro:raise ValueError('Количество превышает остаток продукта.')
                cost=None if b.price_kopecks is None else int((Decimal(b.price_kopecks)*micro/b.quantity_micro).quantize(Decimal(1),rounding=ROUND_HALF_UP))
                operations.update_amount(b,b.quantity_micro-micro)
                if cost is not None:b.price_kopecks-=cost
                after=operations.snapshot(b,g,p)|dict(operation_quantity_micro=micro,operation_cost_kopecks=cost,operation_note='Запись в дневник питания',reason='')
                s.add(ProductMutation(owner_id=owner,batch_id=b.id,action='use',submission_key=key,payload_hash=operations.digest({'nutrition':key}),before=before,after=after,result_group_id=g.id))
                row.stock_deducted=True
        if not entry_id:s.add(row)
        row.day=day;row.meal=values['meal'];row.name=values['name'];row.quantity=shown(number(values['quantity'],'Количество'));row.unit=values['unit'];row.nutrients=nutrients;row.note=values['note']
        if not entry_id:row.source=dict(source,submission_hash=fingerprint,basis=values['basis'],basis_quantity=values['basis_quantity'],input_values={k:values[k] for k in NAMES})
        else:row.source=dict(row.source,basis=values['basis'],basis_quantity=values['basis_quantity'],input_values={k:values[k] for k in NAMES})
        event(s,owner,'edit' if entry_id else 'add',row.name);s.flush();result=row.id;s.commit();return result


def remove_entry(owner,entry_id,revision):
    with db_session.create_session() as s:
        s.execute(text('BEGIN IMMEDIATE'));row=s.get(NutritionEntry,entry_id)
        if not row or row.owner_id!=owner:raise ValueError('Запись не найдена.')
        if row.deleted:return
        if str(row.revision)!=str(revision):raise ValueError('Запись уже изменена. Обновите дневник.')
        row.deleted=True;row.revision+=1;event(s,owner,'delete',row.name);s.commit()


def measure(owner,form):
    kind=form.get('kind');day=day_value(form.get('day'));key=token(form.get('submission_key'))
    if kind not in ['water','weight']:raise ValueError('Неизвестное измерение.')
    value=number(form.get('value'),'Вода, мл' if kind=='water' else 'Вес, кг',1 if kind=='water' else 20,3000 if kind=='water' else 400)
    with db_session.create_session() as s:
        s.execute(text('BEGIN IMMEDIATE'))
        if s.scalar(select(NutritionMeasure.id).where(NutritionMeasure.owner_id==owner,NutritionMeasure.submission_key==key)):return
        rows=list(s.scalars(select(NutritionMeasure).where(NutritionMeasure.owner_id==owner,NutritionMeasure.day==day,NutritionMeasure.kind==kind)))
        if kind=='water' and sum(r.value for r in rows)+float(value)>10000:raise ValueError('На эту дату уже записано больше 10 л воды. Проверьте объём.')
        if kind=='weight':
            for r in rows:s.delete(r)
        s.add(NutritionMeasure(owner_id=owner,day=day,kind=kind,value=float(value),submission_key=key));event(s,owner,kind,'Вода' if kind=='water' else 'Вес');s.commit()


def remove_measure(owner,key):
    with db_session.create_session() as s:
        row=s.get(NutritionMeasure,ident(key))
        if not row or row.owner_id!=owner:raise ValueError('Запись не найдена.')
        event(s,owner,'measure_delete','Измерение удалено');s.delete(row);s.commit()


def summary(owner,day):
    start=day-timedelta(days=6);goals,revision=profile(owner)
    with db_session.create_session() as s:
        entries=list(s.scalars(select(NutritionEntry).where(NutritionEntry.owner_id==owner,NutritionEntry.day.between(start,day),NutritionEntry.deleted.is_(False)).order_by(NutritionEntry.id)))
        measures=list(s.scalars(select(NutritionMeasure).where(NutritionMeasure.owner_id==owner,NutritionMeasure.day.between(start,day)).order_by(NutritionMeasure.id)))
        weights=list(s.scalars(select(NutritionMeasure).where(NutritionMeasure.owner_id==owner,NutritionMeasure.kind=='weight',NutritionMeasure.day<=day).order_by(NutritionMeasure.day.desc()).limit(60)))
    days=[]
    for offset in range(7):
        d=start+timedelta(days=offset);items=[r for r in entries if r.day==d]
        totals={k:shown(sum(Decimal(r.nutrients[k]) for r in items if r.nutrients.get(k) is not None)) for k in NAMES}
        missing={k:sum(r.nutrients.get(k) is None for r in items) for k in NAMES}
        days.append(dict(day=d,entries=items,totals=totals,missing=missing,water=shown(sum(r.value for r in measures if r.day==d and r.kind=='water'))))
    current=days[-1];current.update(goals=goals,profile_revision=revision,days=days,weights=weights,measures=[r for r in measures if r.day==day],meals={k:[r for r in current['entries'] if r.meal==k] for k in MEALS})
    current['progress']={k:min(100,float(Decimal(current['totals'][k])/Decimal(goals[k])*100)) if goals[k] and Decimal(goals[k])>0 else 0 for k in NAMES}
    return current


def save_menu(owner,form):
    day=day_value(form.get('day'),future=True);meal=form.get('meal')
    if meal not in MEALS:raise ValueError('Выберите приём пищи.')
    portions=number(form.get('portions','1'),'Порции',Decimal('.1'),20)
    key=form.get('recipe_key','');r=library.recipe(owner,key) if re.fullmatch('[a-f0-9]{24}',key) else None
    if not r:raise ValueError('Выберите рецепт из результатов поиска.')
    goals,_=profile(owner);check_exclusions(r,goals['excluded'])
    n=library.nutrition(owner,r,library.state(owner,key)['nutrition'])
    nutrients=(n['per_portion'] or n['totals']) if n['complete'] else None
    with db_session.create_session() as s:
        s.execute(text('BEGIN IMMEDIATE'));row=s.scalar(select(NutritionMenu).where(NutritionMenu.owner_id==owner,NutritionMenu.day==day,NutritionMenu.meal==meal))
        if str(row.revision if row else 0)!=str(form.get('revision','0')):raise ValueError('В этом приёме пищи уже есть план. Обновите страницу перед заменой.')
        snapshot={'title':r['title'],'servings_known':bool(r.get('servings')),'nutrients':nutrients}
        if row:row.recipe_key=key;row.snapshot=snapshot;row.portions=float(portions);row.revision+=1
        else:s.add(NutritionMenu(owner_id=owner,day=day,meal=meal,recipe_key=key,snapshot=snapshot,portions=float(portions)))
        event(s,owner,'menu',r['title']);s.commit()


def exclusions(raw):return [x.strip().casefold().replace('ё','е') for x in re.split('[,;\n]',raw) if x.strip()]

def check_exclusions(recipe,raw):
    hay=' '.join(i['name'] for i in recipe['ingredients']).casefold().replace('ё','е')
    excluded=exclusions(raw)
    identities=[matching.identity(i['name']) for i in recipe['ingredients']]
    if any(x in hay or any(matching.compatible(matching.identity(x),ident) for ident in identities) for x in excluded):raise ValueError('В составе есть исключённый ингредиент. Проверьте цели питания.')


def menu_week(owner,start):
    with db_session.create_session() as s:
        rows=list(s.scalars(select(NutritionMenu).where(NutritionMenu.owner_id==owner,NutritionMenu.day.between(start,start+timedelta(days=6)))))
    days=[]
    for offset in range(7):
        day=start+timedelta(days=offset);slots={meal:next((r for r in rows if r.day==day and r.meal==meal),None) for meal in MEALS}
        total={k:Decimal(0) for k in NAMES};missing=0;count=0
        for row in slots.values():
            if not row:continue
            count+=1;values=row.snapshot.get('nutrients')
            if values is None:missing+=1;continue
            for key in NAMES:total[key]+=Decimal(values[key].replace(',','.'))*Decimal(str(row.portions))
        days.append(dict(day=day,slots=slots,totals={k:shown(v) for k,v in total.items()},missing=missing,count=count))
    return days


def auto_menu(owner,form):
    """Bounded local selection, using only complete per-portion nutrition."""
    start=day_value(form.get('day'),future=True)
    days=int(number(form.get('days','1'),'Дни',1,7))
    goals,_=profile(owner)
    if not goals['calories']:raise ValueError('Сначала укажите дневную норму в «Мои цели».')
    candidates=[];seen=set()
    snapshot=matching.stock_snapshot(owner,{}) if form.get('use_stock')=='1' else None
    saved=library.favorites(owner,{'kind':'all'})['recipes']
    found=catalog.browse({'content':'complete','q':str(form.get('q',''))[:120]})['recipes']
    for card in [*saved,*found]:
        key=card['key']
        if key in seen:continue
        seen.add(key);r=library.recipe(owner,key)
        if not r or not r.get('servings'):continue
        try:check_exclusions(r,goals['excluded'])
        except ValueError:continue
        if snapshot is not None:
            assessment=matching.recipe_assessment(r,snapshot)
            if assessment['state']!='ready':continue
        n=library.nutrition(owner,r,library.state(owner,key)['nutrition'])
        if not n['complete'] or not n['per_portion']:continue
        cal=Decimal(n['per_portion']['calories'].replace(',','.'))
        if cal<=0:continue
        candidates.append((r,cal,n['per_portion']))
        if len(candidates)>=32:break
    if not candidates:raise ValueError('Нет подходящих рецептов с полным КБЖУ и известными порциями. Добавьте такие блюда в избранное или уточните их состав; меню можно заполнить вручную.')
    portions_by_meal={'breakfast':Decimal('.25'),'lunch':Decimal('.35'),'dinner':Decimal('.3'),'snack':Decimal('.1')}
    count=0;used={}
    with db_session.create_session() as s:
        s.execute(text('BEGIN IMMEDIATE'))
        for offset in range(days):
            day=start+timedelta(days=offset)
            for meal,share in portions_by_meal.items():
                if s.scalar(select(NutritionMenu.id).where(NutritionMenu.owner_id==owner,NutritionMenu.day==day,NutritionMenu.meal==meal)):continue
                target=Decimal(goals['calories'])*share
                r,cal,nutrients=min(candidates,key=lambda pair:abs(pair[1]-target)+Decimal(used.get(pair[0]['key'],0))*target)
                portion=max(Decimal('.5'),min(Decimal(3),(target/cal*2).quantize(Decimal(1),rounding=ROUND_HALF_UP)/2))
                s.add(NutritionMenu(owner_id=owner,day=day,meal=meal,recipe_key=r['key'],snapshot={'title':r['title'],'servings_known':True,'nutrients':nutrients},portions=float(portion)))
                used[r['key']]=used.get(r['key'],0)+1;count+=1
        event(s,owner,'menu','Подобрано приёмов пищи: '+str(count));s.commit()
    return count


def search_recipes(owner,q=''):
    saved=library.favorites(owner,{'q':q})['recipes']
    catalog_rows=catalog.browse({'q':q,'content':'complete'})['recipes']
    combined={}
    for row in [*saved,*catalog_rows]:
        if row['key'] not in combined:combined[row['key']]=row
    return list(combined.values())[:24]
