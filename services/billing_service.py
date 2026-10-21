"""Prices, test checkout, promotion history and recurring subscription lifecycle."""
import calendar
import hashlib
import hmac
import math
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import text
from data import db_session
from data.billing import Subscription, CardPromotion, BillingOrder
from services.local_gateway import PaymentError

PLANS = {
    'basic': {'name': 'Базовая', 'month': 199, 'year': 1990, 'family': False, 'premium': False},
    'family_basic': {'name': 'Семейная базовая', 'month': 349, 'year': 3490, 'family': True, 'premium': False},
    'premium': {'name': 'Премиум', 'month': 499, 'year': 4990, 'family': False, 'premium': True},
    'family_premium': {'name': 'Семейная премиум', 'month': 799, 'year': 7990, 'family': True, 'premium': True},
}


def regular_price(plan, interval, extra=0):
    if plan not in PLANS or interval not in ('month', 'year'):
        raise PaymentError('Выберите тариф и период оплаты.')
    if not isinstance(extra, int) or extra < 0 or extra > 95 or (extra and not PLANS[plan]['family']):
        raise PaymentError('Проверьте количество дополнительных мест (от 0 до 95).')
    seat = (99 if PLANS[plan]['premium'] else 49) * (10 if interval == 'year' else 1)
    return (PLANS[plan][interval] + seat * extra) * 100


def first_price(plan, interval, extra=0):
    if interval == 'year':
        return regular_price(plan, interval, extra)
    # Discount only the plan, not the additional seats. Round to whole rubles.
    discounted = int((Decimal(PLANS[plan]['month']) * Decimal('.7')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return regular_price(plan, interval, extra) - (PLANS[plan]['month'] - discounted) * 100


def add_period(timestamp, interval):
    value = datetime.fromtimestamp(timestamp, timezone.utc)
    year = value.year + (1 if interval == 'year' else (value.month == 12))
    month = value.month if interval == 'year' else value.month % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1])).timestamp()


def active(subscription, now=None):
    return bool(subscription and subscription.status in ('active', 'trialing') and
        subscription.period_end and subscription.period_end > (time.time() if now is None else now))


def get_subscription(user_id):
    with db_session.create_session() as ses:
        return ses.get(Subscription, user_id)


def ensure_subscription(ses, user_id):
    sub = ses.get(Subscription, user_id)
    if sub is None:
        sub = Subscription(user_id=user_id, status='inactive', interval='month',
            extra_seats=0, cycle_amount=0, trial_used=False, first_paid=False,
            auto_renew=False, revision=0)
        ses.add(sub)
        ses.flush()
    return sub


def card_identity(method, secret):
    fingerprint = method.get('simulation_fingerprint')
    if method.get('type') != 'bank_card' or not method.get('saved') or not method.get('id') or not fingerprint:
        raise PaymentError('Нужна подтверждённая демо-карта.')
    return hmac.new(secret.encode(), ('local:'+fingerprint).encode(), hashlib.sha256).hexdigest()


def discount_allowed(sub, card):
    return not sub.first_paid and not card.discount_used and (card.discount_owner == sub.user_id or (card.discount_owner is None and not card.first_paid))


def trial_allowed(sub, card, plan):
    return not PLANS[plan]['premium'] and not sub.trial_used and not sub.first_paid and not card.trial_used and not card.first_paid


def quote(sub, card, plan, interval, extra, now=None):
    now = time.time() if now is None else now
    total = regular_price(plan, interval, extra)
    amount, discounted, keeps = total, False, False
    start, end = now, add_period(now, interval)
    if active(sub, now) and sub.status == 'active':
        old_total = regular_price(sub.plan, sub.interval, sub.extra_seats)
        remaining = max(0, (sub.period_end-now)/(sub.period_end-sub.period_start))
        if sub.interval == interval:
            amount = math.ceil(max(0, total-old_total)*remaining)
            keeps = True
            start, end = sub.period_start, sub.period_end
        else:
            # A one-time deduction, not a balance. Excess credit is forfeited.
            amount = max(0, total-math.floor(sub.cycle_amount*remaining))
    elif interval == 'month' and discount_allowed(sub, card):
        amount, discounted = first_price(plan, interval, extra), True
    return dict(amount=amount, regular_amount=total, discounted=discounted,
        starts_at=start, ends_at=end, keeps_period=keeps)


def _locked_session():
    ses = db_session.create_session()
    ses.execute(text('BEGIN IMMEDIATE'))
    return ses


