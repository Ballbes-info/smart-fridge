"""CRUD for personal storage locations, protected by the existing subscription gate."""
import hmac
import secrets
import time
import unicodedata
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import delete, select, update, func, text
from sqlalchemy.exc import IntegrityError
from data import db_session
from data.storage import StorageLocation
from data.billing import Subscription
from data.product import ProductGroup, ProductPreferences
from data.activity import ActivityEvent
from services.billing_service import PLANS, active
from services.workspace import SECTIONS

storage = Blueprint('storage', __name__)
KINDS = {'fridge': 'Холодильник', 'freezer': 'Морозильник', 'pantry': 'Шкаф', 'other': 'Другое'}
PAGE_SIZE = 12


class StorageLimitReached(Exception):
    pass


def quota(ses):
    sub = ses.get(Subscription, session['user_id'])
    limit = PLANS.get(sub.plan, {}).get('storage_limit', 0) if active(sub) else 0
    count = ses.scalar(select(func.count()).select_from(StorageLocation).where(
        StorageLocation.owner_id == session['user_id']))
    return count, limit


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split())


def owned(location_id, ses):
    if not 0 < location_id <= 9223372036854775807:
        abort(404)
    item = ses.scalar(select(StorageLocation).where(
        StorageLocation.id == location_id, StorageLocation.owner_id == session['user_id']))
    if item is None:
        abort(404)
    return item


def check_form():
    expected = session.get('storage_csrf', '')
    actual = request.form.get('csrf_token', '')
    if not expected or not hmac.compare_digest(expected.encode(), actual.encode()):
        abort(400, description='Форма устарела. Обновите страницу и повторите действие.')


def revision_matches(item):
    return request.form.get('revision') == str(item.revision)


def read_form():
    values = dict(name=normalized(request.form.get('name', '')),
                  kind=request.form.get('kind', ''),
                  location=normalized(request.form.get('location', '')),
                  note=request.form.get('note', '').strip())
    errors = {}
    if not 1 <= len(values['name']) <= 60:
        errors['name'] = 'Введите название длиной от 1 до 60 символов.'
    if any(unicodedata.category(c).startswith('C') for c in values['name']):
        errors['name'] = 'Уберите невидимые и управляющие символы из названия.'
    if values['kind'] not in KINDS:
        errors['kind'] = 'Выберите тип места хранения.'
    if len(values['location']) > 80:
        errors['location'] = 'Расположение — не более 80 символов.'
    if len(values['note']) > 300:
        errors['note'] = 'Заметка — не более 300 символов.'
    return values, errors


def page(template, **context):
    if 'storage_csrf' not in session:
        session['storage_csrf'] = secrets.token_hex(32)
    with db_session.create_session() as ses:
        used, limit = quota(ses)
    feature = next(item for item in SECTIONS['fridges']['functions'] if item['slug'] == 'storage')
    return render_template(template, active_section='fridges', section=SECTIONS['fridges'], feature=feature,
                           storage_kinds=KINDS, storage_csrf=session['storage_csrf'],
                           storage_used=used, storage_limit=limit, storage_full=used >= limit, **context)


