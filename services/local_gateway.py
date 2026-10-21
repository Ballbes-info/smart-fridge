"""Local, network-free payment simulation. Never accepts real card details."""
from decimal import Decimal
import re
import secrets
import time
import uuid
from sqlalchemy import text
from data import db_session
from data.billing import BillingOrder
from data.local_payment import LocalPayment, LocalRefund, LocalEventState


class PaymentError(Exception):
    """Ошибка локальной оплаты."""

    pass

RARE_EVENTS = {'insufficient_funds', 'declined', 'delay'}
REASONS = {'insufficient_funds': 'Недостаточно средств', 'declined': 'Платёж отклонён',
           'canceled_by_user': 'Платёж отменён'}


def demo_cards(user_id):
    return [
        {'alias':f'local_card_{user_id}_primary','last4':f'{(1000+user_id)%10000:04d}'},
        {'alias':f'local_card_{user_id}_secondary','last4':f'{(2000+user_id)%10000:04d}'},
        {'alias':'local_card_shared','last4':'4444'},
    ]


def _card(alias, user_id):
    for card in demo_cards(user_id):
        if card['alias'] == alias:
            return {'id':alias, 'type':'bank_card', 'saved':True,
                'simulation_fingerprint':alias,
                'card':{'first6':'555555','last4':card['last4'], 'expiry_month':'12','expiry_year':'2035'}}
    # A saved shared/reference card must belong to the payer or be the shared demo card.
    raise PaymentError('Выберите доступную демо-карту.')


def _locked():
    ses = db_session.create_session()
    ses.execute(text('BEGIN IMMEDIATE'))
    return ses


def _choose_event(ses):
    # Serialized with the payment decision: works across workers and restarts.
    state=ses.get(LocalEventState,'rare-events')
    if state is None:
        state=LocalEventState(key='rare-events',successes_remaining=0)
        ses.add(state)
    if state.successes_remaining>0:
        state.successes_remaining-=1
        return 'success'
    value=secrets.randbelow(10000)
    event='success' if value<9700 else 'insufficient_funds' if value<9800 else 'declined' if value<9900 else 'delay'
    if event in RARE_EVENTS:
        state.successes_remaining=6
    return event



def _resolve(ses, payment):
    if payment.event is not None:
        return
    payment.event = 'success' if payment.kind == 'bind' else _choose_event(ses)
    if payment.event in ('insufficient_funds','declined'):
        payment.status, payment.phase, payment.reason = 'canceled','finished',payment.event
    elif payment.event == 'delay':
        payment.status, payment.phase, payment.ready_at = 'pending','processing',time.time()+12
    else:
        payment.status, payment.phase = 'succeeded','finished'


def _view(payment):
    method = _card(payment.card_alias, payment.user_id) if payment.card_alias else {'type':'bank_card','saved':False}
    result = {'id':payment.id, 'test':True, 'recipient':{'account_id':'local'},
        'status':payment.status, 'paid':payment.status=='succeeded',
        'amount':{'value':f'{payment.amount/100:.2f}','currency':'RUB'},
        'metadata':payment.metadata_json, 'payment_method':method}
    if payment.status=='pending' and payment.phase in ('awaiting_card','awaiting_confirmation'):
        result['confirmation']={'confirmation_url':'/payment/'+payment.id}
    if payment.reason:
        result['cancellation_details']={'reason':payment.reason}
    return result


class LocalGateway:
    def create_payment(self, payload, key):
        with _locked() as ses:
            existing=ses.query(LocalPayment).filter_by(idempotence_key=key).first()
            if existing: return _view(existing)
            order=ses.get(BillingOrder,payload.get('metadata',{}).get('order_id'))
            if not order: raise PaymentError('Операция не найдена.')
            amount=Decimal(payload.get('amount',{}).get('value','-1'))*100
            if payload.get('amount',{}).get('currency')!='RUB' or amount<1 or amount!=int(amount):
                raise PaymentError('Некорректная сумма оплаты.')
            binding='payment_method_data' in payload
            alias=None if binding else payload.get('payment_method_id')
            if not binding: _card(alias,order.user_id)
            return_path=payload.get('confirmation',{}).get('return_url') or '/checkout/'+order.id
            if not re.fullmatch(r'/checkout/[a-f0-9]{32}',return_path):
                raise PaymentError('Некорректный адрес возврата.')
            payment=LocalPayment(id='local_'+uuid.uuid4().hex,idempotence_key=key,
                order_id=order.id,user_id=order.user_id,kind='bind' if binding else 'charge',
                amount=int(amount),metadata_json=payload['metadata'],return_path=return_path,
                card_alias=alias,status='pending',phase='awaiting_card' if binding else 'awaiting_confirmation',created_at=time.time())
            if order.kind=='renewal': _resolve(ses,payment)
            ses.add(payment);ses.commit()
            return _view(payment)

    def payment(self,payment_id):
        with _locked() as ses:
            payment=ses.get(LocalPayment,payment_id)
            if not payment: raise PaymentError('Операция не найдена.')
            if payment.phase=='processing' and payment.ready_at<=time.time():
                payment.status,payment.phase='succeeded','finished'
                ses.commit()
            return _view(payment)

    def create_refund(self,payment_id,key):
        with _locked() as ses:
            payment=ses.get(LocalPayment,payment_id)
            if not payment or payment.kind!='bind' or payment.status!='succeeded' or payment.amount!=100:
                raise PaymentError('Возврат проверочного платежа пока недоступен.')
            refund=ses.query(LocalRefund).filter_by(idempotence_key=key).first()
            if not refund:
                refund=LocalRefund(id='local_refund_'+uuid.uuid4().hex,idempotence_key=key,payment_id=payment_id)
                ses.add(refund);ses.commit()
            return {'id':refund.id,'payment_id':refund.payment_id,'status':'succeeded','amount':{'value':'1.00','currency':'RUB'}}

    def refund(self,refund_id):
        with db_session.create_session() as ses:
            refund=ses.get(LocalRefund,refund_id)
            if not refund: raise PaymentError('Возврат не найден.')
            return {'id':refund.id,'payment_id':refund.payment_id,'status':'succeeded','amount':{'value':'1.00','currency':'RUB'}}

    def choose_card(self,payment_id,user_id,alias):
        _card(alias,user_id)
        with _locked() as ses:
            payment=ses.get(LocalPayment,payment_id)
            if not payment or payment.user_id!=user_id: raise PaymentError('Операция не найдена.')
            if payment.phase=='awaiting_card':
                payment.card_alias=alias;payment.phase='awaiting_confirmation';ses.commit()

    def confirm(self,payment_id,user_id):
        with _locked() as ses:
            payment=ses.get(LocalPayment,payment_id)
            if not payment or payment.user_id!=user_id: raise PaymentError('Операция не найдена.')
            if payment.phase=='awaiting_confirmation':
                _resolve(ses,payment);ses.commit()

    def cancel(self,payment_id,user_id):
        with _locked() as ses:
            payment=ses.get(LocalPayment,payment_id)
            if not payment or payment.user_id!=user_id: raise PaymentError('Операция не найдена.')
            if payment.phase in ('awaiting_card','awaiting_confirmation'):
                payment.status,payment.phase,payment.reason='canceled','finished','canceled_by_user';ses.commit()

    def page(self,payment_id,user_id):
        self.payment(payment_id)
        with db_session.create_session() as ses:
            payment=ses.get(LocalPayment,payment_id)
            return payment if payment and payment.user_id==user_id else None