def new_checkout(user_id, plan, interval, extra, gateway, return_url):
    regular_price(plan, interval, extra)
    with _locked_session() as ses:
        sub = ensure_subscription(ses, user_id)
        if sub.pending_order_id:
            return sub.pending_order_id
        order = BillingOrder(id=uuid.uuid4().hex, user_id=user_id, kind='bind', plan=plan,
            interval=interval, extra_seats=extra, amount=100, regular_amount=0,
            subscription_revision=sub.revision, created_at=time.time(), status='created')
        # Always verify a card before this checkout, including switching plans.
        ses.add(order); sub.pending_order_id = order.id; ses.commit()
        order_id = order.id
    return start_payment(order_id, gateway, return_url)


def start_payment(order_id, gateway, return_url):
    with db_session.create_session() as ses:
        order = ses.get(BillingOrder, order_id)
        if order.provider_id or order.status in ('succeeded', 'canceled', 'rejected', 'ready'):
            return order.id
        if order.created_at < time.time() - 23*3600:
            raise PaymentError('Срок операции истёк. Свяжитесь с поддержкой для проверки платежа.')
        payload = {'amount': {'value': f'{order.amount / 100:.2f}', 'currency': 'RUB'},
            'capture': True, 'description': 'SmartFridge: проверка карты (1 ₽ с возвратом)' if order.kind == 'bind' else 'SmartFridge: тестовая подписка',
            'metadata': {'order_id': order.id, 'user_id': str(order.user_id)}}
        if order.kind == 'bind':
            payload.update(payment_method_data={'type':'bank_card'}, save_payment_method=True,
                confirmation={'type':'redirect', 'return_url': return_url(order.id)})
        else:
            payload['payment_method_id'] = order.method_id
        obj = gateway.create_payment(payload, 'pay-' + order.kind + '-' + order.id)
        if not obj.get('id') or obj.get('metadata', {}).get('order_id') != order.id:
            raise PaymentError('Некорректный ответ платёжного сервиса.')
        url = obj.get('confirmation', {}).get('confirmation_url')
        if url and not re.fullmatch(r'/payment/local_[a-f0-9]{32}', url):
            raise PaymentError('Неизвестный адрес страницы оплаты.')
        order.provider_id = obj['id']; order.confirmation_url = url
        order.status = 'pending'; ses.commit()
        return order.id


def get_order(order_id, user_id=None):
    with db_session.create_session() as ses:
        order = ses.get(BillingOrder, order_id)
        return order if order and (user_id is None or order.user_id == user_id) else None


def refresh_order(order_id, gateway, secret, return_url):
    order = get_order(order_id)
    if order is None:
        raise PaymentError('Операция не найдена.')
    if order.status in ('succeeded', 'canceled', 'rejected', 'ready'):
        return order.status
    if not order.provider_id:
        start_payment(order_id, gateway, return_url)
        order = get_order(order_id)
    obj = gateway.payment(order.provider_id)
    if obj.get('metadata', {}).get('order_id') != order.id or obj.get('amount', {}).get('currency') != 'RUB' or Decimal(obj.get('amount', {}).get('value', '-1')) * 100 != order.amount:
        raise PaymentError('Данные платежа не совпадают с заказом.')
    status = obj.get('status')
    if status == 'succeeded' and obj.get('paid') is not True:
        raise PaymentError('Оплата ещё не подтверждена.')
    if status not in ('succeeded', 'canceled'):
        return 'pending'
    if order.kind == 'bind' and status == 'succeeded':
        # Idempotent refund; trial never starts until the test ruble is refunded.
        if not order.refund_id:
            refund = gateway.create_refund(order.provider_id, 'refund-' + order.id)
            with db_session.create_session() as ses:
                saved = ses.get(BillingOrder, order.id); saved.refund_id = refund['id']; ses.commit()
            order = get_order(order.id)
        refund = gateway.refund(order.refund_id)
        if refund.get('payment_id') != order.provider_id or refund.get('amount', {}).get('currency') != 'RUB' or Decimal(refund.get('amount', {}).get('value', '-1')) != 1:
            raise PaymentError('Не удалось подтвердить возврат проверочного платежа.')
        if refund.get('status') != 'succeeded':
            return 'pending'
        try:
            binding_key = card_identity(obj.get('payment_method', {}), secret)
        except PaymentError:
            with _locked_session() as ses:
                saved = ses.get(BillingOrder, order.id)
                if saved.status not in ('succeeded','ready'):
                    saved.status = 'rejected'; saved.finished_at = time.time()
                    sub = ses.get(Subscription, order.user_id)
                    if sub.pending_order_id == order.id:
                        sub.pending_order_id = None
                    ses.commit()
            raise
    with _locked_session() as ses:
        saved = ses.get(BillingOrder, order.id)
        if saved.status in ('succeeded', 'canceled', 'rejected', 'ready'):
            return saved.status
        sub = ses.get(Subscription, order.user_id)
        if status == 'canceled':
            saved.status, saved.finished_at = 'canceled', time.time()
            saved.error = {'insufficient_funds':'Недостаточно средств', 'declined':'Платёж отклонён', 'canceled_by_user':'Платёж отменён'}.get(obj.get('cancellation_details',{}).get('reason'), 'Оплата не завершена')
            if saved.kind == 'renewal' and sub.period_end <= time.time():
                sub.status = 'past_due'
            if sub.pending_order_id == saved.id:
                sub.pending_order_id = None
            card = ses.get(CardPromotion, saved.card_key) if saved.card_key else None
            if card and card.reserved_order_id == saved.id:
                card.reserved_order_id = None
            ses.commit(); return 'canceled'
        if order.kind == 'bind':
            method = obj.get('payment_method', {})
            key = binding_key
            card = ses.get(CardPromotion, key)
            if card is None:
                card = CardPromotion(key=key, trial_used=False, first_paid=False, discount_used=False); ses.add(card)
            saved.card_key, saved.method_id = key, method['id']
            saved.card_last4 = method['card']['last4']
            saved.status = 'ready'
        else:
            _apply_payment(ses, saved, sub)
        ses.commit()
        return saved.status


