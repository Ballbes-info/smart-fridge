import copy
import importlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from data import db_session
from data.user import User
from data.billing import Subscription, CardPromotion, BillingOrder
import billing_service as service
from payment_gateway import PaymentError, YooKassaTest


class FakeGateway:
    """Deterministic provider responses; no credentials or network are used."""
    def __init__(self):
        self.payments = {}; self.refunds = {}; self.keys = {}
        self.suffix = '4444'; self.cancel_next = False; self.pending_next = False
        self.payment_calls = []; self.refund_calls = []

    def create_payment(self, payload, key):
        self.payment_calls.append((copy.deepcopy(payload),key))
        if key in self.keys: return copy.deepcopy(self.payments[self.keys[key]])
        id = 'p-' + str(len(self.payments)+1)
        binding='payment_method_data' in payload
        method = {'id':'card-'+str(len(self.payments)+1), 'type':'bank_card','saved':True,
            'card':{'first6':'555555','last4':self.suffix,'expiry_month':'12','expiry_year':'2035'}}
        if not binding: method['id']=payload['payment_method_id']
        obj={'id':id,'test':True,'paid':True,'recipient':{'account_id':'test-shop'},'status':'pending' if binding or self.pending_next else 'canceled' if self.cancel_next else 'succeeded',
            'amount':payload['amount'],'metadata':payload['metadata'],'payment_method':method}
        self.cancel_next=False; self.pending_next=False
        if binding: obj['confirmation']={'confirmation_url':'https://yoomoney.ru/test/'+id}
        self.payments[id]=obj; self.keys[key]=id
        return copy.deepcopy(obj)

    def payment(self,id): return copy.deepcopy(self.payments[id])

    def create_refund(self,id,key):
        self.refund_calls.append((id,key))
        self.refunds.setdefault(key,{'id':key,'payment_id':id,'status':'succeeded','amount':{'value':'1.00','currency':'RUB'}})
        return copy.deepcopy(self.refunds[key])

    def refund(self,id): return copy.deepcopy(self.refunds[id])


class BillingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.previous_factory=db_session.__dict__.get('__factory')
        db_session.__dict__['__factory']=None
        db_session.global_init(str(Path(cls.temp.name)/'test.db'))
        with patch.dict(os.environ,{'SECRET_KEY':'test-key'}): cls.module=importlib.import_module('app')
        cls.app=cls.module.app
        cls.app.config.update(TESTING=True,PUBLIC_BASE_URL='https://test.example',PAYMENT_CARD_HASH_KEY='stable-test-key')

    @classmethod
    def tearDownClass(cls):
        cls.app.config.pop('BILLING_GATEWAY_FACTORY',None)
        db_session.__dict__['__factory']=cls.previous_factory
        cls.temp.cleanup()

    def setUp(self):
        with db_session.create_session() as ses:
            for model in [BillingOrder,CardPromotion,Subscription]: ses.query(model).delete()
            ses.query(User).delete()
            user=User(email='payer@example.com',name='Payer',is_verified=True)
            user.set_password('Password123!');ses.add(user);ses.commit();self.uid=user.id
        self.fake=FakeGateway();self.app.config['BILLING_GATEWAY_FACTORY']=lambda:self.fake
        self.client=self.app.test_client()
        with self.client.session_transaction() as session: session['user_id']=self.uid

    def return_url(self,id): return 'https://test.example/checkout/'+id

    def bind(self,plan='basic',interval='month',extra=0,uid=None):
        uid=uid or self.uid
        order_id=service.new_checkout(uid,plan,interval,extra,self.fake,self.return_url)
        order=service.get_order(order_id)
        self.fake.payments[order.provider_id]['status']='succeeded'
        self.assertEqual(service.refresh_order(order_id,self.fake,'stable-test-key',self.return_url),'ready')
        return order_id

    def buy(self,plan='basic',interval='month',extra=0):
        oid=self.bind(plan,interval,extra)
        _,_,quote,_=service.checkout_details(oid,self.uid)
        service.confirm_checkout(oid,self.uid,False,quote['amount'],self.fake,self.return_url)
        service.refresh_order(oid,self.fake,'stable-test-key',self.return_url)
        return oid

    def expire(self):
        with db_session.create_session() as ses:
            sub=ses.get(Subscription,self.uid)
            sub.period_start=time.time()-8*86400;sub.period_end=time.time()-1;ses.commit()

    def test_trial_requires_card_refund_then_once_only(self):
        self.assertEqual(self.client.get('/profile').location,'/subscriptions')
        oid=service.new_checkout(self.uid,'basic','month',0,self.fake,self.return_url)
        self.assertFalse(service.active(service.get_subscription(self.uid)))
        self.assertEqual(service.refresh_order(oid,self.fake,'stable-test-key',self.return_url),'pending')
        order=service.get_order(oid);self.fake.payments[order.provider_id]['status']='succeeded'
        service.refresh_order(oid,self.fake,'stable-test-key',self.return_url)
        self.assertEqual(len(self.fake.refund_calls),1)
        service.confirm_checkout(oid,self.uid,True,13900,self.fake,self.return_url)
        self.assertEqual(service.get_subscription(self.uid).status,'trialing')
        self.assertEqual(self.client.get('/profile').status_code,200)
        service.confirm_checkout(oid,self.uid,True,13900,self.fake,self.return_url)
        self.assertEqual(len(self.fake.payment_calls),1)
        oid2=self.bind()
        with self.assertRaises(PaymentError):service.confirm_checkout(oid2,self.uid,True,13900,self.fake,self.return_url)

    def test_same_card_new_account_gets_no_second_trial_or_discount(self):
        self.buy()
        with db_session.create_session() as ses:
            u=User(email='other@example.com',name='Other',is_verified=True);u.set_password('Password123!');ses.add(u);ses.commit();uid=u.id
        oid=self.bind(uid=uid)
        _,_,quote,trial=service.checkout_details(oid,uid)
        self.assertFalse(trial);self.assertEqual(quote['amount'],19900)

    def test_rebinding_another_card_does_not_restore_account_offer(self):
        self.buy(); self.fake.suffix='5555'
        oid=self.bind();_,_,quote,trial=service.checkout_details(oid,self.uid)
        self.assertFalse(trial);self.assertEqual(quote['amount'],0)  # same active tariff
        self.expire()
        _,_,quote,trial=service.checkout_details(oid,self.uid)
        self.assertFalse(trial);self.assertEqual(quote['amount'],19900)

    def test_month_discount_year_price_and_extra_seats(self):
        self.assertEqual(service.first_price('family_premium','month',2),75700)
        self.assertEqual(service.first_price('family_premium','year',2),997000)
        oid=self.buy('family_basic','year',1)
        self.assertEqual(service.get_order(oid).amount,398000)
        sub=service.get_subscription(self.uid);self.assertTrue(sub.first_paid);self.assertTrue(sub.trial_used)

    def test_trial_conversion_burns_days_and_discount_once(self):
        oid=self.bind();service.confirm_checkout(oid,self.uid,True,13900,self.fake,self.return_url)
        before=service.get_subscription(self.uid).period_end
        oid=self.buy();sub=service.get_subscription(self.uid)
        self.assertEqual(service.get_order(oid).amount,13900)
        self.assertEqual(sub.status,'active');self.assertGreater(sub.period_end,before)
        self.expire();service.renew_due(self.fake,'stable-test-key',self.return_url)
        self.assertEqual(self.fake.payment_calls[-1][0]['amount']['value'],'199.00')

    def test_due_trial_automatically_charges_first_discount(self):
        oid=self.bind();service.confirm_checkout(oid,self.uid,True,13900,self.fake,self.return_url)
        self.expire();service.renew_due(self.fake,'stable-test-key',self.return_url)
        self.assertEqual(self.fake.payment_calls[-1][0]['amount']['value'],'139.00')
        self.assertEqual(service.get_subscription(self.uid).status,'active')

    def test_failed_renewal_blocks_functions_keeps_settings(self):
        self.buy();self.expire();self.fake.cancel_next=True
        service.renew_due(self.fake,'stable-test-key',self.return_url)
        self.assertEqual(service.get_subscription(self.uid).status,'past_due')
        self.assertEqual(self.client.get('/profile').location,'/subscriptions')
        self.assertEqual(self.client.get('/settings').status_code,200)

    def test_disable_renewal_preserves_period_then_no_charge(self):
        self.buy();service.set_auto_renew(self.uid,False)
        self.assertTrue(service.active(service.get_subscription(self.uid)))
        count=len(self.fake.payment_calls);self.expire()
        service.renew_due(self.fake,'stable-test-key',self.return_url)
        self.assertEqual(len(self.fake.payment_calls),count)
        self.assertFalse(service.active(service.get_subscription(self.uid)))

    def test_upgrade_prorates_downgrade_immediate_without_balance(self):
        self.buy()
        with db_session.create_session() as ses:
            sub=ses.get(Subscription,self.uid);sub.period_start=time.time()-15*86400;sub.period_end=time.time()+15*86400;ses.commit()
        oid=self.bind('premium');_,_,quote,_=service.checkout_details(oid,self.uid)
        self.assertAlmostEqual(quote['amount'],15000,delta=2)
        service.confirm_checkout(oid,self.uid,False,quote['amount'],self.fake,self.return_url)
        service.refresh_order(oid,self.fake,'stable-test-key',self.return_url)
        end=service.get_subscription(self.uid).period_end
        oid=self.buy('basic');self.assertEqual(service.get_order(oid).amount,0)
        sub=service.get_subscription(self.uid);self.assertEqual(sub.plan,'basic');self.assertEqual(sub.period_end,end)

    def test_refund_pending_does_not_activate_trial(self):
        oid=service.new_checkout(self.uid,'basic','month',0,self.fake,self.return_url)
        order=service.get_order(oid);self.fake.payments[order.provider_id]['status']='succeeded'
        self.fake.refunds['refund-'+oid]={'id':'refund-'+oid,'payment_id':order.provider_id,'status':'pending','amount':{'value':'1.00','currency':'RUB'}}
        self.assertEqual(service.refresh_order(oid,self.fake,'stable-test-key',self.return_url),'pending')
        self.assertFalse(service.active(service.get_subscription(self.uid)))
        with self.assertRaises(PaymentError):service.confirm_checkout(oid,self.uid,True,13900,self.fake,self.return_url)

    def test_duplicate_callback_does_not_extend_subscription(self):
        oid=self.buy();end=service.get_subscription(self.uid).period_end
        for _ in range(3):service.refresh_order(oid,self.fake,'stable-test-key',self.return_url)
        self.assertEqual(service.get_subscription(self.uid).period_end,end)
        self.assertEqual(self.fake.payment_calls[0][1].split('-')[1],'bind')
        self.assertNotEqual(self.fake.payment_calls[0][1],self.fake.payment_calls[1][1])

    def test_webhook_cannot_forge_success(self):
        oid=service.new_checkout(self.uid,'basic','month',0,self.fake,self.return_url)
        order=service.get_order(oid)
        r=self.app.test_client().post('/billing/webhook',json={'event':'payment.succeeded','object':{'id':order.provider_id,'status':'succeeded'}})
        self.assertEqual(r.status_code,200);self.assertFalse(service.active(service.get_subscription(self.uid)))

    def test_checkout_owner_and_consent(self):
        self.assertEqual(self.client.get('/subscriptions').status_code,200)
        self.assertEqual(self.client.post('/subscriptions/start',data={'plan':'basic','interval':'month'}).status_code,302)
        self.assertEqual(len(self.fake.payments),0)
        oid=self.bind()
        other=self.app.test_client()
        self.assertEqual(other.get('/checkout/'+oid).status_code,302)
        self.assertEqual(self.client.post('/checkout/'+oid+'/confirm',data={'action':'pay','expected_amount':'1'}).status_code,302)
        self.assertFalse(service.active(service.get_subscription(self.uid)))
        with self.assertRaises(PaymentError):service.confirm_checkout(oid,self.uid,False,1,self.fake,self.return_url)

    def test_canceled_new_payment_retains_existing_plan_and_card(self):
        self.buy();old=service.get_subscription(self.uid)
        self.fake.suffix='5555';oid=self.bind('premium')
        self.assertEqual(service.get_subscription(self.uid).method_id,old.method_id)
        _,_,quote,_=service.checkout_details(oid,self.uid)
        self.fake.cancel_next=True
        service.confirm_checkout(oid,self.uid,False,quote['amount'],self.fake,self.return_url)
        service.refresh_order(oid,self.fake,'stable-test-key',self.return_url)
        sub=service.get_subscription(self.uid);self.assertEqual(sub.plan,old.plan);self.assertEqual(sub.method_id,old.method_id)

    def test_invalid_inputs_and_real_keys_blocked(self):
        for plan,period,seats in [('bogus','month',0),('basic','day',0),('basic','month',1),('family_basic','month',-1)]:
            with self.assertRaises(PaymentError):service.regular_price(plan,period,seats)
        with self.assertRaises(PaymentError):YooKassaTest('shop','live_not_allowed')
        date=datetime(2024,1,31,tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(service.add_period(date,'month'),timezone.utc).day,29)

    def test_pending_renewal_does_not_create_duplicate_payment(self):
        self.buy();self.expire();self.fake.pending_next=True
        service.renew_due(self.fake,'stable-test-key',self.return_url)
        calls=len(self.fake.payment_calls)
        service.renew_due(self.fake,'stable-test-key',self.return_url)
        self.assertEqual(len(self.fake.payment_calls),calls)

    def test_provider_amount_mismatch_does_not_activate(self):
        oid=self.bind()
        _,_,quote,_=service.checkout_details(oid,self.uid)
        service.confirm_checkout(oid,self.uid,False,quote['amount'],self.fake,self.return_url)
        order=service.get_order(oid)
        self.fake.payments[order.provider_id]['amount']['value']='0.01'
        with self.assertRaises(PaymentError): service.refresh_order(oid,self.fake,'stable-test-key',self.return_url)
        self.assertFalse(service.active(service.get_subscription(self.uid)))

    def test_real_payment_response_and_wrong_shop_rejected(self):
        import io,json
        api=YooKassaTest('shop','test_secret')
        for obj in [{'test':False,'recipient':{'account_id':'shop'}}, {'test':True,'recipient':{'account_id':'other'}}]:
            with patch('payment_gateway.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(obj).encode())):
                with self.assertRaises(PaymentError):api.payment('id')

    def test_disable_during_pending_charge_is_not_overwritten(self):
        self.buy();self.expire();self.fake.pending_next=True
        service.renew_due(self.fake,'stable-test-key',self.return_url)
        service.set_auto_renew(self.uid,False)
        sub=service.get_subscription(self.uid);oid=sub.pending_order_id
        order=service.get_order(oid);self.fake.payments[order.provider_id]['status']='succeeded'
        service.refresh_order(oid,self.fake,'stable-test-key',self.return_url)
        self.assertFalse(service.get_subscription(self.uid).auto_renew)

    def test_test_checkout_missing_configuration_does_not_grant_access(self):
        self.app.config.pop('BILLING_GATEWAY_FACTORY')
        with patch.dict(self.app.config,{'YOOKASSA_SHOP_ID':'','YOOKASSA_SECRET_KEY':'','PAYMENT_MODE':'yookassa_test'}):
            r=self.client.post('/subscriptions/start',data={'plan':'basic','interval':'month','extra_seats':'0','bind_consent':'yes'},follow_redirects=True)
        self.assertIn('Тестовая оплата пока не подключена.',r.get_data(as_text=True))
        self.assertFalse(service.active(service.get_subscription(self.uid)))

    def test_unsaved_card_cannot_start_trial_and_checkout_can_restart(self):
        oid=service.new_checkout(self.uid,'basic','month',0,self.fake,self.return_url)
        order=service.get_order(oid)
        self.fake.payments[order.provider_id]['status']='succeeded'
        self.fake.payments[order.provider_id]['payment_method']['saved']=False
        with self.assertRaises(PaymentError):service.refresh_order(oid,self.fake,'stable-test-key',self.return_url)
        self.assertEqual(service.get_order(oid).status,'rejected')
        self.assertFalse(service.get_subscription(self.uid).pending_order_id)
        self.assertFalse(service.active(service.get_subscription(self.uid)))

    def test_renew_command_is_registered(self):
        result=self.app.test_cli_runner().invoke(args=['billing','renew'])
        self.assertEqual(result.exit_code,0,result.output + repr(result.exception))

    def test_checkout_routes_paid_and_trial(self):
        r=self.client.post('/subscriptions/start',data={'plan':'basic','interval':'month','extra_seats':'0','bind_consent':'yes'})
        self.assertEqual(r.status_code,302)
        oid=r.location.rsplit('/',1)[-1];order=service.get_order(oid)
        self.fake.payments[order.provider_id]['status']='succeeded'
        self.assertEqual(self.client.get(r.location).status_code,200)
        r=self.client.post('/checkout/'+oid+'/confirm',data={'action':'trial','expected_amount':'13900','auto_consent':'yes'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(self.client.get(r.location).status_code,200)
        self.assertEqual(self.client.get('/settings').status_code,200)
        self.assertEqual(self.client.get('/profile').status_code,200)
