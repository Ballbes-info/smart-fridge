"""Conservative ingredient screening, not a medical suitability classifier."""
import re
from decimal import Decimal, InvalidOperation

ALLERGENS = {
    'milk': ('Молоко', r'молок\w*|слив\w*|сыр\w*|творог\w*|сметан\w*|кефир\w*|йогурт\w*|казеин\w*|сыворот\w*|лакт\w*|маскарпоне|моцарелл\w*|пармезан\w*|брынз\w*|фет[аыуе]'),
    'egg': ('Яйца', r'яйц\w*|яиц|яич\w*|желт\w*|белок|белки|белков\w*|меланж\w*|альбумин\w*|майонез\w*'),
    'peanut': ('Арахис', r'арахис\w*'),
    'nuts': ('Орехи', r'орех\w*|миндал\w*|фундук\w*|кешью|фисташ\w*|пекан\w*|макадам\w*|пралине|марципан\w*'),
    'fish': ('Рыба', r'рыб\w*|лосос\w*|семг\w*|форел\w*|треск\w*|минта\w*|тун[ец]\w*|сельд\w*|селед\w*|скумбр\w*|судак\w*|анчоус\w*|икра|икры'),
    'shellfish': ('Ракообразные и моллюски', r'кревет\w*|краб\w*|раки|раков|омар\w*|лангуст\w*|мид\w*|устриц\w*|кальмар\w*|осьминог\w*|гребеш\w*|морепродукт\w*'),
    'soy': ('Соя', r'со[яиюе]|соев\w*|тофу|мисо|эдамаме|темпе'),
    'gluten': ('Злаки с глютеном', r'пшен\w*|рожь|ржан\w*|ячмен\w*|перлов\w*|булгур\w*|кускус\w*|манн\w*|манк\w*|полб\w*|спельт\w*|солод\w*|овес|овся\w*|геркулес\w*|мук\w*|макарон\w*|спагетти|лапш\w*|хлеб\w*|сухар\w*|паниров\w*'),
    'sesame': ('Кунжут', r'кунжут\w*|сезам\w*|тахин\w*'),
    'mustard': ('Горчица', r'горчиц\w*|горчич\w*'),
    'celery': ('Сельдерей', r'сельдер\w*'),
    'lupin': ('Люпин', r'люпин\w*'),
    'sulphites': ('Сульфиты', r'сульфит\w*|диоксид\s+серы|е\s*22[0-8]|e\s*22[0-8]'),
}
AGES = {'adult':'Взрослый', 'child':'Ребёнок 1–17 лет', 'senior':'Пожилой человек', 'infant':'Ребёнок до года'}
CONDITIONS = {'none':'Нет специальных назначений', 'diabetes':'Диабет', 'celiac':'Целиакия', 'kidney':'Заболевание почек', 'other':'Другие назначения врача'}
DEFAULTS = dict(allergens=[], audience='adult', condition='none', medical_excluded='', carb_limit='')
def normalize(value):
    return str(value or '').casefold().replace('ё','е')
def matches(pattern, value):
    return bool(re.search(r'(?<!\w)(?:'+pattern+r')(?!\w)', normalize(value)))
def validate(form):
    selected=form.getlist('allergens') if hasattr(form,'getlist') else form.get('allergens',[])
    if not isinstance(selected,list) or any(x not in ALLERGENS for x in selected):
        raise ValueError('Выберите аллергены из списка.')
    values={k:str(form.get(k,DEFAULTS[k])).strip() for k in ['audience','condition','medical_excluded','carb_limit']}
    values['allergens']=sorted(set(selected))
    if values['audience'] not in AGES or values['condition'] not in CONDITIONS:raise ValueError('Проверьте возрастную группу и назначения.')
    if len(values['medical_excluded'])>500:raise ValueError('Исключения врача — до 500 символов.')
    if values['carb_limit']:
        try:
            limit=Decimal(values['carb_limit'].replace(',','.'))
            if not limit.is_finite() or not 0<limit<=300:raise ValueError
            values['carb_limit']=str(limit)
        except (InvalidOperation,ValueError):raise ValueError('Предел углеводов: число больше 0 и не больше 300 г.')
    return values
def assess(recipe, profile, nutrients=None, portions=1):
    settings=DEFAULTS|profile.get('dietary',{})
    blocked=[];review=[]
    ingredients=recipe.get('ingredients') or []
    text=' '.join(str(i.get(k,'') or '') for i in ingredients for k in ['name','note','original'])
    selected=set(settings['allergens'])
    if settings['condition']=='celiac':selected.add('gluten')
    if not ingredients or any(not i.get('name') for i in ingredients):
        blocked.append('Нет полного списка ингредиентов.')
    for key in sorted(selected):
        if key in ALLERGENS and matches(ALLERGENS[key][1],text):
            blocked.append('Возможный аллерген: '+ALLERGENS[key][0]+'.')
    for word in re.split(r'[,;\n]',str(profile.get('excluded',''))+','+settings['medical_excluded']):
        word=normalize(word).strip()
        if word and word in normalize(text):blocked.append('Исключённый ингредиент: '+word+'.')
    if selected:
        review.append('Проверьте полный состав каждого продукта и следы аллергенов на упаковке. Отсутствие совпадений не подтверждает безопасность.')
        if matches(r'соус\w*|смес\w*|приправ\w*|полуфабрикат\w*|колбас\w*|сосиск\w*|шоколад\w*|печень\w*|бульон\w*|маргарин\w*|майонез\w*|песто|паст[аыуе]',text):
            blocked.append('Состав многокомпонентного продукта не подтверждён.')
    if settings['audience']=='infant':
        blocked.append('Подбор для детей до года пока недоступен: нужен отдельный план прикорма.')
    elif settings['audience']=='child':
        review.append('Проверьте возраст, размер кусочков, текстуру и порцию. Этот фильтр не оценивает риск подавиться и не составляет детский рацион.')
        if matches(r'вино|вина|вином|коньяк\w*|водк\w*|ром|рома|ликер\w*|пив[оау]|виски',text):
            blocked.append('В рецепте указан алкоголь.')
    elif settings['audience']=='senior':
        review.append('Учитывайте аппетит, возможность жевать и глотать и назначения врача. Возраст сам по себе не определяет диету.')
    if settings['condition']!='none':
        review.append('Учитываются только выбранные ограничения. Соответствие лечебной диете не подтверждено.')
    if settings['carb_limit']:
        try:
            carbs=Decimal(str(nutrients['carbs']).replace(',','.'))*Decimal(str(portions))
            if not carbs.is_finite() or carbs<0:raise ValueError
            if carbs>Decimal(settings['carb_limit']):blocked.append('Углеводы в выбранной порции выше заданного предела.')
        except (TypeError,KeyError,InvalidOperation,ValueError):
            blocked.append('Недостаточно данных об углеводах на порцию.')
    return dict(blocked=blocked,review=review,eligible=not blocked)
def active(profile):
    return bool(profile.get('excluded') or profile.get('dietary',{})!=DEFAULTS and profile.get('dietary'))
