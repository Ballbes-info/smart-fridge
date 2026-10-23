"""Product entry, read-only grouped inventory, preferences and local OCR."""
import re
import secrets
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select, func, text
from werkzeug.exceptions import RequestEntityTooLarge
from data import db_session
from data.storage import StorageLocation
from data.product import ProductGroup, ProductBatch, ProductPreferences
from services.storage import check_form
from services import product_service as service
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
                           units=service.UNITS,nutrition_bases=service.BASES,nutrients=service.NUTRIENTS,
                           giga_quota=gigachat_label.allowance(session['user_id']),
                           shelf_units=service.SHELF_UNITS,product_csrf=session['storage_csrf'],**preferences_context(),**kwargs)


def own_storage(location_id, ses):
    return ses.scalar(select(StorageLocation).where(StorageLocation.id==location_id,StorageLocation.owner_id==session['user_id']))


def register_products(app, subscription_required, login_required):
    scanner=label_ocr.runtime_status()
    print(f"Сканер этикеток: {scanner['engine']} · Python {scanner['python']}")
    @products.app_template_filter('product_quantity')
    def quantity(micro,dimension): return service.quantity_text(micro,dimension)

    @products.app_template_filter('product_date')
    def date_text(value):return value.strftime('%d.%m.%Y') if value else 'Не указан'

    @products.app_context_processor
    def inject_preferences():
        if request.endpoint=='workspace.settings':
            session.setdefault('storage_csrf',secrets.token_hex(32))
            return dict(**preferences_context(),product_csrf=session['storage_csrf'])
        return {}

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
        query=service.clean(request.args.get('q',''))[:120];storage_id=request.args.get('storage_id','')
        with db_session.create_session() as ses:
            condition=[ProductGroup.owner_id==session['user_id']]
            if storage_id:
                if not re.fullmatch(r'[0-9]{1,18}',storage_id) or not own_storage(int(storage_id),ses):abort(404)
                condition.append(ProductGroup.storage_id==int(storage_id))
            if query:condition.append(ProductGroup.search_key.contains(query.casefold(),autoescape=True))
            total=ses.scalar(select(func.count()).select_from(ProductGroup).where(*condition))
            pages=max(1,(total+23)//24)
            try:current=max(1,min(pages,int(request.args.get('page','1'))))
            except ValueError:current=1
            groups=list(ses.scalars(select(ProductGroup).where(*condition).order_by(ProductGroup.id.desc()).offset((current-1)*24).limit(24)))
            ids=[item.id for item in groups]
            batches=list(ses.scalars(select(ProductBatch).where(ProductBatch.owner_id==session['user_id'],ProductBatch.group_id.in_(ids)).order_by(ProductBatch.added_at.desc()))) if ids else []
        batch_map={group.id:[item for item in batches if item.group_id==group.id] for group in groups}
        return context_page('product_list.html',active_feature='products',groups=groups,batch_map=batch_map,
                            query=query,selected_storage=storage_id,total=total,pages=pages,current_page=current,today=service.today(),
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

    @products.route('/products/gigachat',methods=['POST'])
    @subscription_required
    def gigachat_scan():
        request.max_content_length=9*1024*1024
        try:
            check_form()
            retry=consume_limits([('gigachat-minute',session['user_id'],12,60)])
            if retry:return jsonify(error='Слишком много запросов. Повторите через минуту.'),429,{'Retry-After':str(retry)}
            upload=request.files.get('image')
            if upload is None:return jsonify(error='Добавьте изображение этикетки.'),400
            result=gigachat_label.recognize(upload.read(8*1024*1024+1),session['user_id'])
            result['quota']=gigachat_label.allowance(session['user_id'])
            return jsonify(result)
        except RequestEntityTooLarge:return jsonify(error='Размер снимка превышает 8 МБ.'),413
        except gigachat_label.ScanError as exc:return jsonify(error=str(exc)),exc.status
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
