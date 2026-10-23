"""Atomic per-mode allowances, subscription anniversaries and paid extra credits."""
import calendar
import os
import re
import time
import uuid
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,func,text,delete
from data import db_session
from data.billing import Subscription,BillingOrder
from data.product import ProductScanUsage
from data.scan_credits import ScanRequest,ScanCreditPack
from services.billing_service import PLANS,active
from services.local_gateway import PaymentError,demo_cards

DEFAULTS={'label':{'basic':10,'family_basic':30,'premium':100,'family_premium':300},
          'product':{'basic':0,'family_basic':0,'premium':20,'family_premium':60}}
PACKAGES={'label50':{'kind':'label','quantity':50,'amount':4900},
          'label150':{'kind':'label','quantity':150,'amount':12900},
          'product10':{'kind':'product','quantity':10,'amount':4900},
          'product30':{'kind':'product','quantity':30,'amount':12900}}
NAMES={'label':'ИИ-этикетки','product':'Продукт по фото'}
MSK=timezone(timedelta(hours=3))


class LimitReached(Exception):pass


def period(sub,now):
    if not sub or not sub.period_start:return now,now
    anchor=datetime.fromtimestamp(sub.period_start,MSK)
    current=datetime.fromtimestamp(now,MSK)
    def at(offset):
        year,month=divmod(anchor.year*12+anchor.month-1+offset,12);month+=1
        return anchor.replace(year=year,month=month,day=min(anchor.day,calendar.monthrange(year,month)[1])).timestamp()
    offset=max(0,(current.year-anchor.year)*12+current.month-anchor.month)
    if offset and at(offset)>now:offset-=1
    start=at(offset);end=min(at(offset+1),sub.period_end or at(offset+1))
    return start,end


def occupied():
    return (ScanRequest.status=='used') | (ScanRequest.created_at>time.time()-600)


def allowance(user_id,kind='label',*,ses=None):
    if kind not in DEFAULTS:raise ValueError('Unknown recognition mode')
    if ses is None:
        with db_session.create_session() as db:return allowance(user_id,kind,ses=db)
    now=time.time();sub=ses.get(Subscription,user_id);plan=sub.plan if sub and sub.plan in PLANS else 'basic'
    eligible=active(sub,now) and (kind=='label' or PLANS[plan]['premium'])
    key=('GIGACHAT_LIMIT_' if kind=='label' else 'GIGACHAT_PRODUCT_LIMIT_')+plan.upper()
    try:limit=max(0,min(10000,int(os.environ.get(key,DEFAULTS[kind][plan]))))
    except ValueError:limit=DEFAULTS[kind][plan]
    if not eligible:limit=0
    start,end=period(sub,now)
    used=ses.scalar(select(func.count()).select_from(ScanRequest).where(
        ScanRequest.user_id==user_id,ScanRequest.kind==kind,ScanRequest.period_start==start,
        ScanRequest.credit_pack_id.is_(None),occupied()))
    # Older versions stored all modes together. Preserve that historical spending
    # against labels only until the subscription's current monthly window ends.
    if kind=='label':
        used+=ses.scalar(select(func.count()).select_from(ProductScanUsage).where(
            ProductScanUsage.user_id==user_id,ProductScanUsage.created_at>=start,
            ProductScanUsage.created_at<end,
            (ProductScanUsage.status=='used') | (ProductScanUsage.created_at>now-600)))
    packs=list(ses.scalars(select(ScanCreditPack).join(BillingOrder,BillingOrder.id==ScanCreditPack.order_id).where(
        ScanCreditPack.user_id==user_id,ScanCreditPack.kind==kind,BillingOrder.status=='succeeded')))
    extra=0
    for pack in packs:
        spent=ses.scalar(select(func.count()).select_from(ScanRequest).where(ScanRequest.credit_pack_id==pack.order_id,occupied()))
        extra+=max(0,pack.quantity-spent)
    included=max(0,limit-used)
    return dict(kind=kind,limit=limit,used=used,included_remaining=included,extra_remaining=extra,
                remaining=included+(extra if eligible else 0),resets_at=end,period_start=start,
                month=datetime.fromtimestamp(start,MSK).strftime('%Y-%m'),configured=bool(os.environ.get('GIGACHAT_CREDENTIALS','').strip()))


