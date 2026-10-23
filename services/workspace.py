"""Navigation and page shells for the signed-in workspace."""
from flask import Blueprint, abort, render_template, session, g
from copy import deepcopy
from services.billing_service import PLANS, active, get_subscription

workspace = Blueprint('workspace', __name__)

SECTIONS = {
    'fridges': {'name': 'Холодильники', 'icon': 'fridge', 'description': 'Все места хранения и продукты внутри.', 'empty': 'Ваши холодильники появятся здесь', 'hint': 'Кухня, морозильник или дача — у каждого места будет свой список запасов.'},
    'shopping': {'name': 'Покупки', 'icon': 'basket', 'description': 'Что купить для дома и выбранных блюд.', 'empty': 'Здесь будет ваш список покупок', 'hint': 'Собирайте покупки для дома и добавляйте недостающие ингредиенты из рецептов.'},
    'recipes': {'name': 'Рецепты', 'icon': 'book', 'description': 'Идеи блюд из продуктов, которые уже есть.', 'empty': 'Здесь вы сможете выбирать рецепты', 'hint': 'Подбирайте блюда по запасам и используйте в первую очередь продукты с ближайшим сроком.'},
    'nutrition': {'name': 'Питание', 'icon': 'leaf', 'description': 'Личный дневник, КБЖУ и цели питания.', 'empty': 'Здесь будет ваш дневник питания', 'hint': 'Записывайте приёмы пищи и следите за КБЖУ. Личный дневник остаётся вашим даже в семейном тарифе.'},
    'assistant': {'name': 'ИИ-помощник', 'icon': 'spark', 'description': 'Вопросы о продуктах, блюдах и системе.', 'empty': 'Здесь появится разговор с помощником', 'hint': 'Помощник сможет подсказать рецепт, помочь с готовкой и объяснить возможности SmartFridge.'},
    'statistics': {'name': 'Статистика', 'icon': 'chart', 'description': 'Расход продуктов, потери и экономия.', 'empty': 'Статистика появится вместе с записями', 'hint': 'Здесь будут собираться данные об использованных и выброшенных продуктах. Экономия будет рассчитываться по вашим данным.'},
    'family': {'name': 'Семья', 'icon': 'users', 'description': 'Общие холодильники и доступ для близких.', 'empty': 'Здесь вы сможете собрать семью', 'hint': 'Приглашайте близких и выбирайте доступ к общим холодильникам. В семейный тариф входят пять участников, дополнительные места оплачиваются отдельно.'},
}


SECTION_HELP = {
    'fridges': 'Здесь будут собраны ваши холодильники, морозильники и другие места хранения. У каждого — своё название и список продуктов. Это позволяет отдельно вести запасы дома и на даче, не смешивая их.\n\nВ карточке продукта предусмотрены количество, срок годности и дата вскрытия. Добавление будет доступно вручную, по этикетке и через анализ фотографии в премиуме. При переносе продукта в другое место не потребуется создавать запись заново.',
    'shopping': 'Раздел объединит список покупок для дома и недостающие ингредиенты из рецептов. Отмечайте купленное, чтобы список оставался актуальным во время похода в магазин.\n\nВ семейном тарифе список будет общим: участники смогут видеть, что ещё нужно купить. Подраздел «Магазины» предназначен для переходов к покупке продуктов; собственный магазин в SmartFridge не предусмотрен.',
    'recipes': 'Здесь можно будет искать блюда с учётом имеющихся продуктов и сохранять понравившиеся рецепты. Подбор поможет использовать запасы с ближайшим сроком годности, а недостающие ингредиенты — перенести в покупки.\n\nВ рецепте предусмотрены состав, порции и пищевая ценность. В премиуме появится генерация рецептов с ИИ под ваши предпочтения. Сохранённые рецепты будут доступны в избранном.',
    'nutrition': 'Личный дневник предназначен для записи приёмов пищи, порций и расчёта калорий, белков, жиров и углеводов. В целях питания можно будет указать предпочтения и желаемое направление: поддержание веса, набор или снижение.\n\nВ премиуме планируется персональное меню с учётом этих целей. Дневник остаётся личным и при семейной подписке. Рекомендации по питанию не заменяют назначения врача.',
    'assistant': 'Помощник будет отвечать на вопросы о продуктах, предлагать варианты блюд и сопровождать приготовление по шагам. Отдельный подраздел посвящён использованию самого SmartFridge.\n\nВызовы ИИ предусмотрены в премиуме. На кухонном дисплее голосовое управление разделится на базовые команды по сценарию и ответы ИИ. Сейчас доступны страницы этих возможностей; работа помощника ещё не подключена.',
    'statistics': 'Здесь будет видна история расхода продуктов: что использовано, что пришлось выбросить и как меняются запасы. Раздел позволит отличать потребление от потерь.\n\nСтоимость потерь и оценка экономии будут рассчитываться только по внесённым вами данным. До появления записей показатели не заполняются случайными числами. Подразделы разделяют текущие запасы, расход, потери и экономию.',
    'family': 'Семейное пространство предназначено для совместного доступа к выбранным холодильникам и покупкам. Владелец сможет приглашать близких и управлять составом участников.\n\nВ семейную подписку входят пять мест, дополнительные оплачиваются отдельно. Личные цели и дневник питания не становятся общими. Этот раздел отвечает за участников, приглашения и доступ, а изменение тарифа остаётся в настройках подписки.',
}
for key, text in SECTION_HELP.items():
    SECTIONS[key]['help'] = text


