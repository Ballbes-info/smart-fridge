"""Family screens: members, bound invitations, shared inventory and shopping."""
import hashlib
import re
import secrets
import time
from flask import Blueprint, abort, flash, render_template, redirect, request, session, url_for
from sqlalchemy import select, text, delete, func
from data import db_session
from data.user import User
from data.family import Family, FamilyMember, FamilyInvitation, FamilyStorage, FamilyShoppingItem, FamilyEvent
from data.storage import StorageLocation
from data.product import ProductGroup, ProductBatch
from services import family_service as fs, product_service as ps, product_operations as ops
from services.storage import check_form
from services.workspace import SECTIONS

family = Blueprint('family', __name__)
TABS = {'members':'Участники', 'invitations':'Приглашения', 'access':'Общие холодильники', 'shopping':'Семейные покупки'}


def clean(raw, limit, label):
    value = ps.clean(raw)
    if not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise fs.FamilyError(f'{label}: от 1 до {limit} символов.')
    return value


def integer(raw):
    try:
        value = int(raw)
        if not 0 < value <= 9223372036854775807: raise ValueError
        return value
    except (TypeError, ValueError):
        raise fs.FamilyError('Запись не найдена. Обновите страницу.')


def common(tab='members'):
    session.setdefault('storage_csrf', secrets.token_hex(32))
    return dict(active_section='family', section=SECTIONS['family'],
        feature=next(f for f in SECTIONS['family']['functions'] if f['slug']==tab), family_csrf=session['storage_csrf'],
        tabs=TABS, tab=tab, shopping_key=secrets.token_hex(24), workspace_back_url=url_for('workspace.home'))


