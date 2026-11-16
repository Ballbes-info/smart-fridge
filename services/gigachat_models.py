"""Two user modes; bounded, local complexity routing, never an extra AI call."""
import os
import re

MODES = {
    'standard': {'name': 'Обычный', 'models': 'Lite / Pro', 'description': 'Повседневные вопросы и рецепты; больше доступных обращений.'},
    'advanced': {'name': 'Усиленный', 'models': 'Max / Ultra', 'description': 'Сложные вопросы и рецепты с несколькими ограничениями; отдельный меньший лимит.'},
}
DEFAULTS = {'lite':'GigaChat-2', 'pro':'GigaChat-2-Pro', 'max':'GigaChat-2-Max'}
TASKS = ('label','product','recipe','chat','cooking')


def validate_mode(value):
    if value not in MODES:
        raise ValueError('Выберите обычный или усиленный режим ИИ.')
    return value


def model_id(tier):
    if tier not in ('lite','pro','max','ultra'):
        raise ValueError('Неизвестная модель GigaChat.')
    variable = 'GIGACHAT_' + tier.upper() + '_MODEL'
    value = os.getenv(variable, '').strip() or DEFAULTS.get(tier)
    if value is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}',value):
        raise ValueError('Проверьте API-имя модели в ' + variable + '.')
    if value:
        family = next((kind for kind in ('ultra','max','pro','lite') if kind in value.casefold()), 'lite' if value in ('GigaChat','GigaChat-2') else None)
        if family and family != tier:
            raise ValueError('В ' + variable + ' указана модель другого уровня.')
    return value


def _request_data(payload):
    messages = payload.get('messages') or []
    users = [m for m in messages if isinstance(m,dict) and m.get('role')=='user']
    # First current structured request, not the system prompt or full history.
    for message in reversed(users):
        import json
        try:
            data = json.loads(message.get('content',''))
            if isinstance(data,dict) and ('question' in data or 'wish' in data):return data
        except (ValueError,TypeError):pass
    return {'question': str(users[-1].get('content',''))[:2500] if users else ''}


def complexity(task,payload):
    data = _request_data(payload)
    text = str(data.get('question') or data.get('wish') or '').casefold()
    score = int(len(text)>250) + int(len(text)>800)
    groups = [r'сравн|проанализ|обосн|рассчита|расчит|пересчита',
              r'рацион|на неделю|на несколько дней|план питания|меню на',
              r'суфле|суфлё|муссов|ферментац|темперир|молекуляр|сло[её]ное|безглютен|безлактоз|аллерг|нескольк.*огранич',
              r'пошагов.*план|нескольк.*вариант|подробн.*сравн|разбер.*ошиб|диагности']
    score += sum(bool(re.search(pattern,text)) for pattern in groups)
    score += int(text.count('?')>=3 or text.count('\n')>=4)
    stock = data.get('current_stock',data.get('stock')) or []
    score += int(isinstance(stock,list) and len(stock)>12)
    recipe = data.get('current_recipe') or {}
    if isinstance(recipe,dict):score += int(len(recipe.get('ingredients') or [])>12 or len(recipe.get('steps') or [])>8)
    if task=='recipe':
        excluded = [x for x in re.split(r'[,;\n]',str(data.get('exclude',''))) if x.strip()]
        score += int(len(excluded)>=2) + int(len(excluded)>=4)
        score += int(data.get('goal','maintain')!='maintain')
        try:score += int(int(data.get('servings',2))>6)
        except (TypeError,ValueError):pass
    return score,data,text


def tiers(task,ai_mode='standard',payload=None,attempt=0):
    if task not in TASKS:raise ValueError('Неизвестная задача GigaChat.')
    validate_mode(ai_mode)
    if task in ('label','product'):
        return ('max',)  # Photos never go to Lite or an unverified Ultra vision model.
    score,data,text = complexity(task,payload or {})
    history = (payload or {}).get('messages') or []
    score += int(sum(len(str(m.get('content',''))) for m in history if isinstance(m,dict) and m.get('role')!='system')>5000)
    if ai_mode=='advanced':
        return ('ultra','max') if score>=3 or attempt else ('max','ultra')
    if task=='recipe':
        easy = (not score and not attempt and len(text)<=120 and not data.get('exclude')
                and re.search(r'омлет|яичниц|бутерброд|овсян|салат из|каша',text)
                and len(data.get('stock') or [])<=3)
        return ('lite','pro') if easy else ('pro',)
    if task=='cooking':return ('pro',)
    simple = not attempt and score==0 and len(text)<=180 and not data.get('current_stock') and not data.get('current_recipe')
    return ('lite','pro') if simple else ('pro',)


def configuration():
    return {tier: model_id(tier) or 'auto: GET /models' for tier in ('lite','pro','max','ultra')}


def main():
    import argparse
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    parser=argparse.ArgumentParser(description='API-модели GigaChat без вывода ключа.')
    parser.add_argument('--available',action='store_true',help='Получить доступные API-имена; без генерации.')
    args=parser.parse_args()
    try:
        for tier,ident in configuration().items():print(tier+': '+ident)
        if args.available:
            import requests
            from services import gigachat_label as giga,gigachat_tls
            if not giga.configured():parser.exit(1,'Настройте GIGACHAT_CREDENTIALS на сервере.\n')
            try:
                with requests.Session() as client:
                    gigachat_tls.configure(client);client.headers['Authorization']='Bearer '+giga.access_token(client)
                    ids=giga.available_models(client)
            except giga.ScanError as exc:parser.exit(1,str(exc)+'\n')
            print('Доступные API-модели:')
            for ident in ids:print('  '+ident)
    except ValueError as exc:parser.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
