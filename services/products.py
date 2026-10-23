"""Product entry, inventory operations, expiry notifications and photo scanning."""
import re
import json
import secrets
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select, func, text, or_
from werkzeug.exceptions import RequestEntityTooLarge
from data import db_session
from data.storage import StorageLocation
from data.product import ProductGroup, ProductBatch, ProductPreferences, ProductMutation
from services.storage import check_form
from services import product_service as service
from services import product_operations as operations
from services import label_ocr, product_catalog, gigachat_label
from services.auth_service import consume_limits
from services.workspace import SECTIONS

products=Blueprint('products',__name__)


def preferences_context():
    uid=session.get('user_id')
    if not uid: return {'product_storages':[], 'product_default_storage':None}
    with db_session.create_session() as ses:
        places=list(ses.scalars(select(StorageLocation).where(StorageLocation.owner_id==uid).order_by(StorageLocation.name_key)))
        pref=ses.get(ProductPreferences,uid)
    return {'product_storages':places,'product_default_storage':pref.default_storage_id if pref else None}


def context_page(template, **kwargs):
    session.setdefault('storage_csrf',secrets.token_hex(32))
    slug=kwargs.pop('active_feature','scan')
    feature=next(item for item in SECTIONS['fridges']['functions'] if item['slug']==slug)
    return render_template(template,active_section='fridges',section=SECTIONS['fridges'],feature=feature,
                           units=service.UNITS,nutrition_bases=service.BASES,nutrients=service.NUTRIENTS,action_names=operations.ACTIONS,
                           giga_quota=gigachat_label.allowance(session['user_id']),
                           giga_quotas={k:gigachat_label.allowance(session['user_id'],kind=k) for k in ['label','product']},
                           product_vision_allowed=gigachat_label.product_allowed(session['user_id']),categories=service.CATEGORIES,
                           shelf_units=service.SHELF_UNITS,product_csrf=session['storage_csrf'],**preferences_context(),**kwargs)


def own_storage(location_id, ses):
    return ses.scalar(select(StorageLocation).where(StorageLocation.id==location_id,StorageLocation.owner_id==session['user_id']))


