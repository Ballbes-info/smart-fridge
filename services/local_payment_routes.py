"""Hosted-style local checkout, using generated cards and no external bank."""
from flask import Blueprint, session, render_template, redirect, url_for, request, abort, jsonify
from services.billing_routes import verified, card_secret, return_url
from services.local_gateway import LocalGateway, PaymentError, demo_cards, REASONS
from services import billing_service as service

local_payment = Blueprint('local_payment', __name__)


def owned(payment_id):
    payment=LocalGateway().page(payment_id,session['user_id'])
    if payment is None: abort(404)
    return payment


def sync(payment):
    try:
        service.refresh_order(payment.order_id,LocalGateway(),card_secret(),return_url)
    except PaymentError:
        # The page still shows the persisted payment state. Merchant checkout can retry reconciliation.
        pass


@local_payment.route('/payment/<payment_id>',methods=['GET','POST'])
@verified
def page(payment_id):
    payment=owned(payment_id)
    api=LocalGateway()
    error=None
    if request.method=='POST':
        try:
            action=request.form.get('action')
            if action=='card': api.choose_card(payment_id,session['user_id'],request.form.get('card',''))
            elif action=='confirm': api.confirm(payment_id,session['user_id'])
            elif action=='cancel': api.cancel(payment_id,session['user_id'])
            else: abort(400)
            payment=owned(payment_id);sync(payment)
            return redirect(url_for('local_payment.page',payment_id=payment_id))
        except PaymentError as exc: error=str(exc)
    payment=owned(payment_id);sync(payment)
    cards=demo_cards(session['user_id'])
    card=next((c for c in cards if c['alias']==payment.card_alias),None)
    return render_template('local_payment.html',payment=payment,cards=cards,card=card,
        reason=REASONS.get(payment.reason,'Платёж не завершён'),error=error)


@local_payment.route('/payment/<payment_id>/status')
@verified
def status(payment_id):
    payment=owned(payment_id);sync(payment)
    return jsonify(status=payment.status)
