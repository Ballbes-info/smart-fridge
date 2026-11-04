"""Family screens: members, bound invitations, shared inventory and shopping."""
import csv
import io
import hashlib
import re
import secrets
import time
from flask import Blueprint, abort, flash, render_template, redirect, request, session, url_for, jsonify, Response
from sqlalchemy import select, text, delete, func
from data import db_session
from data.user import User
from data.family import Family, FamilyMember, FamilyInvitation, FamilyStorage, FamilyShoppingItem, FamilyEvent, FamilyStorageAccess
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
        if not uid:return {'family_invitation_count':0,'has_family_access':False,'family_version':''}
        with db_session.create_session() as ses:
            user=ses.get(User,uid)
            count=ses.scalar(select(func.count()).select_from(FamilyInvitation).where(
                FamilyInvitation.email==user.email.casefold(),FamilyInvitation.status=='pending',
                FamilyInvitation.expires_at>time.time())) if user else 0
            group=fs.family_for(ses,uid)
            family_access=bool(group and fs.entitled(ses,group,uid))
            version=fs.change_version(ses,uid) if request.blueprint=='family' else ''
        return {'family_invitation_count':count,'has_family_access':family_access,'family_version':version}

    @family.route('/family')
    @family.route('/family/<any(members,invitations,access,shopping):tab>')
    @family.route('/home/family')
    @family.route('/home/family/<any(members,invitations,access,shopping):tab>')
    @login_required
    def screen(tab='members'):
        uid = session['user_id']; context = common(tab) | {'now':time.time()}
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
                can_create=bool(active(own_sub) and PLANS.get(own_sub.plan,{}).get('family')),
                personal_subscription=own_sub,personal_signature=fs.subscription_signature(own_sub))
            if group:
                seats = fs.capacity(ses, group); member_rows=fs.members(ses, group)
                users={m.user_id:ses.get(User,m.user_id) for m in member_rows}
                invitations=fs.pending(ses,group) if group.owner_id==uid else []
                shares=fs.accessible_places(ses,uid)
                own=list(ses.scalars(select(StorageLocation).where(StorageLocation.owner_id==uid).order_by(StorageLocation.name_key)))
                shopping=list(ses.scalars(select(FamilyShoppingItem).where(FamilyShoppingItem.family_id==group.id)
                    .order_by(FamilyShoppingItem.bought,FamilyShoppingItem.created_at.desc()).limit(500))) if seats and fs.entitled(ses,group,uid) else []
                history_count=ses.scalar(select(func.count()).select_from(FamilyEvent).where(*fs.history_scope(ses,uid,group)))
                history_pages=max(1,(history_count+24)//25)
                try:history_page=max(1,min(history_pages,int(request.args.get('history_page',1))))
                except ValueError:history_page=1
                events=list(ses.execute(select(FamilyEvent, User.name).join(User, User.id==FamilyEvent.actor_id)
                    .where(*fs.history_scope(ses,uid,group)).order_by(FamilyEvent.id.desc()).offset((history_page-1)*25).limit(25)))
                context.update(seats=seats, members=member_rows, users=users, invitations=invitations,
                    shares=shares, own_places=own, shopping=shopping,bought_snapshot=[[item.id,item.revision] for item in shopping if item.bought], shared_ids={share.storage_id for share,_,_ in shares},
                    overrides={(o.storage_id,o.user_id):o.permission for o in ses.scalars(select(FamilyStorageAccess).where(FamilyStorageAccess.storage_id.in_([p.id for _,p,_ in shares if p.owner_id==uid])))},
                    permissions={p.id:fs.effective_permission(ses,uid,p,share) for share,p,_ in shares},
                    invitation_history=list(ses.scalars(select(FamilyInvitation).where(FamilyInvitation.family_id==group.id).order_by(FamilyInvitation.id.desc()).limit(30))) if group.owner_id==uid else [],
                    free_seats=max(0,seats-len(member_rows)-len(invitations)),
                    history_page=history_page,history_pages=history_pages,events=events, live=bool(seats), entitled=fs.entitled(ses,group,uid), reserved=len(invitations))
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
                    if command=='accept':fs.accept(ses,uid,invitation,renewal_choice=request.form.get('renewal_choice'),subscription_expected=request.form.get('subscription_expected'))
                    elif invitation.status=='pending':invitation.status='declined'
                else:
                    group=fs.require_family(ses,uid,live=False)
                    if command=='invite':
                        token=fs.invite(ses,uid,request.form.get('email',''));ses.commit()
                        # Secret link is shown once and not persisted in session or activity history.
                        return render_template('family_invite_created.html',**common('invitations'),
                            invitation_url=url_for('family.join',token=token,_external=True))
                    elif command=='reissue':
                        fs.require_family(ses,uid,owner=True)
                        invitation=ses.get(FamilyInvitation,integer(request.form.get('invitation_id')))
                        if not invitation or invitation.family_id!=group.id or invitation.status not in ('pending','declined','cancelled'):
                            raise fs.FamilyError('Приглашение уже принято или не найдено.')
                        email=invitation.email
                        invitation.status='cancelled';ses.flush()
                        token=fs.invite(ses,uid,email);ses.commit()
                        return render_template('family_invite_created.html',**common('invitations'),invitation_url=url_for('family.join',token=token,_external=True))
                    elif command=='stop_personal_renewal':
                        if group.owner_id==uid:raise fs.FamilyError('Подпиской владельца управляют в настройках подписки.')
                        if request.form.get('confirm')!='yes':raise fs.FamilyError('Подтвердите отключение личного автопродления.')
                        fs.stop_personal_renewal(ses,uid,request.form.get('subscription_expected'))
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
                        family_places=select(FamilyStorage.storage_id).where(FamilyStorage.family_id==group.id)
                        ses.execute(delete(FamilyStorageAccess).where(FamilyStorageAccess.storage_id.in_(family_places)))
                        for model in (FamilyMember,FamilyInvitation,FamilyStorage,FamilyShoppingItem,FamilyEvent):
                            ses.execute(delete(model).where(model.family_id==group.id))
                        ses.delete(group)
                    elif command in ('share','unshare','member_access'):
                        place=ses.get(StorageLocation,integer(request.form.get('storage_id')))
                        if not place:raise fs.FamilyError('Место не найдено.')
                        if place.owner_id!=uid and group.owner_id!=uid:raise fs.FamilyError('Изменить доступ может владелец места или семьи.')
                        shared=ses.get(FamilyStorage,place.id)
                        if command=='member_access':
                            fs.require_family(ses,uid)
                            if not fs.entitled(ses,group,uid):raise fs.FamilyError('Общий доступ приостановлен.')
                            if place.owner_id!=uid or not shared or shared.family_id!=group.id:raise fs.FamilyError('Индивидуальные права меняет владелец места.')
                            target=integer(request.form.get('user_id'));member=ses.get(FamilyMember,target)
                            if not member or member.family_id!=group.id or target==uid:raise fs.FamilyError('Выберите другого участника семьи.')
                            permission=request.form.get('permission')
                            if permission not in ('default','none','view','edit'):raise fs.FamilyError('Выберите права доступа.')
                            override=ses.get(FamilyStorageAccess,(place.id,target))
                            if permission=='default':
                                if override:ses.delete(override)
                            else:
                                if not override:override=FamilyStorageAccess(storage_id=place.id,user_id=target);ses.add(override)
                                override.permission=permission
                            fs.event(ses,group,uid,'Изменены права участника '+ses.get(User,target).name+' к месту «'+place.name+'»',storage_id=place.id)
                        elif command=='unshare':
                            if not shared or shared.family_id!=group.id:raise fs.FamilyError('Место не найдено.')
                            ses.execute(delete(FamilyStorageAccess).where(FamilyStorageAccess.storage_id==place.id))
                            ses.delete(shared);fs.event(ses,group,uid,'Закрыт общий доступ: '+place.name,storage_id=place.id)
                        else:
                            fs.require_family(ses,uid)
                            if not fs.entitled(ses,group,uid):raise fs.FamilyError('Нет доступного места в семейном тарифе.')
                            if place.owner_id!=uid:raise fs.FamilyError('Открыть доступ может только владелец места.')
                            permission=request.form.get('permission')
                            if permission not in ('view','edit'):raise fs.FamilyError('Выберите права доступа.')
                            if shared and shared.family_id!=group.id:raise fs.FamilyError('Место уже относится к другой семье.')
                            if not shared:shared=FamilyStorage(storage_id=place.id,family_id=group.id);ses.add(shared)
                            shared.permission=permission;fs.event(ses,group,uid,'Общий доступ ('+('изменение' if permission=='edit' else 'просмотр')+'): '+place.name,storage_id=place.id)
                    elif command in ('shopping_add','shopping_toggle','shopping_delete','shopping_edit','shopping_clear'):
                        fs.require_family(ses,uid)
                        if not fs.entitled(ses,group,uid):raise fs.FamilyError('Нет доступного места в семейном тарифе.')
                        if command=='shopping_clear':
                            if request.form.get('confirm')!='yes':raise fs.FamilyError('Подтвердите очистку купленных товаров.')
                            # Only delete the exact bought revisions shown on the confirmation page.
                            from json import loads
                            try:shown=loads(request.form.get('shown','[]'))
                            except (ValueError,TypeError):raise fs.FamilyError('Обновите список перед очисткой.')
                            if not isinstance(shown,list) or len(shown)>500:raise fs.FamilyError('Обновите список перед очисткой.')
                            for pair in shown:
                                if not isinstance(pair,list) or len(pair)!=2:raise fs.FamilyError('Обновите список перед очисткой.')
                                item=ses.get(FamilyShoppingItem,integer(pair[0]))
                                if item and item.family_id==group.id and item.bought and str(item.revision)==str(pair[1]):ses.delete(item)
                            fs.event(ses,group,uid,'Очищены купленные товары')
                        elif command=='shopping_add':
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
                            elif command=='shopping_edit':
                                item.name=clean(request.form.get('name',''),120,'Продукт')
                                note=request.form.get('note','').strip()
                                if len(note)>160:raise fs.FamilyError('Заметка: до 160 символов.')
                                item.note=note;item.revision+=1
                            else:item.bought=not item.bought;item.revision+=1
                            fs.event(ses,group,uid,('Удалено из покупок: ' if command=='shopping_delete' else 'Изменена покупка: ')+item.name)
                    else:raise fs.FamilyError('Неизвестное действие.')
                ses.commit()
            flash('Изменения сохранены.','success')
        except fs.FamilyError as exc:flash(str(exc),'error')
        if command=='accept':
            try:review_id=integer(request.form.get('invitation_id'))
            except fs.FamilyError:return redirect(url_for('family.screen',tab='invitations'))
            return redirect(url_for('family.review_invitation',invitation_id=review_id)) if fs.has_invitation(uid) else redirect(url_for('family.screen'))
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
        return redirect(url_for('family.review_invitation',invitation_id=invite.id))

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
                    if created:fs.event(ses,group,uid,'Добавлен продукт: '+values['name']+' → '+place.name,storage_id=place.id)
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
                state=request.args.get('state','')
                if state not in ('','due','unknown','expired'):state=''
                if state=='unknown':statement=statement.where(ProductBatch.expiry_date.is_(None))
                elif state=='expired':statement=statement.where(ProductBatch.expiry_date<ps.today())
                elif state=='due':
                    from datetime import timedelta
                    statement=statement.where(ProductBatch.expiry_date<=ps.today()+timedelta(days=3))
                total=ses.scalar(select(func.count()).select_from(statement.subquery()))
                pages=max(1,(total+29)//30)
                try:page=max(1,min(pages,int(request.args.get('page',1))))
                except ValueError:page=1
                rows=ses.execute(statement.order_by(ProductBatch.expiry_date.is_(None),ProductBatch.expiry_date,ProductGroup.name,ProductBatch.id).offset((page-1)*30).limit(30)).all()
                group_ids={product.id for product,batch in rows}
                group_totals=dict(ses.execute(select(ProductBatch.group_id,func.sum(ProductBatch.quantity_micro)).where(
                    ProductBatch.group_id.in_(group_ids),ProductBatch.owner_id==place.owner_id,ProductBatch.quantity_micro>0).group_by(ProductBatch.group_id)).all())
                entries=[]
                for product,batch in rows:
                    snapshot=ops.snapshot(batch,product,place)
                    entries.append(dict(product=product,batch=batch,group_total=group_totals.get(product.id,0),expected=ops.digest(snapshot),key=secrets.token_hex(24)))
                return render_template('family_inventory.html',**(common('access') | {'workspace_back_url':url_for('family.screen',tab='access')}),place=place,entries=entries,
                    query=query,today=ps.today(),total=total,page=page,pages=pages,state=state,
                    can_edit=fs.effective_permission(ses,uid,place,shared)=='edit',
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
            if record:fs.event(ses,group,uid,'Отмечен расход: '+product.name+' · '+request.form.get('quantity','')[:20]+' '+request.form.get('unit','')[:10],storage_id=place.id)
        try:
            with db_session.create_session() as ses:
                _,place,_=fs.storage_access(ses,uid,storage_id,write=True);owner=place.owner_id
            ops.apply(owner,batch_id,'use',request.form.get('submission_key',''),request.form.get('expected',''),
                      dict(quantity=request.form.get('quantity',''),unit=request.form.get('unit',''),note=''),authorize=authorize)
            flash('Расход сохранён.','success')
        except (fs.FamilyError,ops.OperationError) as exc:flash(str(exc),'error')
        return redirect(url_for('family.inventory',storage_id=storage_id))

    @family.route('/family/invitation/<int:invitation_id>/accept')
    @login_required
    def review_invitation(invitation_id):
        if not 0<invitation_id<=9223372036854775807:abort(404)
        from data.billing import Subscription, BillingOrder
        from services.billing_service import PLANS,active
        with db_session.create_session() as ses:
            invite=ses.get(FamilyInvitation,integer(invitation_id))
            if not invite or invite.email!=ses.get(User,session['user_id']).email.casefold():abort(404)
            group=ses.get(Family,invite.family_id)
            if invite.status!='pending' or invite.expires_at<=time.time() or not group:
                flash('Приглашение истекло, отменено или уже принято.','error')
                return redirect(url_for('family.screen',tab='invitations'))
            sub=ses.get(Subscription,session['user_id']);family_sub=fs.plan_for(ses,group)
            inflight=ses.scalar(select(BillingOrder.id).where(BillingOrder.user_id==session['user_id'],BillingOrder.kind.in_(['bind','purchase','renewal']),BillingOrder.status.in_(['created','pending'])).limit(1))
            return render_template('family_accept.html',**common('invitations'),invitation=invite,invited_family=group,
                owner=ses.get(User,group.owner_id),personal_subscription=sub,personal_signature=fs.subscription_signature(sub),
                personal_active=active(sub),plans=PLANS,family_subscription=family_sub,inflight=bool(inflight),
                can_join=bool(family_sub and not fs.family_for(ses,session['user_id']) and len(fs.members(ses,group))<fs.capacity(ses,group)))

    @family.route('/family/updates')
    @login_required
    def updates():
        with db_session.create_session() as ses:return jsonify(version=fs.change_version(ses,session['user_id']))

    @family.route('/family/history.csv')
    @login_required
    def export_history():
        with db_session.create_session() as ses:
            try:group=fs.require_family(ses,session['user_id'],live=False)
            except fs.FamilyError:abort(403)
            rows=ses.execute(select(FamilyEvent,User.name).join(User,User.id==FamilyEvent.actor_id).where(*fs.history_scope(ses,session['user_id'],group)).order_by(FamilyEvent.id.desc()).limit(5001)).all()
            if len(rows)>5000:abort(413,description='История превышает 5000 записей.')
        from datetime import datetime,timezone,timedelta
        def cell(value):
            value=str(value)
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        stream=io.StringIO();writer=csv.writer(stream,delimiter=';');writer.writerow(['Дата МСК','Участник','Действие'])
        for event,name in rows:writer.writerow([datetime.fromtimestamp(event.created_at,timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M'),cell(name),cell(event.description)])
        return Response('\ufeff'+stream.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="family-history.csv"','X-Content-Type-Options':'nosniff'})

    @family.route('/family/storage/new',methods=['GET','POST'])
    @login_required
    def create_storage():
        from services.storage import read_form,quota,KINDS
        values=dict(name='',kind='fridge',location='',note='');errors={}
        try:
            with db_session.create_session() as ses:
                group=fs.require_family(ses,session['user_id'])
                if not fs.entitled(ses,group,session['user_id']):raise fs.FamilyError('Недостаточно оплаченных мест в семье.')
            if request.method=='POST':
                check_form();values,errors=read_form();permission=request.form.get('permission','view')
                if permission not in ('view','edit'):errors['permission']='Выберите права семьи.'
                if not errors:
                    from sqlalchemy.exc import IntegrityError
                    try:
                        with db_session.create_session() as ses:
                            ses.execute(text('BEGIN IMMEDIATE'));group=fs.require_family(ses,session['user_id'])
                            if not fs.entitled(ses,group,session['user_id']):raise fs.FamilyError('Доступ к семье изменился.')
                            used,limit=quota(ses)
                            if used>=limit:raise fs.FamilyError('Достигнут лимит мест хранения семьи: '+str(limit)+'.')
                            place=StorageLocation(owner_id=session['user_id'],name_key=values['name'].casefold(),**values)
                            ses.add(place);ses.flush();ses.add(FamilyStorage(storage_id=place.id,family_id=group.id,permission=permission))
                            fs.event(ses,group,session['user_id'],'Создан общий холодильник: '+place.name,storage_id=place.id);ses.commit();place_id=place.id
                        return redirect(url_for('family.inventory',storage_id=place_id))
                    except IntegrityError:errors['name']='У вас уже есть место с таким названием.'
            return render_template('family_storage_form.html',**common('access'),values=values,errors=errors,kinds=KINDS),422 if errors else 200
        except fs.FamilyError as exc:
            flash(str(exc),'error');return redirect(url_for('family.screen',tab='access'))

    @family.route('/family/storage/<int:storage_id>/batch/<int:batch_id>/<any(edit,waste,move,delete):operation>',methods=['GET','POST'])
    @login_required
    def batch_operation(storage_id,batch_id,operation):
        uid=session['user_id'];error=None
        try:
            with db_session.create_session() as ses:
                group,place,_=fs.storage_access(ses,uid,storage_id,write=True)
                batch,product,batch_place=ops.owned(ses,place.owner_id,batch_id)
                if product.storage_id!=storage_id:raise fs.FamilyError('Продукт не найден в этом месте.')
                expected=ops.digest(ops.snapshot(batch,product,place));values=ops.values_for(batch,product)
                destinations=[p for _,p,_ in fs.accessible_places(ses,uid,write=True) if p.owner_id==place.owner_id and p.id!=place.id]
                owner_id=place.owner_id;product_name=product.name;dimension=product.dimension
            key=secrets.token_hex(24)
            if request.method=='POST':
                check_form();key=request.form.get('submission_key','');expected=request.form.get('expected','')
                params={}
                if operation=='edit':
                    values,parsed,errors=ps.validate(request.form,allow_zero=True)
                    if errors:raise fs.FamilyError(' '.join(errors.values()))
                    params=dict(values=values,parsed=parsed,storage_id=storage_id)
                elif operation=='delete':params=dict(confirm=request.form.get('confirm'))
                else:
                    params=dict(quantity=request.form.get('quantity',''),unit=request.form.get('unit',''),reason=request.form.get('reason',''),note=request.form.get('note',''))
                    if operation=='move':params['storage_id']=integer(request.form.get('destination'))
                def authorize(ses,current,product,source,record):
                    family_group,_,_=fs.storage_access(ses,uid,storage_id,write=True)
                    if product.storage_id!=storage_id:raise fs.FamilyError('Продукт перемещён. Обновите страницу.')
                    if operation=='move':
                        _,target,_=fs.storage_access(ses,uid,params['storage_id'],write=True)
                        if target.owner_id!=owner_id:raise fs.FamilyError('Выберите общее место того же владельца.')
                    if record:fs.event(ses,family_group,uid,{'edit':'Изменён продукт','waste':'Списан продукт','move':'Перемещён продукт','delete':'Удалено поступление'}[operation]+': '+product.name+' · '+source.name,storage_id=source.id)
                ops.apply(owner_id,batch_id,operation,key,expected,params,authorize=authorize)
                flash('Изменения сохранены.','success')
                return redirect(url_for('family.inventory',storage_id=storage_id))
            return render_template('family_batch_operation.html',**(common('access')|{'workspace_back_url':url_for('family.inventory',storage_id=storage_id)}),
                operation=operation,place=place,product_name=product_name,values=values,dimension=dimension,units=ps.UNITS,
                destinations=destinations,expected=expected,submission_key=key,nutrients=ps.NUTRIENTS,bases=ps.BASES)
        except (fs.FamilyError,ops.OperationError) as exc:
            if request.method=='POST' and 'product_name' in locals():
                return render_template('family_batch_operation.html',**(common('access')|{'workspace_back_url':url_for('family.inventory',storage_id=storage_id)}),
                    operation=operation,place=place,product_name=product_name,values=values,dimension=dimension,units=ps.UNITS,
                    destinations=destinations,expected=expected,submission_key=key,nutrients=ps.NUTRIENTS,bases=ps.BASES,error=str(exc)),getattr(exc,'status',422)
            flash(str(exc),'error');return redirect(url_for('family.inventory',storage_id=storage_id))

    @family.after_request
    def private(response):
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='same-origin'
        return response
    app.register_blueprint(family)
