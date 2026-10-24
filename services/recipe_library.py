"""Private recipe state and reproducible, explicitly estimated nutrition."""
import copy
import time
from decimal import Decimal,InvalidOperation
from sqlalchemy import select,func,or_
from data import db_session
from data.recipe import RecipeState
from data.product import ProductGroup
from data.activity import ActivityEvent
from services import recipe_catalog as catalog,recipe_matching as matching

NUTRIENTS={'calories':'Ккал','protein':'Белки, г','fat':'Жиры, г','carbs':'Углеводы, г'}
# Rounded generic raw-food estimates, not a replacement for the actual product label.
REFERENCE={
 'potato':(77,2,.4,16.3),'carrot':(35,1.3,.1,6.9),'onion':(40,1.4,.2,8.2),'tomato':(20,1.1,.2,3.7),
 'cucumber':(15,.8,.1,2.8),'cabbage':(28,1.8,.1,4.7),'beet':(43,1.5,.1,8.8),'zucchini':(24,.6,.3,4.6),
 'rice':(344,6.7,.7,78.9),'buckwheat':(308,12.6,3.3,57.1),'oats':(366,12.3,6.1,59.5),'pasta':(350,12,1.5,72),
 'flour':(334,10.3,1.1,70.6),'sugar':(399,0,0,99.8),'salt':(0,0,0,0),'water':(0,0,0,0),
 'chicken':(120,22,3,0),'turkey':(114,23.7,1.5,0),'beef':(187,18.9,12.4,0),'pork':(259,16,21.6,0),
 'egg':(157,12.7,11.5,.7),'curd':(121,17.2,5,1.8),'sour_cream':(206,2.5,20,3.4),
 'milk':(52,2.8,2.5,4.7),'kefir':(53,2.9,2.5,4),'cream':(206,2.5,20,4),
 'butter':(748,.5,82.5,.8),'vegetable_oil':(899,0,99.9,0),'sunflower_oil':(899,0,99.9,0),'olive_oil':(898,0,99.8,0),
 'apple':(47,.4,.4,9.8),'banana':(96,1.5,.5,21),'lemon':(34,.9,.1,3),'garlic':(149,6.5,.5,29.9),
 'mushroom':(27,4.3,1,1),'honey':(329,.8,0,81.5),'cheese':(350,25,27,0),
}


def state(owner,key):
    with db_session.create_session() as s:
        row=s.get(RecipeState,(owner,key))
        return dict(favorite=row.favorite,note=row.note,checked=row.checked,nutrition=row.nutrition,generated=row.generated) if row else dict(favorite=False,note='',checked=[],nutrition={},generated=False)


def recipe(owner,key,servings=None):
    result=catalog.detail(key,servings)
    if result is not None:return result
    with db_session.create_session() as s:
        row=s.get(RecipeState,(owner,key))
        if not row or not row.snapshot:return None
        result=copy.deepcopy(row.snapshot)
    base=result.get('servings');chosen=base;error=''
    if servings:
        try:
            chosen=int(servings)
            if not base or not 1<=chosen<=100:raise ValueError
        except (ValueError,TypeError):chosen=base;error='Укажите от 1 до 100 порций.'
    for i in result['ingredients']:
        i['display']=i.get('original') or 'Количество не указано';i['scaled']=False
        if base and chosen and i.get('amount'):
            n=Decimal(i['amount'])*chosen/base
            i['display']=format(n.quantize(Decimal('.001')).normalize(),'f').replace('.',',')+(' '+i['unit'] if i.get('unit') else '')
            i['scaled']=chosen!=base
    result.update(chosen_servings=chosen,portion_error=error)
    return result


def change(owner,key,recipe,action,form):
    with db_session.create_session() as s:
        row=s.get(RecipeState,(owner,key))
        if row is None:row=RecipeState(owner_id=owner,recipe_key=key,snapshot=recipe,favorite=False,generated=False,note='',checked=[],nutrition={});s.add(row)
        if action=='favorite':row.favorite=form.get('value')=='1'
        elif action=='note':
            note=form.get('note','').strip()
            if len(note)>2000:raise ValueError('Заметка — не более 2000 символов.')
            row.note=note
        elif action=='steps':
            allowed=set(range(len(recipe['steps'])))
            values=form.getlist('checked') if hasattr(form,'getlist') else form.get('checked',[])
            row.checked=sorted({int(v) for v in values if str(v).isdigit() and int(v) in allowed})
        elif action=='steps_reset':row.checked=[]
        elif action=='nutrition_reset':row.nutrition={}
        elif action=='nutrition':
            edits={}
            for n,_ in enumerate(recipe['ingredients']):
                item={}
                for k in ['quantity',*NUTRIENTS]:
                    raw=form.get(f'n{n}_{k}','').strip().replace(',','.')
                    if raw:
                        try:
                            value=Decimal(raw)
                            maximum=100 if k in ['protein','fat','carbs'] else 1000000 if k=='quantity' else 1000
                            if not value.is_finite() or not 0<=value<=maximum:raise ValueError
                        except (InvalidOperation,ValueError):raise ValueError('Проверьте числовые значения КБЖУ и количества.')
                        item[k]=str(value)
                basis=form.get(f'n{n}_basis','100g')
                if basis not in ['100g','100ml']:raise ValueError('Выберите основание расчёта.')
                if all(k in item for k in ['protein','fat','carbs']) and sum(Decimal(item[k]) for k in ['protein','fat','carbs'])>Decimal('100.5'):
                    raise ValueError('Сумма белков, жиров и углеводов на 100 г/мл превышает 100 г.')
                item['basis']=basis
                if len(item)>1:edits[str(n)]=item
            row.nutrition=edits
        else:raise ValueError('Неизвестное действие.')
        row.updated_at=time.time()
        s.add(ActivityEvent(owner_id=owner,category='recipes',action='recipe_'+action,after={'name':recipe['title']}));s.commit()


