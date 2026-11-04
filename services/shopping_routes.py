"""Purchases workspace, shared family adapter, recipe shortages and explicit stock intake."""
import csv
import io
import json
import secrets
from decimal import Decimal
from flask import Blueprint,render_template,request,session,redirect,url_for,flash,abort,Response,jsonify
from sqlalchemy import select,func,text
from data import db_session
from data.shopping import ShoppingMutation,ShoppingStore
from data.activity import ActivityEvent
from services import shopping_service as ss,product_service as ps,family_service as fs
from services.storage import check_form
from services.workspace import SECTIONS

shopping=Blueprint('shopping',__name__)


def mode():
    value=request.values.get('scope','personal')
    if value not in ('personal','family'):abort(400)
    return value


def common(slug='list',scope='personal'):
    session.setdefault('storage_csrf',secrets.token_hex(32))
    return dict(active_section='shopping',section=SECTIONS['shopping'],feature=next(f for f in SECTIONS['shopping']['functions'] if f['slug']==slug),
        shopping_csrf=session['storage_csrf'],shopping_scope=scope,shopping_key=secrets.token_hex(24),shopping_nonce=lambda:secrets.token_hex(24),units=ps.UNITS,categories=ss.CATEGORIES,
        workspace_back_url=url_for('shopping.index'))


def version(ses,uid,scope):
    model,condition,_,_=ss.scope(ses,uid,scope)
    rows=ses.execute(select(model.id,model.revision,model.submission_key).where(condition).order_by(model.id)).all()
    from services.product_operations import digest
    return digest(list(map(tuple,rows)))