def reserve(user_id,kind='label'):
    with db_session.create_session() as ses:
        ses.execute(text('BEGIN IMMEDIATE'))
        ses.execute(delete(ScanRequest).where(ScanRequest.status=='pending',ScanRequest.created_at<time.time()-600))
        quota=allowance(user_id,kind,ses=ses)
        if quota['remaining']<=0:raise LimitReached('Лимит этого способа исчерпан. Дождитесь обновления или купите пакет распознаваний.')
        pack_id=None
        if not quota['included_remaining']:
            packs=list(ses.scalars(select(ScanCreditPack).join(BillingOrder,BillingOrder.id==ScanCreditPack.order_id).where(
                ScanCreditPack.user_id==user_id,ScanCreditPack.kind==kind,BillingOrder.status=='succeeded')
                .order_by(ScanCreditPack.created_at,ScanCreditPack.order_id)))
            for pack in packs:
                spent=ses.scalar(select(func.count()).select_from(ScanRequest).where(ScanRequest.credit_pack_id==pack.order_id,occupied()))
                if spent<pack.quantity:pack_id=pack.order_id;break
            if pack_id is None:raise LimitReached('Пакет распознаваний закончился.')
        key=uuid.uuid4().hex
        ses.add(ScanRequest(id=key,user_id=user_id,kind=kind,period_start=quota['period_start'],
                            credit_pack_id=pack_id,status='pending',created_at=time.time()));ses.commit()
        return key


def finish(key,success):
    with db_session.create_session() as ses:
        item=ses.get(ScanRequest,key)
        if item:
            if success:item.status='used'
            else:ses.delete(item)
            ses.commit()


def new_order(user_id,package_id,submission):
    package=PACKAGES.get(package_id)
    if not package or not re.fullmatch('[a-f0-9]{48}',submission):raise PaymentError('Проверьте выбранный пакет и обновите страницу.')
    with db_session.create_session() as ses:
        ses.execute(text('BEGIN IMMEDIATE'))
        old=ses.scalar(select(ScanCreditPack).where(ScanCreditPack.user_id==user_id,ScanCreditPack.submission_key==submission))
        if old:
            if old.package_id!=package_id:raise PaymentError('Эта форма уже использована для другого пакета.')
            return old.order_id
        sub=ses.get(Subscription,user_id)
        if not active(sub):raise PaymentError('Для покупки пакета нужна активная подписка.')
        if package['kind']=='product' and not PLANS[sub.plan]['premium']:raise PaymentError('Пакеты продуктов по фото доступны только с Премиумом.')
        # Resume an existing unfinished checkout rather than creating accidental duplicates.
        pending=ses.scalar(select(ScanCreditPack).join(BillingOrder,BillingOrder.id==ScanCreditPack.order_id).where(
            ScanCreditPack.user_id==user_id,ScanCreditPack.package_id==package_id,
            BillingOrder.status.in_(['ready','created','pending'])).order_by(ScanCreditPack.created_at.desc()))
        if pending:return pending.order_id
        key=uuid.uuid4().hex;now=time.time()
        ses.add(BillingOrder(id=key,user_id=user_id,kind='scan_pack',plan=sub.plan,interval=sub.interval,
                            amount=package['amount'],regular_amount=package['amount'],status='ready',
                            subscription_revision=sub.revision,created_at=now))
        ses.add(ScanCreditPack(order_id=key,user_id=user_id,kind=package['kind'],package_id=package_id,
                              quantity=package['quantity'],submission_key=submission,created_at=now));ses.commit()
        return key


def confirm_order(order_id,user_id,card,expected_amount,gateway,return_url):
    from services.billing_service import start_payment
    selected=next((c for c in demo_cards(user_id) if c['alias']==card),None)
    if not selected:raise PaymentError('Выберите карту.')
    with db_session.create_session() as ses:
        ses.execute(text('BEGIN IMMEDIATE'));order=ses.get(BillingOrder,order_id);pack=ses.get(ScanCreditPack,order_id)
        if not order or order.user_id!=user_id or not pack or order.kind!='scan_pack':raise PaymentError('Пакет не найден.')
        if order.status in ['created','pending','succeeded']:return order.id
        if order.created_at<time.time()-23*3600:raise PaymentError('Срок покупки истёк. Отмените покупку и выберите пакет снова.')
        if order.status!='ready' or order.amount!=expected_amount:raise PaymentError('Условия операции изменились. Проверьте сумму.')
        sub=ses.get(Subscription,user_id)
        if not active(sub) or (pack.kind=='product' and not PLANS[sub.plan]['premium']):raise PaymentError('Для этого пакета нужна активная подходящая подписка.')
        order.method_id=card;order.card_last4=selected['last4'];order.status='created';ses.commit()
    start_payment(order_id,gateway,return_url)
    return order_id