def favorites(owner,args):
    q=catalog.clean(args.get('q',''),120).casefold();kind=args.get('kind','all')
    try:page=max(1,int(args.get('page',1)))
    except (ValueError,TypeError):page=1
    with db_session.create_session() as s:
        conditions=[RecipeState.owner_id==owner,RecipeState.favorite.is_(True)]
        if kind=='ai':conditions.append(RecipeState.generated.is_(True))
        elif kind=='catalog':conditions.append(RecipeState.generated.is_(False))
        if q:conditions.append(or_(func.unicode_casefold(RecipeState.snapshot['title'].as_string()).contains(q,autoescape=True),func.unicode_casefold(RecipeState.note).contains(q,autoescape=True)))
        total=s.scalar(select(func.count()).select_from(RecipeState).where(*conditions));pages=max(1,(total+23)//24);page=min(page,pages)
        rows=s.scalars(select(RecipeState).where(*conditions).order_by(RecipeState.updated_at.desc(),RecipeState.recipe_key).offset((page-1)*24).limit(24))
        cards=[]
        for row in rows:
            r=copy.deepcopy(row.snapshot);r['key']=row.recipe_key;r['generated']=row.generated;cards.append(r)
    return dict(recipes=cards,total=total,pages=pages,page=page,q=q,kind=kind)


def nutrition(owner,recipe,edits):
    with db_session.create_session() as s:
        groups=list(s.scalars(select(ProductGroup).where(ProductGroup.owner_id==owner)))
    rows=[];totals={k:Decimal(0) for k in NUTRIENTS};known=0
    base=recipe.get('servings');chosen=recipe.get('chosen_servings') or base
    ratio=Decimal(chosen)/base if base and chosen else Decimal(1)
    for n,i in enumerate(recipe['ingredients']):
        ident=matching.identity(i['name']);head,states=ident;m=matching.measure(i.get('unit',''));values={};source='';basis='100g'
        qty=Decimal(i['amount'])*Decimal(m[1]) if i.get('amount') and m else None
        if m and m[0]=='volume':basis='100ml'
        if m and m[0] not in ['mass','volume']:qty=None
        labels={}
        for g in groups:
            if matching.compatible(ident,matching.identity(g.name)):
                d=g.details
                if d.get('nutrition_basis') in ['100g','100ml'] and all(d.get(k) not in [None,''] for k in NUTRIENTS):
                    labels[json_key(d)]={k:str(d[k]) for k in NUTRIENTS}|{'basis':d['nutrition_basis']}
        if len(labels)==1:
            label=next(iter(labels.values()));values={k:label[k] for k in NUTRIENTS};source='Из этикетки ваших продуктов'
            label_basis=label['basis']
            if label_basis!=basis:qty=None
            basis=label_basis
        elif not states and head in REFERENCE:
            values={k:str(v) for k,v in zip(NUTRIENTS,REFERENCE[head])};source='Усреднённая справочная оценка'
            expected='100ml' if head in ['milk','cream','kefir','water'] else '100g'
            if expected!=basis:qty=None
            basis=expected
        edit=edits.get(str(n),{})
        if edit:
            values={k:edit.get(k,values.get(k,'')) for k in NUTRIENTS}
            if edit.get('basis',basis)!=basis and 'quantity' not in edit:qty=None
            basis=edit.get('basis',basis);source='Ваши значения'
            if 'quantity' in edit:qty=Decimal(edit['quantity'])
        if qty is None and all(values.get(k) not in [None,''] and Decimal(values[k])==0 for k in NUTRIENTS):qty=Decimal(0)
        complete=qty is not None and all(values.get(k) not in [None,''] for k in NUTRIENTS)
        if complete:
            known+=1
            for k in NUTRIENTS:totals[k]+=Decimal(values[k])*qty*ratio/100
        rows.append(dict(name=i['name'],original=i.get('original',''),quantity=format(qty.normalize(),'f') if qty is not None else '',basis=basis,values=values,source=source,complete=complete))
    def shown(value):return format(value.quantize(Decimal('.1')).normalize(),'f').replace('.',',')
    return dict(rows=rows,known=known,total=len(rows),complete=known==len(rows),
                totals={k:shown(v) for k,v in totals.items()},per_portion={k:shown(v/chosen) for k,v in totals.items()} if chosen else None,
                servings=chosen,ratio_changed=bool(base and chosen!=base))


def json_key(d):
    return tuple(str(d.get(k)) for k in [*NUTRIENTS,'nutrition_basis'])