# Page destinations only. No inventory, AI, recipe or family operations run here.
FUNCTIONS = {
    'fridges': [
        ('storage', 'Места хранения', 'Несколько холодильников и морозильников с собственными названиями.'),
        ('products', 'Продукты и сроки', 'Количество, дата вскрытия и срок годности каждого продукта.'),
        ('scan', 'Добавление продуктов', 'Ручной ввод, чтение этикетки и распознавание фото в премиуме.'),
        ('transfer', 'Перемещение', 'Перенос продуктов между холодильниками и местами хранения.'),
    ],
    'shopping': [
        ('list', 'Список покупок', 'Продукты, которые нужно купить, в одном списке.'),
        ('ingredients', 'Из рецептов', 'Недостающие ингредиенты для выбранных блюд.'),
        ('shared', 'Совместные покупки', 'Общий список для участников семейного тарифа.'),
        ('stores', 'Магазины', 'Переходы к покупке недостающих продуктов.'),
    ],
    'recipes': [
        ('available', 'Из ваших продуктов', 'Подбор блюд с учётом запасов и ближайших сроков.'),
        ('saved', 'Избранное', 'Рецепты, к которым хочется вернуться.'),
        ('nutrition', 'Состав и КБЖУ', 'Ингредиенты, порции и пищевая ценность блюда.'),
        ('generate', 'Создать с ИИ', 'Генерация рецептов под ваши пожелания в премиуме.'),
    ],
    'nutrition': [
        ('diary', 'Дневник питания', 'Личная запись приёмов пищи и порций.'),
        ('nutrients', 'Калории и БЖУ', 'Расчёт пищевой ценности по записям дневника.'),
        ('goals', 'Мои цели', 'Предпочтения и цели питания в личном профиле.'),
        ('menu', 'Персональное меню', 'Меню с учётом целей и предпочтений в премиуме.'),
    ],
    'assistant': [
        ('chat', 'Чат с помощником', 'Вопросы о продуктах и питании в премиуме.'),
        ('cooking', 'Помощь с готовкой', 'Подсказки по шагам приготовления блюда.'),
        ('guide', 'Помощь по SmartFridge', 'Объяснение возможностей и навигации по системе.'),
        ('voice', 'Голосовой помощник', 'Голосовое управление и помощь на кухонном дисплее.'),
    ],
    'statistics': [
        ('inventory', 'Ваши запасы', 'Обзор добавленных продуктов и изменения количества.'),
        ('usage', 'Расход', 'История использованных продуктов.'),
        ('waste', 'Потери', 'Продукты, которые пришлось выбросить, и их стоимость.'),
        ('savings', 'Экономия', 'Оценка по вашим данным, без вымышленных показателей.'),
    ],
    'family': [
        ('members', 'Участники', 'Состав семьи и доступные места в подписке.'),
        ('invitations', 'Приглашения', 'Приглашение близких в семейное пространство.'),
        ('access', 'Общие холодильники', 'Доступ участников к выбранным местам хранения.'),
        ('shopping', 'Семейные покупки', 'Покупки и списки для общего дома.'),
    ],
}
for key, functions in FUNCTIONS.items():
    SECTIONS[key]['functions'] = [dict(slug=slug, name=name, description=description) for slug, name, description in functions]