def register_storage(app, subscription_required):
    @storage.route('/home/fridges/storage')
    @subscription_required
    def index():
        query = normalized(request.args.get('q', ''))[:100]
        kind = request.args.get('kind', '')
        if kind not in KINDS:
            kind = ''
        try:
            current_page = max(1, int(request.args.get('page', '1')))
        except ValueError:
            current_page = 1
        with db_session.create_session() as ses:
            items = list(ses.scalars(select(StorageLocation).where(StorageLocation.owner_id == session['user_id'])
                                    .order_by(StorageLocation.name_key, StorageLocation.id)))
        total = len(items)
        key = query.casefold()
        matches = [item for item in items if (not kind or item.kind == kind) and
                   (not key or key in (item.name + ' ' + item.location).casefold())]
        pages = max(1, (len(matches) + PAGE_SIZE - 1) // PAGE_SIZE)
        current_page = min(current_page, pages)
        return page('storage_list.html', items=matches[(current_page-1)*PAGE_SIZE:current_page*PAGE_SIZE],
                    total=total, found=len(matches), query=query, selected_kind=kind,
                    current_page=current_page, pages=pages,
                    workspace_back_url=url_for('workspace.section', section='fridges'))

    @storage.route('/home/fridges/storage/new', methods=['GET', 'POST'])
    @subscription_required
    def create():
        values = dict(name='', kind='fridge', location='', note='')
        errors = {}; status = 200
        if request.method == 'POST':
            check_form()
            values, errors = read_form()
            if not errors:
                try:
                    with db_session.create_session() as ses:
                        # Serialize quota check + insert with plan changes and other tabs.
                        ses.execute(text('BEGIN IMMEDIATE'))
                        used, limit = quota(ses)
                        if used >= limit:
                            raise StorageLimitReached(f'Лимит вашего тарифа — {limit}. Новое место не создано.')
                        now = time.time()
                        item = StorageLocation(owner_id=session['user_id'], name_key=values['name'].casefold(),
                                               created_at=now, updated_at=now, **values)
                        ses.add(item); ses.commit(); location_id = item.id
                    flash('Место хранения создано.', 'success')
                    return redirect(url_for('storage.detail', location_id=location_id))
                except StorageLimitReached as exc:
                    errors['form'] = str(exc)
                    status = 409
                except IntegrityError:
                    errors['name'] = 'У вас уже есть место с таким названием. Выберите другое.'
        return page('storage_form.html', values=values, errors=errors, item=None,
                    workspace_back_url=url_for('storage.index')), status if status == 409 else 422 if errors else 200

    @storage.route('/home/fridges/storage/<int:location_id>')
    @subscription_required
    def detail(location_id):
        with db_session.create_session() as ses:
            item = owned(location_id, ses)
        return page('storage_detail.html', item=item, workspace_back_url=url_for('storage.index'))

    @storage.route('/home/fridges/storage/<int:location_id>/edit', methods=['GET', 'POST'])
    @subscription_required
    def edit(location_id):
        with db_session.create_session() as ses:
            item = owned(location_id, ses)
        values = {key: getattr(item, key) for key in ('name', 'kind', 'location', 'note')}
        errors = {}; status = 200
        if request.method == 'POST':
            check_form()
            values, errors = read_form()
            if not revision_matches(item):
                errors['form'] = 'Место уже изменено в другой вкладке. Проверьте текущие данные перед сохранением.'
                status = 409
            elif not errors:
                try:
                    with db_session.create_session() as ses:
                        result = ses.execute(update(StorageLocation).where(
                            StorageLocation.id == location_id, StorageLocation.owner_id == session['user_id'],
                            StorageLocation.revision == item.revision).values(
                                **values, name_key=values['name'].casefold(), updated_at=time.time(), revision=item.revision+1))
                        if result.rowcount != 1:
                            abort(409, description='Место изменилось. Обновите страницу перед сохранением.')
                        before={key:getattr(item,key) for key in ['name','kind','location','note']}
                        if before!=values:
                            ses.add(ActivityEvent(owner_id=session['user_id'],category='storage',action='storage_changed',before=before,after=values))
                        ses.commit()
                    flash('Изменения сохранены.', 'success')
                    return redirect(url_for('storage.detail', location_id=location_id))
                except IntegrityError:
                    errors['name'] = 'У вас уже есть место с таким названием. Выберите другое.'
            if errors and status != 409:
                status = 422
        return page('storage_form.html', values=values, errors=errors, item=item,
                    workspace_back_url=url_for('storage.detail', location_id=location_id)), status

    @storage.route('/home/fridges/storage/<int:location_id>/delete', methods=['GET', 'POST'])
    @subscription_required
    def remove(location_id):
        with db_session.create_session() as ses:
            item = owned(location_id, ses)
            product_count = ses.scalar(select(func.count()).select_from(ProductGroup).where(ProductGroup.storage_id==location_id))
        error = None; status = 200
        if request.method == 'POST':
            check_form()
            if request.form.get('confirm') != 'yes':
                error = 'Подтвердите удаление галочкой.'
                status = 422
            elif not revision_matches(item):
                error = 'Место изменилось в другой вкладке. Проверьте его данные перед удалением.'
                status = 409
            else:
                with db_session.create_session() as ses:
                    ses.execute(text('BEGIN IMMEDIATE'))
                    product_count = ses.scalar(select(func.count()).select_from(ProductGroup).where(ProductGroup.storage_id==location_id))
                    if product_count:
                        return page('storage_delete.html', item=item, error='В этом месте есть продукты. Удаление недоступно.',
                                    product_count=product_count, workspace_back_url=url_for('storage.detail',location_id=location_id)),409
                    pref=ses.get(ProductPreferences,session['user_id'])
                    if pref and pref.default_storage_id==location_id:pref.default_storage_id=None
                    result = ses.execute(delete(StorageLocation).where(
                        StorageLocation.id == location_id, StorageLocation.owner_id == session['user_id'],
                        StorageLocation.revision == item.revision))
                    if result.rowcount != 1:
                        abort(409, description='Место изменилось. Обновите страницу перед удалением.')
                    ses.add(ActivityEvent(owner_id=session['user_id'],category='storage',action='storage_deleted',before={key:getattr(item,key) for key in ['name','kind','location','note']}))
                    ses.commit()
                flash('Место хранения удалено.', 'success')
                return redirect(url_for('storage.index'))
        return page('storage_delete.html', item=item, error=error, product_count=product_count,
                    workspace_back_url=url_for('storage.detail', location_id=location_id)), status

    app.register_blueprint(storage)