def register_products(app, subscription_required, login_required):
    @products.after_request
    def log_free_scans(response):
        if request.endpoint in ['products.ocr','products.catalog'] and request.method=='POST' and session.get('user_id') and response.status_code in [200,422]:
            import hmac
            if session.get('storage_csrf') and hmac.compare_digest(session['storage_csrf'],request.form.get('csrf_token','')):
                from services.activity_audit import record
                data=response.get_json(silent=True) or {}
                phase=request.form.get('phase','full')
                if request.endpoint=='products.catalog' or phase!='identify':
                    action='barcode_lookup' if request.endpoint=='products.catalog' else 'ocr_read'
                    record(session['user_id'],action,category='ai',after=dict(kind='barcode' if action=='barcode_lookup' else 'ocr',status='used' if response.status_code==200 else 'failed',charged=0,product_name=str(data.get('proposals',{}).get('name',''))[:120],error=str(data.get('error',''))[:500]))
        return response

    scanner=label_ocr.runtime_status()
    print(f"Сканер этикеток: {scanner['engine']} · Python {scanner['python']}")
    @products.app_template_filter('product_quantity')
    def quantity(micro,dimension): return service.quantity_text(micro,dimension)

    @products.app_template_filter('product_expiry_status')
    def expiry_status(batches,on_date):return service.expiry_status(batches,on_date)

    @products.app_template_filter('product_date')
    def date_text(value):return value.strftime('%d.%m.%Y') if value else 'Не указан'

    @products.app_context_processor
    def inject_preferences():
        if request.endpoint=='workspace.settings':
            session.setdefault('storage_csrf',secrets.token_hex(32))
            return dict(**preferences_context(),product_csrf=session['storage_csrf'])
        return {}

    @products.app_context_processor
    def expiry_notifications():
        from services.billing_service import get_subscription,active
        if not session.get('user_id') or not active(get_subscription(session['user_id'])):
            return dict(product_notifications={'items':[],'unread':0,'total':0})
        session.setdefault('storage_csrf',secrets.token_hex(32))
        return dict(product_notifications=operations.notifications(session['user_id']),notification_csrf=session['storage_csrf'])

    @products.route('/home/fridges/scan',methods=['GET','POST'])
    @subscription_required
    def create():
        pref=preferences_context();places=pref['product_storages']
        values={key:'' for key in service.FIELDS};values.update(quantity='1',unit='pcs',shelf_unit='days')
        requested=request.form.get('storage_id') if request.method=='POST' else request.args.get('storage_id')
        try: selected=int(requested) if requested else pref['product_default_storage'] or (places[0].id if places else None)
        except (TypeError,ValueError): abort(400)
        if selected and selected not in [item.id for item in places]: abort(404)
        errors={};status=200
        submission=request.form.get('submission_key','') if request.method=='POST' else secrets.token_hex(24)
        if request.method=='POST':
            check_form();values,parsed,errors=service.validate(request.form)
            if not selected: errors['storage_id']='Сначала создайте место хранения.'
            if not re.fullmatch('[a-f0-9]{48}',submission): errors['form']='Форма устарела. Проверьте данные и отправьте её ещё раз.';submission=secrets.token_hex(24)
            if not errors:
                try:
                    group_id,created=service.save(session['user_id'],selected,submission,values,parsed)
                    flash('Продукт сохранён.' if created else 'Эта запись уже сохранена, повтор не добавлен.','success')
                    return redirect(url_for('products.create',storage_id=selected,added=group_id))
                except service.StorageMissing:
                    errors['storage_id']='Это место хранения уже удалено. Выберите другое.';status=409
                except service.ProductConflict:
                    errors['form']='Эта форма уже была отправлена с другими данными. Проверьте запись перед повторным добавлением.'
                    submission=secrets.token_hex(24);status=409
            if errors and status==200:status=422
        last=None
        if request.method=='GET' and re.fullmatch(r'[0-9]{1,18}',request.args.get('added','')):
            with db_session.create_session() as ses:
                last=ses.scalar(select(ProductGroup).where(ProductGroup.id==int(request.args['added']),ProductGroup.owner_id==session['user_id']))
        return context_page('product_form.html',values=values,errors=errors,selected_storage=selected,
                            submission_key=submission,last_added=last,ocr_used=request.form.get('ocr_used')=='yes',
                            workspace_back_url=url_for('storage.detail',location_id=selected) if selected else url_for('storage.index')),status

    @products.route('/home/fridges/products')
    @subscription_required
    def index():
        from datetime import timedelta
        query=service.clean(request.args.get('q',''))[:120];storage_id=request.args.get('storage_id','')
        state=request.args.get('state','active');sort=request.args.get('sort','expiry')
        if state not in ['active','expired','soon','missing','finished','all','due']:state='active'
        if sort not in ['expiry','newest','name']:sort='expiry'
        today=service.today();soon=today+timedelta(days=3)
        with db_session.create_session() as ses:
            condition=[ProductGroup.owner_id==session['user_id']]
            if storage_id:
                if not re.fullmatch(r'[0-9]{1,18}',storage_id) or not own_storage(int(storage_id),ses):abort(404)
                condition.append(ProductGroup.storage_id==int(storage_id))
            if query:condition.append(ProductGroup.search_key.contains(query.casefold(),autoescape=True))
            base=select(ProductBatch.id).where(ProductBatch.group_id==ProductGroup.id,ProductBatch.owner_id==session['user_id'],ProductBatch.quantity_micro>0)
            if state=='finished':condition.append(~base.exists())
            elif state!='all':
                if state=='expired':base=base.where(ProductBatch.expiry_date<today)
                elif state=='soon':base=base.where(ProductBatch.expiry_date>=today,ProductBatch.expiry_date<=soon)
                elif state=='missing':base=base.where(ProductBatch.expiry_date.is_(None))
                elif state=='due':base=base.where(ProductBatch.expiry_date<=soon)
                condition.append(base.exists())
            nearest=select(func.min(ProductBatch.expiry_date)).where(ProductBatch.group_id==ProductGroup.id,ProductBatch.owner_id==session['user_id'],ProductBatch.quantity_micro>0).correlate(ProductGroup).scalar_subquery()
            order=[nearest.asc().nullslast(),ProductGroup.id.desc()] if sort=='expiry' else [ProductGroup.name.asc(),ProductGroup.id] if sort=='name' else [ProductGroup.id.desc()]
            total=ses.scalar(select(func.count()).select_from(ProductGroup).where(*condition));pages=max(1,(total+23)//24)
            try:current=max(1,min(pages,int(request.args.get('page','1'))))
            except ValueError:current=1
            groups=list(ses.scalars(select(ProductGroup).where(*condition).order_by(*order).offset((current-1)*24).limit(24)))
            ids=[item.id for item in groups]
            batch_condition=[ProductBatch.owner_id==session['user_id'],ProductBatch.group_id.in_(ids)]
            if state not in ['finished','all']:batch_condition.append(ProductBatch.quantity_micro>0)
            batches=list(ses.scalars(select(ProductBatch).where(*batch_condition).order_by(ProductBatch.expiry_date.asc().nullslast(),ProductBatch.added_at.desc()))) if ids else []
        batch_map={group.id:[item for item in batches if item.group_id==group.id] for group in groups}
        return context_page('product_list.html',active_feature='products',groups=groups,batch_map=batch_map,state=state,sort=sort,
                            query=query,selected_storage=storage_id,total=total,pages=pages,current_page=current,today=today,
                            workspace_back_url=url_for('workspace.section',section='fridges'))

    @products.route('/home/fridges/products/<int:group_id>')
    @subscription_required
    def detail(group_id):
        if not 0 < group_id <= 9223372036854775807:abort(404)
        with db_session.create_session() as ses:
            group=ses.scalar(select(ProductGroup).where(ProductGroup.id==group_id,ProductGroup.owner_id==session['user_id']))
            if not group:abort(404)
            place=own_storage(group.storage_id,ses)
            if not place:abort(404)
            batches=list(ses.scalars(select(ProductBatch).where(ProductBatch.group_id==group.id,ProductBatch.owner_id==session['user_id']).order_by(ProductBatch.added_at.desc())))
        return context_page('product_detail.html',active_feature='products',group=group,place=place,batches=batches,
                            today=service.today(),workspace_back_url=url_for('products.index',storage_id=place.id))

    @products.route('/products/batches/<int:batch_id>/<action>',methods=['GET','POST'])
    @subscription_required
    def batch_action(batch_id,action):
        if action not in operations.ACTIONS:abort(404)
        errors={};status=200;values=None;params={}
        nonce=request.form.get('submission_key','') if request.method=='POST' else secrets.token_hex(24)
        expected=request.form.get('expected_state','')
        if request.method=='POST':
            check_form()
            retry=consume_limits([('stock-actions',session['user_id'],60,60)])
            if retry:abort(429)
            if action=='edit':
                values,parsed,errors=service.validate(request.form,allow_zero=True)
                try:target=int(request.form.get('storage_id',''))
                except ValueError:target=0
                if not 0<target<=9223372036854775807:errors['storage_id']='Выберите место хранения.'
                params=dict(values=values,parsed=parsed,storage_id=target)
            else:
                params={key:request.form.get(key,'') for key in ['quantity','unit','note','reason','confirm']}
                if action=='move':
                    try:target=int(request.form.get('storage_id',''))
                    except ValueError:target=0
                    if not 0<target<=9223372036854775807:errors['storage_id']='Выберите место хранения.'
                    params['storage_id']=target
            if not errors:
                try:
                    target,changed=operations.apply(session['user_id'],batch_id,action,nonce,expected,params)
                    flash(('Изменения сохранены.' if action=='edit' else 'Поступление удалено.' if action=='delete' else 'Продукт перемещён.' if action=='move' else 'Расход записан.' if action=='use' else 'Списание записано.') if changed else 'Это действие уже выполнено.','success')
                    with db_session.create_session() as ses:
                        exists=ses.scalar(select(ProductGroup.id).where(ProductGroup.id==target,ProductGroup.owner_id==session['user_id'])) if target and changed else None
                    return redirect(url_for('products.detail',group_id=target) if exists else url_for('products.index'))
                except operations.OperationError as exc:
                    if exc.status==404:abort(404)
                    errors[exc.field]=str(exc);status=exc.status
            else:status=422
        try:
            with db_session.create_session() as ses:batch,group,place=operations.owned(ses,session['user_id'],batch_id)
        except operations.OperationError:
            if status!=200:return context_page('product_operation_error.html',message=errors.get('form','Поступление больше недоступно.')),status
            abort(404)
        if request.method=='GET':expected=operations.digest(operations.snapshot(batch,group,place))
        if action=='edit':
            values=values or operations.values_for(batch,group)
            selected=params.get('storage_id',place.id)
            return context_page('product_form.html',values=values,errors=errors,selected_storage=selected,editing_batch=batch,
                                submission_key=nonce,expected_state=expected,last_added=None,ocr_used=False,
                                workspace_back_url=url_for('products.detail',group_id=group.id)),status
        return context_page('product_operation.html',batch=batch,group=group,place=place,action=action,errors=errors,values=params,
                            submission_key=nonce,expected_state=expected,selected_storage=params.get('storage_id'),
                            workspace_back_url=url_for('products.detail',group_id=group.id)),status

    @products.route('/home/fridges/history')
    @login_required
    def history():
        return app.view_functions['activity.index']()

    @products.route('/products/notifications/read',methods=['POST'])
    @subscription_required
    def read_notifications():
        check_form()
        try:
            seen=json.loads(request.form.get('notifications_seen','[]'))
            if not isinstance(seen,list) or len(seen)>50:raise ValueError
            if any(not isinstance(item,dict) or type(item.get('batch_id')) is not int or not 0<item['batch_id']<=9223372036854775807 or not isinstance(item.get('signature'),str) or not re.fullmatch('[a-f0-9]{64}',item['signature']) for item in seen):raise ValueError
        except (ValueError,TypeError):abort(400)
        operations.mark_notifications(session['user_id'],seen)
        if request.accept_mimetypes.best=='application/json':return jsonify(unread=operations.notifications(session['user_id'])['unread'])
        return redirect(url_for('products.index',state='due'))

    @products.route('/settings/product-default',methods=['POST'])
    @login_required
    def default_storage():
        check_form();value=request.form.get('default_storage_id','')
        with db_session.create_session() as ses:
            ses.execute(text('BEGIN IMMEDIATE'))
            if value and (not re.fullmatch(r'[0-9]{1,18}',value) or not own_storage(int(value),ses)):abort(404)
            pref=ses.get(ProductPreferences,session['user_id'])
            if not pref:pref=ProductPreferences(user_id=session['user_id']);ses.add(pref)
            pref.default_storage_id=int(value) if value else None;ses.commit()
        flash('Место по умолчанию сохранено.','success')
        return redirect(url_for('workspace.settings'))

    @products.route('/products/catalog',methods=['POST'])
    @subscription_required
    def catalog():
        request.max_content_length=4096
        check_form()
        code=request.form.get('barcode','').strip()
        if not product_catalog.valid_gtin(code):return jsonify(error='Проверьте цифры штрихкода на упаковке.'),422
        retry=consume_limits([('catalog-minute',session['user_id'],12,60)])
        if retry:return jsonify(error='Слишком много запросов. Повторите чуть позже.'),429,{'Retry-After':str(retry)}
        return jsonify(product_catalog.lookup(code,session['user_id']))

    @products.route('/products/ai-quota')
    @subscription_required
    def ai_quota():
        kind=request.args.get('kind','label')
        if kind not in ['label','product']:return jsonify(error='Неизвестный режим.'),400
        return jsonify(gigachat_label.allowance(session['user_id'],kind=kind)),200,{'Cache-Control':'no-store'}

    @products.route('/products/gigachat',methods=['POST'])
    @subscription_required
    def gigachat_scan():
        request.max_content_length=9*1024*1024
        try:
            check_form()
            kind=request.form.get('kind','label')
            if kind not in ['label','product']:return jsonify(error='Неизвестный режим распознавания.'),400
            if kind=='product' and not gigachat_label.product_allowed(session['user_id']):
                return jsonify(error='Распознавание продукта по внешнему виду доступно в Премиум и Семейном премиуме.'),403
            retry=consume_limits([('gigachat-minute',session['user_id'],12,60)])
            if retry:return jsonify(error='Слишком много запросов. Повторите через минуту.'),429,{'Retry-After':str(retry)}
            upload=request.files.get('image')
            if upload is None:return jsonify(error='Добавьте изображение этикетки.'),400
            result=gigachat_label.recognize(upload.read(8*1024*1024+1),session['user_id'],kind=kind)
            result['quota']=gigachat_label.allowance(session['user_id'],kind=kind)
            return jsonify(result)
        except RequestEntityTooLarge:return jsonify(error='Размер снимка превышает 8 МБ.'),413
        except gigachat_label.ScanError as exc:return jsonify(error=str(exc),diagnostic=exc.diagnostic),exc.status
        except label_ocr.OCRError as exc:return jsonify(error=str(exc)),422

    @products.route('/products/ocr',methods=['POST'])
    @subscription_required
    def ocr():
        request.max_content_length=9*1024*1024
        try:
            check_form()
            retry=consume_limits([('barcode-minute' if request.form.get('phase')=='identify' else 'ocr-minute',session['user_id'],12,60)])
            if retry:return jsonify(error='Слишком много снимков. Повторите чуть позже.'),429,{'Retry-After':str(retry)}
            upload=request.files.get('image')
            if upload is None:return jsonify(error='Добавьте изображение этикетки.'),400
            raw=upload.read(8*1024*1024+1)
            phase=request.form.get('phase','full')
            if phase not in ['identify','text','full']:return jsonify(error='Неизвестный режим сканирования.'),400
            if phase=='identify':return jsonify(product_catalog.identify(raw,session['user_id']))
            result=label_ocr.recognize(raw, structured=True)
            if phase=='full':
                catalog=product_catalog.identify(raw,session['user_id'])
            else:
                code=request.form.get('barcode','')
                catalog=product_catalog.lookup(code,session['user_id']) if product_catalog.valid_gtin(code) else {}
            if catalog:
                result=product_catalog.enrich(result,catalog)
                result['barcodes']=catalog.get('barcodes',[])
            return jsonify(result)
        except RequestEntityTooLarge:return jsonify(error='Размер снимка превышает 8 МБ.'),413
        except label_ocr.OCRBusy as exc:return jsonify(error=str(exc)),429
        except label_ocr.OCRError as exc:return jsonify(error=str(exc)),422

    app.register_blueprint(products)
