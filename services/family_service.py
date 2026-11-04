"""Live family permissions and serialized, email-bound invitation lifecycle."""
import hashlib
import re
import secrets
import time
from sqlalchemy import select, func, delete
from data import db_session
from data.user import User
from data.billing import Subscription
from data.storage import StorageLocation
from data.family import Family, FamilyMember, FamilyInvitation, FamilyStorage, FamilyEvent
from services.billing_service import PLANS, active


class FamilyError(ValueError):
    pass


def family_for(ses, uid):
    member = ses.get(FamilyMember, uid)
    return ses.get(Family, member.family_id) if member else None


def plan_for(ses, family):
    sub = ses.get(Subscription, family.owner_id) if family else None
    return sub if active(sub) and PLANS.get(sub.plan, {}).get('family') else None


def access_subscription(uid, ses=None):
    if ses is None:
        with db_session.create_session() as own:
            return access_subscription(uid, own)
    personal = ses.get(Subscription, uid)
    group = family_for(ses, uid)
    inherited = plan_for(ses, group)
    if inherited and not entitled(ses,group,uid):inherited=None
    # Family plan supplies access without changing billing ownership or personal renewals.
    if inherited and (not active(personal) or PLANS[inherited.plan]['premium'] or not PLANS.get(personal.plan, {}).get('premium')):
        return inherited
    return personal


def require_family(ses, uid, *, owner=False, live=True):
    family = family_for(ses, uid)
    if not family:
        raise FamilyError('Вы пока не состоите в семье.')
    if owner and family.owner_id != uid:
        raise FamilyError('Это действие доступно только владельцу семьи.')
    if live and not plan_for(ses, family):
        raise FamilyError('Семейная подписка владельца неактивна. Общий доступ приостановлен; данные сохранены.')
    return family


def capacity(ses, family):
    sub = plan_for(ses, family)
    return 5 + (sub.extra_seats or 0) if sub else 0


def members(ses, family):
    # Preserve earliest members on a seat downgrade; the owner always keeps access.
    return list(ses.scalars(select(FamilyMember).where(FamilyMember.family_id == family.id)
                           .order_by((FamilyMember.user_id != family.owner_id), FamilyMember.joined_at, FamilyMember.user_id)))


def entitled(ses, family, uid):
    return uid in [m.user_id for m in members(ses, family)[:capacity(ses, family)]]


def pending(ses, family):
    return list(ses.scalars(select(FamilyInvitation).where(FamilyInvitation.family_id == family.id,
        FamilyInvitation.status == 'pending', FamilyInvitation.expires_at > time.time()).order_by(FamilyInvitation.id)))


def event(ses, family, uid, description, *, storage_id=None):
    from data.activity import ActivityEvent
    ses.add(FamilyEvent(family_id=family.id, actor_id=uid, description=description[:300],storage_id=storage_id))
    ses.add(ActivityEvent(owner_id=uid,category='family',action='family_changed',before={},after={'description':description[:300]}))


def create(ses, uid, name):
    if family_for(ses, uid):
        raise FamilyError('Сначала выйдите из текущей семьи.')
    sub = ses.get(Subscription, uid)
    if not active(sub) or not PLANS.get(sub.plan, {}).get('family'):
        raise FamilyError('Для создания семьи нужна семейная подписка.')
    family = Family(owner_id=uid, name=name)
    ses.add(family); ses.flush()
    ses.add(FamilyMember(user_id=uid, family_id=family.id)); ses.flush()
    event(ses, family, uid, 'Создана семья «'+name+'»')
    return family


def invite(ses, uid, email):
    family = require_family(ses, uid, owner=True)
    email = email.strip().casefold()
    if not email.isascii() or len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise FamilyError('Введите адрес электронной почты.')
    target = ses.scalar(select(User).where(func.lower(User.email) == email))
    if target and family_for(ses, target.id):
        raise FamilyError('Этот пользователь уже состоит в семье.')
    if any(i.email == email for i in pending(ses, family)):
        raise FamilyError('Приглашение на эту почту уже ожидает ответа.')
    if len(members(ses, family)) + len(pending(ses, family)) >= capacity(ses, family):
        raise FamilyError('Свободных мест нет. Отмените приглашение, удалите участника или добавьте места в подписке.')
    token = secrets.token_urlsafe(32)
    invitation = FamilyInvitation(family_id=family.id, email=email,
        token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=time.time()+7*86400)
    ses.add(invitation); event(ses, family, uid, 'Создано приглашение для '+email,storage_id=0)
    return token