def register_shopping(app,subscription_required,login_required):
    def page(scope,*,add_values=None,add_error=None,status=200):
        uid=session['user_id'];q=ps.clean(request.args.get('q',''))[:120];state=request.args.get('state','pending');category=request.args.get('category','')
        if state not in ('pending','bought','stocked','all'):state='pending'
        if category not in ss.CATEGORIES:category=''
        try:
            with db_session.create_session() as ses:
                model,condition,_,group=ss.scope(ses,uid,scope)
                items=list(ses.scalars(select(model).where(condition).order_by(model.stocked,model.bought,model.priority.desc(),model.created_at.desc(),model.id)))
                filtered=[i for i in items if (not q or q.casefold() in (i.name+' '+i.brand+' '+i.note+' '+i.store).casefold()) and (not category or i.category==category)
                    and (state=='all' or state=='pending' and not i.bought or state=='bought' and i.bought and not i.stocked or state=='stocked' and i.stocked)]
                pages=max(1,(len(filtered)+29)//30)
                try:current=max(1,min(pages,int(request.args.get('page',1))))
                except ValueError:current=1
                pending=[i for i in items if not i.bought];purchased=[i for i in items if i.bought]
                stats=dict(total=len(items),pending=len(pending),bought=sum(i.bought and not i.stocked for i in items),stocked=sum(i.stocked for i in items),
                    budget=sum(i.expected_price or 0 for i in pending),unknown=sum(i.expected_price is None for i in pending))
                visible=filtered[(current-1)*30:current*30]
                context=dict(items=visible,signatures={i.id:ss.signature(i) for i in visible},stats=stats,q=q,state=state,category=category,
                    current=current,pages=pages,found=len(filtered),clear_snapshot=[{'id':i.id,'expected':ss.signature(i)} for i in purchased],
                    list_version=version(ses,uid,scope),family_name=group.name if group else None,add_values=add_values or {},add_error=add_error)
            return render_template('shopping_list.html',**common('shared' if scope=='family' else 'list',scope),**context),status
        except ss.ShoppingError as exc:
            return render_template('shopping_access.html',**common('shared',scope),access_error=str(exc)),exc.status

    @shopping.route('/home/shopping')
    @shopping.route('/home/shopping/list')
    @subscription_required
    def index():return page('personal')

    @shopping.route('/home/shopping/shared')
    @login_required
    def shared():return page('family')

    @shopping.route('/shopping/action',methods=['POST'])
    @login_required
    def action():
        check_form();scope=mode();command=request.form.get('command','');params=request.form.to_dict()
        params.pop('submission_key',None);params.pop('csrf_token',None);params.pop('scope',None);params.pop('command',None)
        try:
            if command=='clear':
                try:params['shown']=json.loads(params.get('shown','[]'))
                except ValueError:raise ss.ShoppingError('Форма очистки устарела.')
            result=ss.mutate(session['user_id'],scope,command,request.form.get('submission_key',''),params)
            if request.accept_mimetypes.best=='application/json':return jsonify(ok=True,result=result,next_key=secrets.token_hex(24))
            flash({'add':'Добавлено в список.','toggle':'Отметка сохранена.','delete':'Запись удалена. Продукты в холодильнике не изменились.','clear':'Купленные записи удалены.','repeat':'Добавлено в список для следующей покупки.'}.get(command,'Изменения сохранены.'),'success')
        except ss.ShoppingError as exc:
            if request.accept_mimetypes.best=='application/json':return jsonify(ok=False,error=str(exc)),exc.status
            if command=='add':return page(scope,add_values=request.form.to_dict(),add_error=str(exc),status=exc.status)
            flash(str(exc),'error')
        return redirect(url_for('shopping.shared' if scope=='family' else 'shopping.index'))

    @shopping.route('/shopping/<int:item_id>/edit',methods=['GET','POST'])
    @login_required
    def edit(item_id):
        scope=mode();error=None;status=200
        try:
            with db_session.create_session() as ses:
                item=ss.get(ses,session['user_id'],scope,item_id);data=ss.form_values(item);expected=ss.signature(item)
                if item.stocked:raise ss.ShoppingError('Эта покупка уже в запасах. Используйте «Купить ещё».',409)
            if request.method=='POST':
                check_form();data=dict(request.form);expected=request.form.get('expected','')
                try:
                    ss.mutate(session['user_id'],scope,'edit',request.form.get('submission_key',''),dict(data,item_id=item_id,expected=expected))
                    flash('Покупка изменена.','success');return redirect(url_for('shopping.shared' if scope=='family' else 'shopping.index'))
                except ss.ShoppingError as exc:error=str(exc);status=exc.status
            return render_template('shopping_edit.html',**common('shared' if scope=='family' else 'list',scope),values=data,expected=expected,error=error,item_id=item_id),status
        except ss.ShoppingError as exc:
            flash(str(exc),'error');return redirect(url_for('shopping.shared' if scope=='family' else 'shopping.index'))

    @shopping.route('/shopping/<int:item_id>/stock',methods=['GET','POST'])
    @login_required
    def intake(item_id):
        scope=mode();error=None;status=200;uid=session['user_id']
        if request.method=='POST':
            check_form()
            try:
                ss.mutate(uid,scope,'stock',request.form.get('submission_key',''),dict(request.form.to_dict(),item_id=item_id,expected=request.form.get('expected','')))
                flash('Покупка добавлена в холодильник.','success')
                return redirect(url_for('shopping.shared' if scope=='family' else 'shopping.index',state='stocked'))
            except ss.ShoppingError as exc:error=str(exc);status=exc.status
        try:
            with db_session.create_session() as ses:
                item=ss.get(ses,uid,scope,item_id);expected=ss.signature(item)
                if not item.bought or item.stocked:raise ss.ShoppingError('Нужна купленная запись, которая ещё не перенесена в запасы.',409)
                places=ss.stock_places(ses,uid)
                shared_place_ids={p.id for _,p,_ in fs.accessible_places(ses,uid,write=True)}
                if scope=='family':places.sort(key=lambda place:place.id not in shared_place_ids)
                values={k:'' for k in ps.FIELDS};values.update(name=item.name,brand=item.brand,quantity=item.quantity,unit=item.unit or 'pcs',category=item.category,
                    note=item.note,price=format(Decimal(item.expected_price)/100,'f') if item.expected_price is not None else '',shelf_unit='days')
            selected=request.form.get('storage_id') if request.method=='POST' else str(places[0].id) if places else ''
            if request.method=='POST':values=dict(request.form);expected=request.form.get('expected','')
            return render_template('shopping_stock.html',**common('shared' if scope=='family' else 'list',scope),values=values,expected=expected,places=places,
                selected=selected,error=error,item_id=item_id,shared_place_ids=shared_place_ids,nutrient_names=ps.NUTRIENTS,bases=ps.BASES),status
        except ss.ShoppingError as exc:
            flash(str(exc),'error');return redirect(url_for('shopping.shared' if scope=='family' else 'shopping.index'))

    @shopping.route('/home/shopping/ingredients',methods=['GET','POST'])
    @subscription_required
    def ingredients():
        from services import recipe_catalog as catalog
        scope=mode();error=None;preview=None;results=None;status=200;uid=session['user_id']
        key=request.values.get('recipe_key','');servings=request.values.get('servings','');q=ps.clean(request.args.get('q',''))[:120]
        try:
            with db_session.create_session() as ses:ss.scope(ses,uid,scope)
            if key:
                preview=ss.recipe_preview(uid,scope,key,servings)
                if request.method=='POST':
                    check_form()
                    ss.mutate(uid,scope,'recipe',request.form.get('submission_key',''),dict(recipe_key=key,servings=servings,
                        preview_signature=request.form.get('preview_signature',''),selected=request.form.getlist('selected')))
                    flash('Выбранные ингредиенты добавлены. Совместимые позиции объединены.','success')
                    return redirect(url_for('shopping.shared' if scope=='family' else 'shopping.index'))
            else:
                if request.method=='POST':check_form();raise ss.ShoppingError('Выберите рецепт.')
                results=catalog.browse({'q':q,'page':request.args.get('page','1')})
        except ss.ShoppingError as exc:error=str(exc);status=exc.status
        return render_template('shopping_ingredients.html',**common('ingredients',scope),preview=preview,results=results,q=q,error=error),status

    @shopping.route('/shopping/export.csv')
    @login_required
    def export():
        scope=mode()
        try:
            with db_session.create_session() as ses:
                model,condition,_,_=ss.scope(ses,session['user_id'],scope)
                items=list(ses.scalars(select(model).where(condition).order_by(model.bought,model.name)))
        except ss.ShoppingError as exc:abort(exc.status,description=str(exc))
        def cell(text):
            value=str(text or '')
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        buf=io.StringIO();writer=csv.writer(buf,delimiter=';');writer.writerow(['Продукт','Марка','Количество','Единица','Категория','Магазин','Ожидаемая стоимость, ₽','Статус','Заметка'])
        for item in items:writer.writerow([cell(item.name),cell(item.brand),item.quantity,ps.UNITS[item.unit][0] if item.unit else '',ss.CATEGORIES[item.category],cell(item.store),
            format(Decimal(item.expected_price)/100,'f') if item.expected_price is not None else '', 'В запасах' if item.stocked else 'Куплено' if item.bought else 'Купить',cell(item.note)])
        return Response('\ufeff'+buf.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="shopping-list.csv"','X-Content-Type-Options':'nosniff'})

    @shopping.route('/shopping/updates')
    @login_required
    def updates():
        try:
            with db_session.create_session() as ses:return jsonify(version=version(ses,session['user_id'],mode()))
        except ss.ShoppingError as exc:return jsonify(error=str(exc)),exc.status

    @shopping.route('/home/shopping/stores',methods=['GET','POST'])
    @subscription_required
    def stores():
        error=None;status=200
        if request.method=='POST':
            check_form()
            try:
                with db_session.create_session() as ses:
                    ses.execute(text('BEGIN IMMEDIATE'))
                    if request.form.get('command')=='delete':
                        saved=ses.get(ShoppingStore,ss.ident(request.form.get('store_id')))
                        if not saved or saved.owner_id!=session['user_id']:raise ss.ShoppingError('Магазин не найден.',404)
                        ses.add(ActivityEvent(owner_id=session['user_id'],category='shopping',action='shopping_store_deleted',after={'name':saved.name}))
                        ses.delete(saved)
                    else:
                        name=ps.clean(request.form.get('name',''));url=ss.store_url(request.form.get('url',''))
                        if not 1<=len(name)<=80:raise ss.ShoppingError('Название магазина: до 80 символов.')
                        if ses.scalar(select(func.count()).select_from(ShoppingStore).where(ShoppingStore.owner_id==session['user_id']))>=20:raise ss.ShoppingError('Можно сохранить до 20 магазинов.')
                        old=ses.scalar(select(ShoppingStore).where(ShoppingStore.owner_id==session['user_id'],ShoppingStore.url==url))
                        if old:raise ss.ShoppingError('Этот магазин уже сохранён.')
                        ses.add(ShoppingStore(owner_id=session['user_id'],name=name,url=url))
                        ses.add(ActivityEvent(owner_id=session['user_id'],category='shopping',action='shopping_store_added',after={'name':name}))
                    ses.commit()
                return redirect(url_for('shopping.stores'))
            except ss.ShoppingError as exc:error=str(exc);status=exc.status
        with db_session.create_session() as ses:items=list(ses.scalars(select(ShoppingStore).where(ShoppingStore.owner_id==session['user_id']).order_by(ShoppingStore.name)))
        return render_template('shopping_stores.html',**common('stores'),stores=items,error=error,search=request.args.get('q','')[:120]),status

    def legacy_action():
        # Existing open family forms keep working against the same rows and stock guards.
        check_form();params=request.form.to_dict();command=params.get('command','').removeprefix('shopping_')
        try:
            key=params.get('submission_key') or secrets.token_hex(24)
            if command=='clear':
                shown=json.loads(params.get('shown','[]'));selected=[]
                with db_session.create_session() as ses:
                    for item_id,revision in shown:
                        item=ss.get(ses,session['user_id'],'family',item_id)
                        if str(item.revision)==str(revision):selected.append({'id':item.id,'expected':ss.signature(item)})
                params['shown']=selected
            elif command!='add':
                with db_session.create_session() as ses:
                    item=ss.get(ses,session['user_id'],'family',params.get('item_id'))
                    if str(item.revision)!=params.get('revision'):raise ss.ShoppingError('Покупка изменилась. Обновите страницу.',409)
                    params['expected']=ss.signature(item)
                    if command=='edit':params={**ss.form_values(item),**params}
                if command=='delete':params['confirm']='yes'
            ss.mutate(session['user_id'],'family',command,key,params);flash('Изменения сохранены.','success')
        except (ss.ShoppingError,ValueError,TypeError) as exc:flash(str(exc),'error')
        return redirect(url_for('shopping.shared'))
    app.extensions['shopping_legacy_action']=legacy_action

    @shopping.after_request
    def private(response):response.headers['Cache-Control']='no-store';return response
    app.register_blueprint(shopping)
