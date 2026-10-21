"""Authenticated test checkout and subscription settings."""
import time
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo
from flask import Blueprint, current_app, session, redirect, url_for, request, render_template, flash, jsonify, abort
import click
from data import db_session
from data.user import User
from data.billing import BillingOrder
import billing_service as service
from payment_gateway import YooKassaTest, PaymentError

billing = Blueprint('billing', __name__)


def verified(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        with db_session.create_session() as ses:
            user = ses.get(User, session.get('user_id')) if session.get('user_id') else None
        if not user:
            return redirect(url_for('login'))
        if not user.is_verified:
            return redirect(url_for('verify'))
        return view(*args, **kwargs)
    return wrapped


def gateway():
    factory = current_app.config.get('BILLING_GATEWAY_FACTORY')
    if factory and current_app.testing:
        return factory()
    if current_app.config.get('PAYMENT_MODE', 'local') == 'local':
        from local_gateway import LocalGateway
        return LocalGateway()
    if current_app.config.get('PAYMENT_MODE') != 'yookassa_test':
        raise PaymentError('Неизвестный режим оплаты.')
    return YooKassaTest(current_app.config.get('YOOKASSA_SHOP_ID'), current_app.config.get('YOOKASSA_SECRET_KEY'))


def return_url(order_id):
    path = current_app.url_map.bind('localhost').build('billing.checkout', {'order_id': order_id})
    if current_app.config.get('PAYMENT_MODE', 'local') == 'local' and not current_app.config.get('BILLING_GATEWAY_FACTORY'):
        return path
    base = current_app.config.get('PUBLIC_BASE_URL', '').rstrip('/')
    if not base or not (base.startswith('https://') or (current_app.testing and base.startswith('http://'))):
        raise PaymentError('Тестовая оплата пока не подключена.')
    path = current_app.url_map.bind('localhost').build('billing.checkout', {'order_id': order_id})
    return base + path


def card_secret():
    return current_app.config.get('PAYMENT_CARD_HASH_KEY') or current_app.secret_key


@billing.app_template_filter('rubles')
def rubles(value):
    value = int(value or 0)
    full, cents = divmod(value, 100)
    return f'{full:,}'.replace(',', ' ') + (f',{cents:02d}' if cents else '') + ' ₽'


@billing.app_template_filter('billing_date')
def billing_date(value):
    return datetime.fromtimestamp(value, ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y в %H:%M МСК') if value else '—'


@billing.app_context_processor
def billing_context():
    sub = service.get_subscription(session['user_id']) if session.get('user_id') else None
    return {'subscription':sub, 'subscription_active':service.active(sub), 'billing_plans':service.PLANS, 'local_payments':current_app.config.get('PAYMENT_MODE', 'local')=='local'}


@billing.route('/subscriptions')
@verified
def plans():
    return render_template('subscriptions.html', plans=service.PLANS)


@billing.route('/settings')
@verified
def settings():
    sub = service.get_subscription(session['user_id'])
    next_amount = 0
    if sub and sub.plan:
        with db_session.create_session() as ses:
            from data.billing import CardPromotion
            card = ses.get(CardPromotion, sub.card_key) if sub.card_key else None
            discount = bool(card and sub.interval=='month' and service.discount_allowed(sub,card))
        next_amount = service.first_price(sub.plan,sub.interval,sub.extra_seats) if discount else service.regular_price(sub.plan,sub.interval,sub.extra_seats)
    return render_template('billing_settings.html', next_amount=next_amount)


@billing.route('/subscriptions/start', methods=['POST'])
@verified
def start():
    if request.form.get('bind_consent') != 'yes':
        flash('Подтвердите проверку и сохранение карты.', 'error')
        return redirect(url_for('billing.plans'))
    try:
        extra = int(request.form.get('extra_seats','0'))
        api=gateway()
        return_url('check')  # Validate public URL before creating a local pending operation.
        order_id=service.new_checkout(session['user_id'],request.form.get('plan',''),request.form.get('interval',''),extra,api,return_url)
        return redirect(url_for('billing.checkout',order_id=order_id))
    except (ValueError, PaymentError) as exc:
        flash(str(exc) if isinstance(exc,PaymentError) else 'Проверьте количество мест.','error')
        return redirect(url_for('billing.plans'))


@billing.route('/checkout/<order_id>')
@verified
def checkout(order_id):
    order=service.get_order(order_id,session['user_id'])
    if not order: abort(404)
    if order.status in ('created','pending'):
        try:
            service.refresh_order(order_id,gateway(),card_secret(),return_url)
        except PaymentError as exc:
            flash(str(exc),'error')
    order,sub,quote,trial_allowed=service.checkout_details(order_id,session['user_id'])
    next_amount = service.first_price(order.plan,order.interval,order.extra_seats) if quote and quote['discounted'] else service.regular_price(order.plan,order.interval,order.extra_seats)
    return render_template('checkout.html',order=order,quote=quote,trial_allowed=trial_allowed,next_amount=next_amount,trial_end=time.time()+7*86400)


@billing.route('/checkout/<order_id>/confirm',methods=['POST'])
@verified
def confirm(order_id):
    if request.form.get('auto_consent') != 'yes':
        flash('Для оформления подтвердите условия автоматического продления.','error')
        return redirect(url_for('billing.checkout',order_id=order_id))
    try:
        api=gateway()
        order_id=service.confirm_checkout(order_id,session['user_id'],request.form.get('action')=='trial',int(request.form.get('expected_amount','-1')),api,return_url)
        return redirect(url_for('billing.checkout',order_id=order_id))
    except (ValueError,PaymentError) as exc:
        flash(str(exc) if isinstance(exc,PaymentError) else 'Проверьте условия оплаты.','error')
        return redirect(url_for('billing.checkout',order_id=order_id))


@billing.route('/checkout/<order_id>/close',methods=['POST'])
@verified
def close(order_id):
    try: service.abandon_checkout(order_id,session['user_id'])
    except PaymentError as exc: flash(str(exc),'error')
    return redirect(url_for('billing.plans'))


@billing.route('/billing/status/<order_id>')
@verified
def status(order_id):
    if not service.get_order(order_id,session['user_id']): abort(404)
    try:
        state=service.refresh_order(order_id,gateway(),card_secret(),return_url)
        return jsonify(status=state)
    except PaymentError:
        return jsonify(status='pending'),503


@billing.route('/billing/auto-renew',methods=['POST'])
@verified
def auto_renew():
    try:
        enabled=request.form.get('enabled')=='yes'
        if enabled and request.form.get('consent')!='yes':
            raise PaymentError('Подтвердите автоматическое продление.')
        service.set_auto_renew(session['user_id'],enabled)
        flash('Автопродление включено.' if enabled else 'Автопродление отключено. Доступ сохранится до конца текущего периода.','success')
    except PaymentError as exc: flash(str(exc),'error')
    return redirect(url_for('billing.settings'))


@billing.route('/billing/webhook',methods=['POST'])
def webhook():
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict) or not isinstance(payload.get('object'),dict):
        return '',400
    provider_id=payload['object'].get('id')
    if not isinstance(provider_id,str): return '',400
    with db_session.create_session() as ses:
        order=ses.query(BillingOrder).filter(BillingOrder.provider_id==provider_id).first()
        order_id=order.id if order else None
    if not order_id: return '',200
    # Notification body is untrusted: always fetch the payment using server credentials.
    try: service.refresh_order(order_id,gateway(),card_secret(),return_url)
    except PaymentError: return '',503
    return '',200


@billing.cli.command('renew')
def renew():
    """Reconcile pending test payments and charge due subscriptions."""
    try:
        api=gateway(); return_url('check')
        results=service.renew_due(api,card_secret(),return_url)
        click.echo(f'Проверено продлений: {len(results)}; состояния: {results}')
    except PaymentError as exc:
        raise click.ClickException(str(exc))