def accept(ses, uid, invitation, *, renewal_choice=None, subscription_expected=None):
    user = ses.get(User, uid)
    if not invitation or invitation.status != 'pending' or invitation.expires_at <= time.time():
        raise FamilyError('Приглашение истекло, отменено или уже принято.')
    if user.email.casefold() != invitation.email or not user.is_verified:
        raise FamilyError('Войдите в подтверждённый аккаунт с почтой, указанной в приглашении.')
    if family_for(ses, uid):
        raise FamilyError('Вы уже состоите в семье. Сначала выйдите из неё.')
    family = ses.get(Family, invitation.family_id)
    if not plan_for(ses, family):
        raise FamilyError('Семейная подписка владельца неактивна.')
    if len(members(ses, family)) >= capacity(ses, family):
        raise FamilyError('В семье больше нет свободных мест.')
    personal=ses.get(Subscription,uid)
    if personal and personal.auto_renew:
        if renewal_choice not in ('keep','disable'):
            raise FamilyError('Перед вступлением выберите, сохранять ли автопродление личной подписки.')
        if subscription_signature(personal)!=subscription_expected:
            raise FamilyError('Личная подписка изменилась. Снова откройте условия вступления.')
        if renewal_choice=='disable':stop_personal_renewal(ses,uid,subscription_expected)
    invitation.status = 'accepted'
    # One account can join only one family; free reservations in other families.
    for other in ses.scalars(select(FamilyInvitation).where(FamilyInvitation.email==user.email.casefold(),FamilyInvitation.status=='pending',FamilyInvitation.id!=invitation.id)):
        other.status='declined'
    ses.add(FamilyMember(user_id=uid, family_id=family.id))
    event(ses, family, uid, 'Присоединился к семье')
    return family


def storage_access(ses, uid, storage_id, *, write=False):
    if not 0 < storage_id <= 9223372036854775807:raise FamilyError('Место не найдено.')
    family = require_family(ses, uid)
    if not entitled(ses, family, uid):
        raise FamilyError('Для вашего места не хватает оплаченных мест в семейной подписке.')
    shared = ses.get(FamilyStorage, storage_id)
    place = ses.get(StorageLocation, storage_id)
    if not shared or shared.family_id != family.id or not place or not ses.get(FamilyMember, place.owner_id) or family_for(ses, place.owner_id).id != family.id:
        raise FamilyError('Общий доступ к этому месту закрыт.')
    permission=effective_permission(ses,uid,place,shared)
    if permission=='none':raise FamilyError('Доступ к этому месту для вас закрыт.')
    if write and permission != 'edit':
        raise FamilyError('Для этого места доступен только просмотр.')
    return family, place, shared


def remove_member(ses, family, uid, target):
    member = ses.get(FamilyMember, target)
    if not member or member.family_id != family.id or target == family.owner_id:
        raise FamilyError('Участник не найден или является владельцем.')
    # Unshare their inventory immediately; their products remain in their account.
    from data.family import FamilyStorageAccess
    locations = select(StorageLocation.id).where(StorageLocation.owner_id == target)
    family_places=select(FamilyStorage.storage_id).where(FamilyStorage.family_id==family.id)
    ses.execute(delete(FamilyStorageAccess).where(FamilyStorageAccess.storage_id.in_(family_places),FamilyStorageAccess.user_id==target))
    ses.execute(delete(FamilyStorageAccess).where(FamilyStorageAccess.storage_id.in_(locations)))
    ses.execute(delete(FamilyStorage).where(FamilyStorage.family_id == family.id, FamilyStorage.storage_id.in_(locations)))
    ses.delete(member)
    event(ses, family, uid, 'Участник покинул семью: '+ses.get(User, target).name)


