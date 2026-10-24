"""Authenticated test checkout and subscription settings."""
import time
import secrets
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Blueprint, current_app, session, redirect, url_for, request, render_template, flash, jsonify, abort
import click
from data import db_session
from data.user import User
from services import billing_service as service
from services.local_gateway import LocalGateway, PaymentError, demo_cards

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
    return LocalGateway()


def return_url(order_id):
    return current_app.url_map.bind('localhost').build('billing.checkout', {'order_id': order_id})


def card_secret():
    return current_app.config.get('PAYMENT_CARD_HASH_KEY') or current_app.secret_key


@billing.app_template_filter('rubles')
def rubles(value):
    value = int(value or 0)
    full, cents = divmod(value, 100)
    return f'{full:,}'.replace(',', ' ') + (f',{cents:02d}' if cents else '') + ' ₽'


@billing.app_template_filter('billing_date')
def billing_date(value):
    return datetime.fromtimestamp(value, timezone(timedelta(hours=3))).strftime('%d.%m.%Y в %H:%M МСК') if value else '—'


@billing.app_context_processor
def billing_context():
    sub = service.get_subscription(session['user_id']) if session.get('user_id') else None
    return {'subscription':sub, 'subscription_active':service.active(sub), 'billing_plans':service.PLANS}


@billing.route('/subscriptions')
@verified
def plans():
    return render_template('subscriptions.html', plans=service.PLANS)


@billing.route('/settings/subscription')
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
    try:
        extra = int(request.form.get('extra_seats','0'))
        api=gateway()
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
    if order.kind=='scan_pack':
        from data.scan_credits import ScanCreditPack
        from services.scan_credits import NAMES
        session.setdefault('storage_csrf',secrets.token_hex(32))
        with db_session.create_session() as ses:pack=ses.get(ScanCreditPack,order.id)
        if not pack:abort(404)
        return render_template('scan_pack_checkout.html',order=order,pack=pack,kind_name=NAMES[pack.kind],
                               cards=demo_cards(session['user_id']),csrf=session['storage_csrf'])
    order,sub,quote,trial_allowed=service.checkout_details(order_id,session['user_id'])
    next_amount = service.first_price(order.plan,order.interval,order.extra_seats) if quote and quote['discounted'] else service.regular_price(order.plan,order.interval,order.extra_seats)
    return render_template('checkout.html',order=order,quote=quote,trial_allowed=trial_allowed,next_amount=next_amount,trial_end=time.time()+7*86400,cards=demo_cards(session["user_id"]))


@billing.route('/checkout/<order_id>/card', methods=['POST'])
@verified
def select_card(order_id):
    try:
        service.select_card(order_id, session['user_id'], request.form.get('card', ''), card_secret())
    except PaymentError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('billing.checkout', order_id=order_id))


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


@billing.cli.command('renew')
def renew():
    """Reconcile pending test payments and charge due subscriptions."""
    try:
        api=gateway()
        results=service.renew_due(api,card_secret(),return_url)
        click.echo(f'Проверено продлений: {len(results)}; состояния: {results}')
    except PaymentError as exc:
        raise click.ClickException(str(exc))


@billing.route('/settings/ai-limits')
@verified
def scan_limits():
    from services import scan_credits,recipe_generation
    session.setdefault('storage_csrf',secrets.token_hex(32))
    sub=service.get_subscription(session['user_id'])
    premium=bool(service.active(sub) and service.PLANS[sub.plan]['premium'])
    return render_template('scan_limits.html',quotas={k:scan_credits.allowance(session['user_id'],k) for k in ['label','product']},
                           packages=scan_credits.PACKAGES,kind_names=scan_credits.NAMES,premium=premium,recipe_quota=recipe_generation.quota(session['user_id']),
                           csrf=session['storage_csrf'],submission=secrets.token_hex(24),workspace_back_url=url_for('billing.settings'))


@billing.route('/scan-packs/start',methods=['POST'])
@verified
def scan_pack_start():
    from services.storage import check_form
    from services.scan_credits import new_order
    check_form()
    try:
        key=new_order(session['user_id'],request.form.get('package',''),request.form.get('submission_key',''))
        return redirect(url_for('billing.checkout',order_id=key))
    except PaymentError as exc:flash(str(exc),'error');return redirect(url_for('billing.scan_limits'))


@billing.route('/scan-packs/<order_id>/confirm',methods=['POST'])
@verified
def scan_pack_confirm(order_id):
    from services.storage import check_form
    from services.scan_credits import confirm_order
    check_form()
    if not service.get_order(order_id,session['user_id']):abort(404)
    try:
        confirm_order(order_id,session['user_id'],request.form.get('card',''),
                      int(request.form.get('expected_amount','-1')),gateway(),return_url)
        order=service.get_order(order_id,session['user_id'])
        return redirect(order.confirmation_url or url_for('billing.checkout',order_id=order_id))
    except (PaymentError,ValueError) as exc:
        flash(str(exc) if isinstance(exc,PaymentError) else 'Проверьте сумму оплаты.','error')
        return redirect(url_for('billing.checkout',order_id=order_id))


@billing.route('/scan-packs/<order_id>/cancel',methods=['POST'])
@verified
def scan_pack_cancel(order_id):
    from services.storage import check_form
    from sqlalchemy import text
    check_form()
    with db_session.create_session() as ses:
        ses.execute(text('BEGIN IMMEDIATE'));order=ses.get(service.BillingOrder,order_id)
        if not order or order.user_id!=session['user_id'] or order.kind!='scan_pack':abort(404)
        if order.status=='ready':order.status='canceled';order.finished_at=time.time();ses.commit()
    return redirect(url_for('billing.scan_limits'))