# One policy supplies both the navigation and the server-side route checks.
SECTION_REQUIREMENTS = {'family': 'family'}
FEATURE_REQUIREMENTS = {
    ('shopping', 'shared'): 'family',
    ('recipes', 'generate'): 'premium',
    ('nutrition', 'menu'): 'premium',
    ('assistant', 'chat'): 'premium',
    ('assistant', 'cooking'): 'premium',
}
REQUIREMENTS = {
    'family': {'name': 'Семейная подписка', 'plans': 'Семейная базовая или Семейная премиум',
               'detail': 'Совместный доступ к холодильникам, участникам и покупкам входит в семейные тарифы.'},
    'premium': {'name': 'Премиум', 'plans': 'Премиум или Семейная премиум',
                'detail': 'Функции с ИИ и персональное меню входят в премиум-тарифы.'},
}


def access_rule(section, feature=None):
    return SECTION_REQUIREMENTS.get(section) or FEATURE_REQUIREMENTS.get((section, feature))


def access_state(section, feature=None):
    if 'workspace_subscription' not in g:
        g.workspace_subscription = get_subscription(session['user_id']) if session.get('user_id') else None
    sub = g.workspace_subscription
    rule = access_rule(section, feature)
    plan = PLANS.get(sub.plan, {}) if sub else {}
    locked = bool(rule and (not active(sub) or not plan.get(rule)))
    return dict(locked=locked, requirement=REQUIREMENTS.get(rule), rule=rule)


def navigation_sections():
    sections = deepcopy(SECTIONS)
    for key, section in sections.items():
        section.update(access_state(key))
        for item in section['functions']:
            item.update(access_state(key, item['slug']))
    return sections


def register_workspace(app, login_required, subscription_required):
    @workspace.app_context_processor
    def workspace_context():
        sections = navigation_sections()
        entries = []
        for key, section in sections.items():
            entries.append(dict(name=section['name'], group='Раздел', url='/home/'+key,
                                keywords=section['description'], locked=section['locked'], requirement=section['requirement']))
            if not section['locked']:
                for item in section['functions']:
                    entries.append(dict(name=item['name'], group=section['name'], url='/home/'+key+'/'+item['slug'],
                                        keywords=item['description'], locked=item['locked'], requirement=item['requirement']))
        return {'workspace_sections': sections, 'workspace_search': entries}

    @workspace.route('/home')
    @subscription_required
    def home():
        return render_template('workspace.html', active_section='home')

    @workspace.route('/home/<section>')
    @subscription_required
    def section(section):
        if section not in SECTIONS:
            abort(404)
        selected = navigation_sections()[section]
        if selected['locked']:
            return render_template('workspace_locked.html', active_section=section,
                                   access_title=selected['name'], requirement=selected['requirement']), 403
        return render_template('workspace.html', active_section=section, section=selected)

    @workspace.route('/home/<section>/<feature>')
    @subscription_required
    def feature(section, feature):
        selected = navigation_sections().get(section)
        item = next((item for item in selected['functions'] if item['slug'] == feature), None) if selected else None
        if item is None:
            abort(404)
        if item['locked']:
            return render_template('workspace_locked.html', active_section=section, section=selected,
                                   access_title=item['name'], requirement=item['requirement']), 403
        return render_template('workspace.html', active_section=section, section=selected, feature=item)

    @workspace.route('/settings')
    @login_required
    def settings():
        return render_template('settings.html', active_section='settings')

    app.register_blueprint(workspace)