def has_invitation(uid):
    with db_session.create_session() as ses:
        user=ses.get(User,uid)
        return bool(user and ses.scalar(select(FamilyInvitation.id).where(
            FamilyInvitation.email==user.email.casefold(),FamilyInvitation.status=='pending',
            FamilyInvitation.expires_at>time.time()).limit(1)))


def subscription_signature(sub):
    payload=None if sub is None else {key:getattr(sub,key) for key in
        ('plan','status','period_start','period_end','auto_renew','revision','pending_order_id')}
    from services.product_operations import digest
    return digest(payload)


def stop_personal_renewal(ses, uid, signature):
    from data.billing import BillingOrder
    sub=ses.get(Subscription,uid)
    if subscription_signature(sub)!=signature:
        raise FamilyError('Личная подписка изменилась. Проверьте обновлённые условия перед подтверждением.')
    if sub and sub.auto_renew:
        inflight=ses.scalar(select(BillingOrder.id).where(BillingOrder.user_id==uid,
            BillingOrder.kind.in_(['bind','purchase','renewal']),BillingOrder.status.in_(['created','pending'])).limit(1))
        if inflight:
            raise FamilyError('Платёж личной подписки уже обрабатывается. Дождитесь его результата и повторите действие; текущий платёж здесь отменить нельзя.')
        sub.auto_renew=False
        sub.consent_at=time.time()
    return sub


def effective_permission(ses,uid,place,shared):
    from data.family import FamilyStorageAccess
    if place.owner_id==uid:return 'edit'
    if shared is None:return 'none'
    override=ses.get(FamilyStorageAccess,(place.id,uid))
    return override.permission if override else shared.permission


def accessible_places(ses,uid,*,write=False):
    group=family_for(ses,uid)
    if not group or not entitled(ses,group,uid):return []
    rows=ses.execute(select(FamilyStorage,StorageLocation,User.name).join(StorageLocation,StorageLocation.id==FamilyStorage.storage_id)
        .join(User,User.id==StorageLocation.owner_id).where(FamilyStorage.family_id==group.id).order_by(StorageLocation.name_key)).all()
    return [(shared,place,name) for shared,place,name in rows if effective_permission(ses,uid,place,shared) in (['edit'] if write else ['edit','view'])]


def change_version(ses,uid):
    """Opaque revision; never return private product names or another user's billing data."""
    from data.product import ProductMutation
    group=family_for(ses,uid)
    inbox=ses.scalar(select(func.count()).select_from(FamilyInvitation).where(FamilyInvitation.email==ses.get(User,uid).email.casefold(),
        FamilyInvitation.status=='pending',FamilyInvitation.expires_at>time.time()))
    payload={'inbox':inbox,'family':group.id if group else None,'personal':subscription_signature(ses.get(Subscription,uid))}
    if group:
        places=accessible_places(ses,uid)
        ids=[p.id for _,p,_ in places]
        from sqlalchemy import or_
        latest=ses.scalar(select(func.max(ProductMutation.id)).where(or_(
            ProductMutation.before['storage_id'].as_integer().in_(ids),ProductMutation.after['storage_id'].as_integer().in_(ids)))) if ids else None
        payload.update(seats=capacity(ses,group),reserved=[i.id for i in pending(ses,group)],members=[m.user_id for m in members(ses,group)],
            event=ses.scalar(select(func.max(FamilyEvent.id)).where(FamilyEvent.family_id==group.id)),
            stock=latest,places=[(p.id,p.revision,effective_permission(ses,uid,p,share)) for share,p,_ in places])
    from services.product_operations import digest
    return digest(payload)


def history_scope(ses,uid,group):
    from sqlalchemy import or_
    allowed=[place.id for _,place,_ in accessible_places(ses,uid)]
    return (FamilyEvent.family_id==group.id,or_(FamilyEvent.storage_id.is_(None),FamilyEvent.storage_id.in_(allowed),FamilyEvent.actor_id==uid))