def _apply_payment(ses, order, sub):
    if sub.revision != order.subscription_revision:
        raise PaymentError('Состояние подписки изменилось. Требуется проверка операции.')
    card = ses.get(CardPromotion, order.card_key)
    if (order.kind == 'purchase' and not order.keeps_period) or order.kind == 'renewal':
        order.starts_at = time.time()
        order.ends_at = add_period(order.starts_at, order.interval)
    sub.plan, sub.interval, sub.extra_seats = order.plan, order.interval, order.extra_seats
    sub.card_key, sub.method_id, sub.card_last4 = order.card_key, order.method_id, order.card_last4
    sub.status = 'active'
    sub.period_start, sub.period_end = order.starts_at, order.ends_at
    # Total actual spending of this cycle for a later change of billing interval.
    sub.cycle_amount = sub.cycle_amount + order.amount if order.keeps_period else order.amount
    sub.trial_used = sub.first_paid = True
    if order.kind != 'renewal' and (order.consent_at or 0) >= (sub.consent_at or 0):
        sub.auto_renew = True
        sub.consent_at = order.consent_at
    sub.revision += 1; sub.pending_order_id = None
    card.trial_used = card.first_paid = True
    if card.discount_owner in (None, sub.user_id):
        card.discount_used = True
    if card.reserved_order_id == order.id:
        card.reserved_order_id = None
    order.status, order.finished_at = 'succeeded', time.time()


def checkout_details(order_id, user_id):
    with db_session.create_session() as ses:
        order = ses.get(BillingOrder, order_id)
        if not order or order.user_id != user_id:
            raise PaymentError('Операция не найдена.')
        sub = ses.get(Subscription, user_id)
        card = ses.get(CardPromotion, order.card_key) if order.card_key else None
        if not card:
            return order, sub, None, False
        return order, sub, quote(sub, card, order.plan, order.interval, order.extra_seats), trial_allowed(sub, card, order.plan)