def register_family(app, login_required):
    @family.app_context_processor
    def invitation_notice():
        uid=session.get('user_id')
        if not uid:return {'family_invitation_count':0,'has_family_access':False}
        with db_session.create_session() as ses:
            user=ses.get(User,uid)
            count=ses.scalar(select(func.count()).select_from(FamilyInvitation).where(
                FamilyInvitation.email==user.email.casefold(),FamilyInvitation.status=='pending',
                FamilyInvitation.expires_at>time.time())) if user else 0
            group=fs.family_for(ses,uid)
            family_access=bool(group and fs.entitled(ses,group,uid))
        return {'family_invitation_count':count,'has_family_access':family_access}

    @family.route('/family')
    @family.route('/family/<any(members,invitations,access,shopping):tab>')
    @family.route('/home/family')
    @family.route('/home/family/<any(members,invitations,access,shopping):tab>')
    @login_required
    def screen(tab='members'):
        uid = session['user_id']; context = common(tab)
        with db_session.create_session() as ses:
            group = fs.family_for(ses, uid)
            incoming = list(ses.execute(select(FamilyInvitation, Family.name, User.name)
                .join(Family, Family.id==FamilyInvitation.family_id).join(User, User.id==Family.owner_id)
                .where(FamilyInvitation.email==ses.get(User,uid).email.casefold(), FamilyInvitation.status=='pending',
                       FamilyInvitation.expires_at>time.time()).order_by(FamilyInvitation.id.desc())))
            from services.billing_service import PLANS, active
            from data.billing import Subscription
            own_sub=ses.get(Subscription,uid)
            context.update(family=group, incoming=incoming, is_owner=bool(group and group.owner_id==uid),
                can_create=bool(active(own_sub) and PLANS.get(own_sub.plan,{}).get('family')))
            if group:
                seats = fs.capacity(ses, group); member_rows=fs.members(ses, group)
                users={m.user_id:ses.get(User,m.user_id) for m in member_rows}
                invitations=fs.pending(ses,group) if group.owner_id==uid else []
                shares=list(ses.execute(select(FamilyStorage,StorageLocation,User.name)
                    .join(StorageLocation,StorageLocation.id==FamilyStorage.storage_id).join(User,User.id==StorageLocation.owner_id)
                    .where(FamilyStorage.family_id==group.id).order_by(StorageLocation.name_key))) if seats and fs.entitled(ses,group,uid) else []
                own=list(ses.scalars(select(StorageLocation).where(StorageLocation.owner_id==uid).order_by(StorageLocation.name_key)))
                shopping=list(ses.scalars(select(FamilyShoppingItem).where(FamilyShoppingItem.family_id==group.id)
                    .order_by(FamilyShoppingItem.bought,FamilyShoppingItem.created_at.desc()).limit(500))) if seats and fs.entitled(ses,group,uid) else []
                events=list(ses.execute(select(FamilyEvent, User.name).join(User, User.id==FamilyEvent.actor_id)
                    .where(FamilyEvent.family_id==group.id).order_by(FamilyEvent.id.desc()).limit(25)))
                context.update(seats=seats, members=member_rows, users=users, invitations=invitations,
                    shares=shares, own_places=own, shopping=shopping, shared_ids={share.storage_id for share,_,_ in shares},
                    events=events, live=bool(seats), entitled=fs.entitled(ses,group,uid), reserved=len(invitations))
        return render_template('family.html',**context)

    @family.route('/family/action', methods=['POST'])
    @login_required
    def action():
        check_form(); uid=session['user_id']; command=request.form.get('command'); tab=request.form.get('tab','members')
        if tab not in TABS:tab='members'
        try:
            with db_session.create_session() as ses:
                ses.execute(text('BEGIN IMMEDIATE'))
                if command=='create':
                    fs.create(ses,uid,clean(request.form.get('name',''),60,'Название семьи'))
                elif command=='accept' or command=='decline':
                    invitation=ses.get(FamilyInvitation,integer(request.form.get('invitation_id')))
                    if not invitation or invitation.email!=ses.get(User,uid).email.casefold():
                        raise fs.FamilyError('Приглашение не найдено.')
                    if command=='accept':fs.accept(ses,uid,invitation)
                    elif invitation.status=='pending':invitation.status='declined'
                else:
                    group=fs.require_family(ses,uid,live=False)
                    if command=='invite':
                        token=fs.invite(ses,uid,request.form.get('email',''));ses.commit()
                        # Secret link is shown once and not persisted in session or activity history.
                        return render_template('family_invite_created.html',**common('invitations'),
                            invitation_url=url_for('family.join',token=token,_external=True))
                    elif command=='cancel':
                        fs.require_family(ses,uid,owner=True,live=False)
                        invitation=ses.get(FamilyInvitation,integer(request.form.get('invitation_id')))
                        if not invitation or invitation.family_id!=group.id:raise fs.FamilyError('Приглашение не найдено.')
                        if invitation.status=='pending':invitation.status='cancelled'
                    elif command=='rename':
                        fs.require_family(ses,uid,owner=True,live=False)
                        group.name=clean(request.form.get('name',''),60,'Название семьи')
                        fs.event(ses,group,uid,'Изменено название семьи: '+group.name)
                    elif command=='remove':
                        fs.require_family(ses,uid,owner=True,live=False)
                        fs.remove_member(ses,group,uid,integer(request.form.get('user_id')))
                    elif command=='leave':
                        if group.owner_id==uid:raise fs.FamilyError('Владелец может распустить семью, но не выйти из неё.')
                        if request.form.get('confirm')!='yes':raise fs.FamilyError('Подтвердите выход из семьи.')
                        fs.remove_member(ses,group,uid,uid)
                    elif command=='dissolve':
                        fs.require_family(ses,uid,owner=True,live=False)
                        if request.form.get('confirm')!='yes':raise fs.FamilyError('Подтвердите роспуск семьи.')
                        for model in (FamilyMember,FamilyInvitation,FamilyStorage,FamilyShoppingItem,FamilyEvent):
                            ses.execute(delete(model).where(model.family_id==group.id))
                        ses.delete(group)
                    elif command in ('share','unshare'):
                        place=ses.get(StorageLocation,integer(request.form.get('storage_id')))
                        if not place:raise fs.FamilyError('Место не найдено.')
                        if place.owner_id!=uid and group.owner_id!=uid:raise fs.FamilyError('Изменить доступ может владелец места или семьи.')
                        shared=ses.get(FamilyStorage,place.id)
                        if command=='unshare':
                            if not shared or shared.family_id!=group.id:raise fs.FamilyError('Место не найдено.')
                            ses.delete(shared);fs.event(ses,group,uid,'Закрыт общий доступ: '+place.name)
                        else:
                            fs.require_family(ses,uid)
                            if not fs.entitled(ses,group,uid):raise fs.FamilyError('Нет доступного места в семейном тарифе.')
                            if place.owner_id!=uid:raise fs.FamilyError('Открыть доступ может только владелец места.')
                            permission=request.form.get('permission')
                            if permission not in ('view','edit'):raise fs.FamilyError('Выберите права доступа.')
                            if shared and shared.family_id!=group.id:raise fs.FamilyError('Место уже относится к другой семье.')
                            if not shared:shared=FamilyStorage(storage_id=place.id,family_id=group.id);ses.add(shared)
                            shared.permission=permission;fs.event(ses,group,uid,'Общий доступ ('+('изменение' if permission=='edit' else 'просмотр')+'): '+place.name)
                    elif command in ('shopping_add','shopping_toggle','shopping_delete'):
                        fs.require_family(ses,uid)
                        if not fs.entitled(ses,group,uid):raise fs.FamilyError('Нет доступного места в семейном тарифе.')
                        if command=='shopping_add':
                            key=request.form.get('submission_key','')
                            if not re.fullmatch('[a-f0-9]{48}',key):raise fs.FamilyError('Форма устарела. Обновите страницу.')
                            existing=ses.scalar(select(FamilyShoppingItem).where(FamilyShoppingItem.family_id==group.id,FamilyShoppingItem.submission_key==key))
                            if existing:
                                if existing.name!=ps.clean(request.form.get('name','')) or existing.note!=request.form.get('note','').strip():raise fs.FamilyError('Эта форма уже использована. Повторите действие с новой страницы.')
                                return redirect(url_for('family.screen',tab='shopping'))
                            count=ses.scalar(select(func.count()).select_from(FamilyShoppingItem).where(FamilyShoppingItem.family_id==group.id))
                            if count>=500:raise fs.FamilyError('В списке уже 500 записей. Удалите ненужные.')
                            name=clean(request.form.get('name',''),120,'Продукт');note=request.form.get('note','').strip()
                            if len(note)>160:raise fs.FamilyError('Количество или заметка: до 160 символов.')
                            ses.add(FamilyShoppingItem(family_id=group.id,name=name,note=note,created_by=uid,submission_key=key))
                            fs.event(ses,group,uid,'Добавлено в покупки: '+name)
                        else:
                            item=ses.get(FamilyShoppingItem,integer(request.form.get('item_id')))
                            if not item or item.family_id!=group.id:raise fs.FamilyError('Покупка не найдена.')
                            if request.form.get('revision')!=str(item.revision):raise fs.FamilyError('Запись изменилась. Страница обновлена; повторите действие.')
                            if command=='shopping_delete':ses.delete(item)
                            else:item.bought=not item.bought;item.revision+=1
                            fs.event(ses,group,uid,('Удалено из покупок: ' if command=='shopping_delete' else 'Изменена покупка: ')+item.name)
                    else:raise fs.FamilyError('Неизвестное действие.')
                ses.commit()
            flash('Изменения сохранены.','success')
        except fs.FamilyError as exc:flash(str(exc),'error')
        return redirect(url_for('family.screen',tab=tab))

    @family.route('/family/join/<token>')
    @login_required
    def join(token):
        if len(token)>100:abort(404)
        with db_session.create_session() as ses:
            invite=ses.scalar(select(FamilyInvitation).where(FamilyInvitation.token_hash==hashlib.sha256(token.encode()).hexdigest()))
            if not invite or invite.status!='pending' or invite.expires_at<=time.time():
                flash('Приглашение истекло, отменено или уже принято.','error')
                return redirect(url_for('family.screen'))
            if invite.email!=ses.get(User,session['user_id']).email.casefold():
                flash('Приглашение предназначено для другого адреса. Войдите в аккаунт с указанной в нём почтой.','error')
                return redirect(url_for('family.screen'))
        return redirect(url_for('family.screen'))

    @family.route('/family/storage/<int:storage_id>',methods=['GET','POST'])
    @login_required
    def inventory(storage_id):
        uid=session['user_id'];error=None;status=200
        values={key:'' for key in ps.FIELDS};values.update(quantity='1',unit='pcs',shelf_unit='days')
        submission=request.form.get('submission_key','') if request.method=='POST' else secrets.token_hex(24)
        try:
            if request.method=='POST':
                check_form()
                values,parsed,errors=ps.validate(request.form)
                if errors:raise fs.FamilyError(' '.join(errors.values()))
                if not re.fullmatch('[a-f0-9]{48}',submission):raise fs.FamilyError('Форма устарела. Откройте её заново.')
                with db_session.create_session() as ses:
                    ses.execute(text('BEGIN IMMEDIATE'))
                    group,place,shared=fs.storage_access(ses,uid,storage_id,write=True)
                    _,created=ps.save_in_session(ses,place.owner_id,storage_id,submission,values,parsed)
                    if created:fs.event(ses,group,uid,'Добавлен продукт: '+values['name']+' → '+place.name)
                    ses.commit()
                flash('Продукт добавлен.' if created else 'Продукт уже был добавлен.','success')
                return redirect(url_for('family.inventory',storage_id=storage_id))
        except (fs.FamilyError,ps.ProductConflict,ps.StorageMissing) as exc:
            error=str(exc) or 'Запись изменилась. Повторите действие.';status=422
        try:
            with db_session.create_session() as ses:
                group,place,shared=fs.storage_access(ses,uid,storage_id)
                query=ps.clean(request.args.get('q',''))[:100]
                statement=select(ProductGroup,ProductBatch).join(ProductBatch,ProductBatch.group_id==ProductGroup.id).where(
                    ProductGroup.storage_id==storage_id,ProductGroup.owner_id==place.owner_id,
                    ProductBatch.owner_id==place.owner_id,ProductBatch.quantity_micro>0)
                if query:statement=statement.where(ProductGroup.search_key.contains(query.casefold(),autoescape=True))
                rows=ses.execute(statement.order_by(ProductBatch.expiry_date.is_(None),ProductBatch.expiry_date,ProductGroup.name).limit(201)).all()
                entries=[]
                for product,batch in rows[:200]:
                    snapshot=ops.snapshot(batch,product,place)
                    entries.append(dict(product=product,batch=batch,expected=ops.digest(snapshot),key=secrets.token_hex(24)))
                return render_template('family_inventory.html',**common('access'),place=place,entries=entries,
                    query=query,today=ps.today(),truncated=len(rows)>200,can_edit=shared.permission=='edit' or place.owner_id==uid,
                    units=ps.UNITS,values=values,error=error,submission_key=submission),status
        except fs.FamilyError as exc:
            flash(str(exc),'error');return redirect(url_for('family.screen',tab='access'))

    @family.route('/family/storage/<int:storage_id>/use/<int:batch_id>',methods=['POST'])
    @login_required
    def use(storage_id,batch_id):
        check_form();uid=session['user_id']
        def authorize(ses,batch,product,place,record):
            group,shared_place,_=fs.storage_access(ses,uid,storage_id,write=True)
            if product.storage_id!=storage_id or place.id!=shared_place.id:raise fs.FamilyError('Продукт не найден в этом месте.')
            if record:fs.event(ses,group,uid,'Отмечен расход: '+product.name+' · '+request.form.get('quantity','')[:20]+' '+request.form.get('unit','')[:10])
        try:
            with db_session.create_session() as ses:
                _,place,_=fs.storage_access(ses,uid,storage_id,write=True);owner=place.owner_id
            ops.apply(owner,batch_id,'use',request.form.get('submission_key',''),request.form.get('expected',''),
                      dict(quantity=request.form.get('quantity',''),unit=request.form.get('unit',''),note=''),authorize=authorize)
            flash('Расход сохранён.','success')
        except (fs.FamilyError,ops.OperationError) as exc:flash(str(exc),'error')
        return redirect(url_for('family.inventory',storage_id=storage_id))

    @family.after_request
    def private(response):
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='same-origin'
        return response
    app.register_blueprint(family)
