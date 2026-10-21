import importlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from data import db_session
from data.user import User
from data.billing import Subscription,CardPromotion,BillingOrder
from data.local_payment import LocalPayment,LocalRefund,LocalEventState
from local_gateway import LocalGateway,demo_cards,_choose_event
import billing_service as service


class LocalPaymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.previous_factory=db_session.__dict__.get('__factory')
        db_session.__dict__['__factory']=None
        db_session.global_init(str(Path(cls.temp.name)/'local.db'))
        with patch.dict(os.environ,{'SECRET_KEY':'test-only'}):cls.module=importlib.import_module('app')
        cls.app=cls.module.app
        cls.saved_config={key:cls.app.config.get(key) for key in ['TESTING','PAYMENT_MODE','PUBLIC_BASE_URL','BILLING_GATEWAY_FACTORY']}
        cls.app.config.update(TESTING=True,PAYMENT_MODE='local',PUBLIC_BASE_URL='')
        cls.app.config.pop('BILLING_GATEWAY_FACTORY',None)

    @classmethod
    def tearDownClass(cls):
        cls.app.config.update(cls.saved_config)
        db_session.__dict__['__factory']=cls.previous_factory
        cls.temp.cleanup()

    def setUp(self):
        with db_session.create_session() as ses:
            for model in [LocalRefund,LocalPayment,LocalEventState,BillingOrder,CardPromotion,Subscription,User]:ses.query(model).delete()
            user=User(email='local@example.com',name='Local',is_verified=True);user.set_password('Password123!')
            ses.add(user);ses.commit();self.uid=user.id
        self.client=self.app.test_client()
        with self.client.session_transaction() as session:session['user_id']=self.uid
        self.random=patch('local_gateway.secrets.randbelow',return_value=0).start()
        self.network=patch('urllib.request.urlopen',side_effect=AssertionError('No network allowed')).start()
        self.addCleanup(patch.stopall)

    def ready(self,plan='basic',shared=False):
        r=self.client.post('/subscriptions/start',data={'plan':plan,'interval':'month','extra_seats':'0','bind_consent':'yes'})
        self.assertEqual(r.status_code,302)
        oid=r.location.rsplit('/',1)[-1]
        self.assertEqual(self.client.get(r.location).status_code,200)
        order=service.get_order(oid);pid=order.provider_id
        cards=demo_cards(self.uid);alias=cards[-1 if shared else 0]['alias']
        self.assertEqual(self.client.post('/payment/'+pid,data={'action':'card','card':alias}).status_code,302)
        self.assertEqual(self.client.post('/payment/'+pid,data={'action':'confirm'}).status_code,302)
        self.assertEqual(service.get_order(oid).status,'ready')
        return oid

    def paid_pending(self,plan='basic'):
        oid=self.ready(plan)
        _,_,quote,_=service.checkout_details(oid,self.uid)
        r=self.client.post('/checkout/'+oid+'/confirm',data={'action':'pay','expected_amount':str(quote['amount']),'auto_consent':'yes'})
        self.assertEqual(r.status_code,302)
        return oid,service.get_order(oid).provider_id

    def buy(self,plan='basic'):
        oid,pid=self.paid_pending(plan)
        self.client.post('/payment/'+pid,data={'action':'confirm'})
        self.assertEqual(service.get_order(oid).status,'succeeded')
        return oid,pid

    def test_full_local_flow_without_https_or_keys_or_network(self):
        oid,pid=self.buy()
        self.assertTrue(service.active(service.get_subscription(self.uid)))
        self.assertEqual(self.client.get('/profile').status_code,200)
        self.network.assert_not_called()
        html=self.client.get('/payment/'+pid).get_data(as_text=True)
        self.assertIn('Оплата прошла',html)
        self.assertNotIn('name="card_number"',html)
        self.assertNotIn('name="cvc"',html)

    def test_trial_with_returned_verification_ruble(self):
        oid=self.ready()
        r=self.client.post('/checkout/'+oid+'/confirm',data={'action':'trial','expected_amount':'13900','auto_consent':'yes'})
        self.assertEqual(r.status_code,302)
        self.assertEqual(service.get_subscription(self.uid).status,'trialing')
        with db_session.create_session() as ses:self.assertEqual(ses.query(LocalRefund).count(),1)

    def test_rare_event_has_six_successful_operations_cooldown(self):
        with patch('local_gateway.secrets.randbelow',return_value=9800) as draw:
            with db_session.create_session() as ses:
                self.assertEqual(_choose_event(ses),'declined');ses.commit()
            for _ in range(6):
                with db_session.create_session() as ses:
                    self.assertEqual(_choose_event(ses),'success');ses.commit()
            self.assertEqual(draw.call_count,1)
            with db_session.create_session() as ses:
                self.assertEqual(_choose_event(ses),'declined');ses.commit()
            self.assertEqual(draw.call_count,2)

    def test_probability_boundaries(self):
        for sample,expected in [(0,'success'),(9699,'success'),(9700,'insufficient_funds'),(9799,'insufficient_funds'),(9800,'declined'),(9899,'declined'),(9900,'delay'),(9999,'delay')]:
            with db_session.create_session() as ses:
                ses.query(LocalEventState).delete();ses.commit()
            with patch('local_gateway.secrets.randbelow',return_value=sample):
                with db_session.create_session() as ses:
                    self.assertEqual(_choose_event(ses),expected);ses.commit()

    def test_decline_persists_refresh_does_not_reroll(self):
        oid,pid=self.paid_pending('premium')
        with patch('local_gateway.secrets.randbelow',return_value=9800) as draw:
            self.client.post('/payment/'+pid,data={'action':'confirm'})
            for _ in range(4):
                self.client.get('/payment/'+pid)
                self.client.post('/payment/'+pid,data={'action':'confirm'})
                self.client.get('/billing/status/'+oid)
            self.assertEqual(draw.call_count,1)
        self.assertEqual(service.get_order(oid).status,'canceled')
        self.assertFalse(service.active(service.get_subscription(self.uid)))
        self.assertIn('Платёж отклонён',self.client.get('/payment/'+pid).get_data(as_text=True))

    def test_delay_is_pending_then_success_once(self):
        oid,pid=self.paid_pending()
        with patch('local_gateway.secrets.randbelow',return_value=9999):
            self.client.post('/payment/'+pid,data={'action':'confirm'})
        self.assertEqual(service.get_order(oid).status,'pending')
        self.assertFalse(service.active(service.get_subscription(self.uid)))
        with db_session.create_session() as ses:
            ses.get(LocalPayment,pid).ready_at=time.time()-1;ses.commit()
        self.client.get('/payment/'+pid+'/status')
        sub=service.get_subscription(self.uid);self.assertTrue(service.active(sub))
        end=sub.period_end
        self.client.get('/payment/'+pid+'/status')
        self.assertEqual(service.get_subscription(self.uid).period_end,end)

    def test_insufficient_funds_and_cancel(self):
        oid,pid=self.paid_pending()
        with patch('local_gateway.secrets.randbelow',return_value=9750):self.client.post('/payment/'+pid,data={'action':'confirm'})
        self.assertIn('Недостаточно средств',self.client.get('/payment/'+pid).get_data(as_text=True))
        oid2=service.new_checkout(self.uid,'basic','month',0,LocalGateway(),lambda id:'/checkout/'+id)
        order=service.get_order(oid2)
        self.client.post('/payment/'+order.provider_id,data={'action':'cancel'})
        self.assertEqual(service.get_order(oid2).status,'canceled')
        self.assertFalse(service.get_subscription(self.uid).pending_order_id)

    def test_payment_page_requires_owner_and_known_demo_card(self):
        oid=service.new_checkout(self.uid,'basic','month',0,LocalGateway(),lambda id:'/checkout/'+id)
        pid=service.get_order(oid).provider_id
        self.assertEqual(self.app.test_client().get('/payment/'+pid).status_code,302)
        with db_session.create_session() as ses:
            user=User(email='other@example.com',name='Other',is_verified=True);user.set_password('Password123!');ses.add(user);ses.commit();other=user.id
        client=self.app.test_client()
        with client.session_transaction() as session:session['user_id']=other
        self.assertEqual(client.get('/payment/'+pid).status_code,404)
        r=self.client.post('/payment/'+pid,data={'action':'card','card':'real-card-number'})
        self.assertEqual(r.status_code,200)
        self.assertIn('Выберите доступную демо-карту',r.get_data(as_text=True))

    def test_local_renewal_success_and_failure_without_payment_page(self):
        self.buy()
        with db_session.create_session() as ses:
            sub=ses.get(Subscription,self.uid);sub.period_start=time.time()-32*86400;sub.period_end=time.time()-1;ses.commit()
        with patch('local_gateway.secrets.randbelow',return_value=9800):
            service.renew_due(LocalGateway(),'test-key',lambda id:'/checkout/'+id)
        self.assertEqual(service.get_subscription(self.uid).status,'past_due')
        self.assertEqual(self.client.get('/profile').location,'/subscriptions')
        self.assertEqual(self.client.get('/settings').status_code,200)
        self.network.assert_not_called()

    def test_shared_demo_card_cannot_get_second_trial_on_new_account(self):
        oid=self.ready(shared=True)
        self.client.post('/checkout/'+oid+'/confirm',data={'action':'trial','expected_amount':'13900','auto_consent':'yes'})
        with db_session.create_session() as ses:
            user=User(email='other@example.com',name='Other',is_verified=True);user.set_password('Password123!');ses.add(user);ses.commit();self.uid=user.id
        with self.client.session_transaction() as session:session['user_id']=self.uid
        oid=self.ready(shared=True)
        _,_,quote,trial=service.checkout_details(oid,self.uid)
        self.assertFalse(trial)

    def test_background_worker_processes_due_local_subscription(self):
        from local_payment_worker import start_worker
        self.buy()
        with db_session.create_session() as ses:
            sub=ses.get(Subscription,self.uid);sub.period_start=time.time()-32*86400;sub.period_end=time.time()-1;ses.commit()
        self.app.config['TESTING']=False
        try:
            start_worker(self.app)
            deadline=time.monotonic()+3
            while not service.active(service.get_subscription(self.uid)) and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(service.active(service.get_subscription(self.uid)))
        finally:
            worker=self.app.extensions.pop('local_payment_worker',None)
            if worker:
                worker['stop'].set();worker['thread'].join(timeout=3)
            self.app.config['TESTING']=True
        self.network.assert_not_called()

    def test_worker_not_started_by_unit_tests(self):
        self.client.get('/subscriptions')
        self.assertNotIn('local_payment_worker',self.app.extensions)