def confirm_checkout(order_id, user_id, wants_trial, expected_amount, gateway, return_url):
    with _locked_session() as ses:
        order = ses.get(BillingOrder, order_id)
        if not order or order.user_id != user_id:
            raise PaymentError('Операция не найдена.')
        if order.status == 'succeeded':
            return order.id
        if order.kind != 'bind' or order.status != 'ready':
            raise PaymentError('Сначала подтвердите карту.')
        sub = ses.get(Subscription, user_id)
        if sub.pending_order_id != order.id or sub.revision != order.subscription_revision:
            raise PaymentError('Выбранный план уже изменился. Начните оформление заново.')
        card = ses.get(CardPromotion, order.card_key)
        if card.reserved_order_id and card.reserved_order_id != order.id:
            raise PaymentError('По этой карте уже проверяется другая оплата. Дождитесь её завершения.')
        if wants_trial:
            if not trial_allowed(sub, card, order.plan):
                raise PaymentError('Бесплатная неделя уже использована или недоступна для этого тарифа.')
            sub.plan, sub.interval, sub.extra_seats = order.plan, order.interval, order.extra_seats
            sub.card_key, sub.method_id, sub.card_last4 = order.card_key, order.method_id, order.card_last4
            sub.status = 'trialing'; sub.period_start = time.time(); sub.period_end = sub.period_start + 7*86400
            sub.trial_used = card.trial_used = True
            if order.interval == 'month' and discount_allowed(sub, card):
                card.discount_owner = sub.user_id
            sub.cycle_amount = 0; sub.auto_renew = True; sub.consent_at = time.time()
            sub.pending_order_id = None; sub.revision += 1
            order.status = 'succeeded'; order.finished_at = time.time(); order.wants_trial = True
            ses.commit(); return order.id
        details = quote(sub, card, order.plan, order.interval, order.extra_seats)
        # Price may decrease by a kopeck with elapsed time. Never charge more than shown.
        if details['amount'] > expected_amount:
            raise PaymentError('Условия скидки изменились. Проверьте новую сумму и подтвердите снова.')
        order.kind = 'purchase'; order.status = 'created'; order.provider_id = None
        order.refund_id = None; order.confirmation_url = None
        order.consent_at = time.time()
        for key, value in details.items(): setattr(order, key, value)
        if not card.discount_used:
            card.reserved_order_id = order.id
        if order.amount == 0:
            _apply_payment(ses, order, sub)
        ses.commit()
    if get_order(order_id).status != 'succeeded':
        start_payment(order_id, gateway, return_url)
    return order_id


def abandon_checkout(order_id, user_id):
    with _locked_session() as ses:
        order = ses.get(BillingOrder, order_id)
        if not order or order.user_id != user_id or order.status != 'ready' or order.kind != 'bind':
            raise PaymentError('Операцию нельзя закрыть, пока платёж не проверен.')
        sub = ses.get(Subscription, user_id)
        order.status = 'canceled'; order.finished_at = time.time()
        if sub.pending_order_id == order.id:
            sub.pending_order_id = None
        ses.commit()


def set_auto_renew(user_id, enabled):
    with _locked_session() as ses:
        sub = ses.get(Subscription, user_id)
        if sub is None or not sub.method_id:
            raise PaymentError('Сначала привяжите карту и оформите подписку.')
        if enabled and not active(sub):
            raise PaymentError('Сначала оплатите подписку.')
        sub.auto_renew = enabled
        sub.consent_at = time.time()
        ses.commit()


def renew_due(gateway, secret, return_url):
    """Run from a scheduler every minute. Existing pending operations are reconciled first."""
    with db_session.create_session() as ses:
        pending = [x.id for x in ses.query(BillingOrder).filter(BillingOrder.status.in_(['created', 'pending'])).all()]
    for order_id in pending:
        try: refresh_order(order_id, gateway, secret, return_url)
        except PaymentError: pass  # Remains pending, retry with the same idempotence key.
    with db_session.create_session() as ses:
        due = [s.user_id for s in ses.query(Subscription).filter(Subscription.status.in_(['trialing','active']), Subscription.period_end <= time.time()).all()]
    results = []
    for user_id in due:
        with _locked_session() as ses:
            sub = ses.get(Subscription, user_id)
            if active(sub) or sub.status not in ('trialing','active'): continue
            if not sub.auto_renew:
                sub.status = 'past_due'; ses.commit(); continue
            if sub.pending_order_id: continue
            card = ses.get(CardPromotion, sub.card_key)
            if card is None or not sub.method_id: continue
            if card.reserved_order_id: continue
            discount = sub.interval == 'month' and discount_allowed(sub, card)
            amount = first_price(sub.plan, sub.interval, sub.extra_seats) if discount else regular_price(sub.plan, sub.interval, sub.extra_seats)
            order = BillingOrder(id=uuid.uuid4().hex, user_id=user_id, kind='renewal',
                plan=sub.plan, interval=sub.interval, extra_seats=sub.extra_seats,
                status='created', amount=amount, regular_amount=regular_price(sub.plan,sub.interval,sub.extra_seats),
                discounted=discount, subscription_revision=sub.revision,
                starts_at=sub.period_end, ends_at=add_period(sub.period_end,sub.interval),
                created_at=time.time(), method_id=sub.method_id, card_key=sub.card_key, card_last4=sub.card_last4, consent_at=sub.consent_at)
            ses.add(order); sub.pending_order_id=order.id
            if not card.discount_used: card.reserved_order_id=order.id
            ses.commit(); order_id=order.id
        try:
            start_payment(order_id,gateway,return_url)
            results.append(refresh_order(order_id,gateway,secret,return_url))
        except PaymentError:
            results.append('pending')
    return results
