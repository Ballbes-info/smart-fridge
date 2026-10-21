"""Regression tests use a temporary SQLite database and never send real email."""
import importlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        db = Path(cls.temp.name) / 'test.db'
        # Start with the existing schema to check additive initialization.
        with sqlite3.connect(db) as connection:
            connection.execute('''CREATE TABLE users (
                id INTEGER PRIMARY KEY, email VARCHAR UNIQUE NOT NULL,
                name VARCHAR NOT NULL, password_hash VARCHAR NOT NULL,
                created_at VARCHAR, is_verified BOOLEAN NOT NULL,
                verify_code_hash VARCHAR, verify_code_expires VARCHAR)''')
            connection.execute("INSERT INTO users VALUES (99, 'old@example.com', 'Old', 'legacy-hash', NULL, 1, NULL, NULL)")
        from data import db_session
        cls.previous_factory = db_session.__dict__.get('__factory')
        db_session.__dict__['__factory'] = None
        db_session.global_init(str(db))
        with patch.dict(os.environ, {'SECRET_KEY': 'test-only-key'}):
            cls.module = importlib.import_module('app')
        cls.module.app.config['TESTING'] = True
        cls.db_session = db_session
        from data.auth import AuthCode, AuthRateLimit
        from data.user import User
        cls.Code, cls.Limit, cls.User = AuthCode, AuthRateLimit, User
        cls.legacy_preserved = cls.module.get_user_by_id(99).is_verified

    @classmethod
    def tearDownClass(cls):
        cls.db_session.__dict__['__factory'] = cls.previous_factory
        cls.temp.cleanup()

    def setUp(self):
        with self.db_session.create_session() as ses:
            ses.query(self.Code).delete()
            ses.query(self.Limit).delete()
            ses.query(self.User).delete()
            u = self.User(email='test@example.com', name='Test', is_verified=True)
            u.set_password('Password123!')
            ses.add(u); ses.commit(); self.user_id = u.id
        self.client = self.module.app.test_client()
        self.sender = patch('mail.send_code', return_value=None).start()
        patch('mail.generate_code', return_value='123456').start()
        self.addCleanup(patch.stopall)

    def clear_limits(self):
        with self.db_session.create_session() as ses:
            ses.query(self.Limit).delete(); ses.commit()

    def code_exists(self, purpose):
        with self.db_session.create_session() as ses:
            return ses.get(self.Code, (self.user_id, purpose)) is not None

    def reset_request(self):
        return self.client.post('/reset-password', data={'email': 'test@example.com'})

    def reset_submit(self, password='NewPassword123!', password2=None, code='123456'):
        return self.client.post('/reset-password/verify', data={
            'code': code, 'password': password,
            'password2': password if password2 is None else password2})

    def test_existing_schema_and_public_pages(self):
        self.assertTrue(self.legacy_preserved)
        for path in ['/', '/login', '/register', '/reset-password']:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertNotIn(b'class="nav-home"', self.client.get('/').data)

    def test_reset_request_preserves_verified_account(self):
        self.assertEqual(self.reset_request().status_code, 302)
        self.assertTrue(self.module.get_user_by_id(self.user_id).is_verified)
        self.assertTrue(self.code_exists('reset'))
        self.sender.assert_called_once_with('test@example.com', '123456', purpose='reset')

    def test_invalid_password_does_not_consume_code(self):
        self.reset_request()
        self.assertEqual(self.reset_submit('short').status_code, 200)
        self.assertTrue(self.code_exists('reset'))
        self.assertEqual(self.reset_submit(password2='different').status_code, 200)
        self.assertTrue(self.code_exists('reset'))
        self.assertEqual(self.reset_submit().status_code, 302)
        self.assertTrue(self.module.get_user_by_id(self.user_id).check_password('NewPassword123!'))
        self.assertFalse(self.code_exists('reset'))
        from auth_service import complete_code
        self.assertFalse(complete_code(self.user_id, '123456', 'reset', 'AnotherPassword!'))

    def test_wrong_attempt_limit_survives_new_client(self):
        self.reset_request()
        for _ in range(5): self.reset_submit(code='000000')
        with self.client.session_transaction() as session: saved = dict(session)
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session: session.update(saved)
        self.assertEqual(self.reset_submit().status_code, 200)
        self.assertTrue(self.module.get_user_by_id(self.user_id).check_password('Password123!'))

    def test_expired_code_cannot_change_password(self):
        self.reset_request()
        with self.db_session.create_session() as ses:
            ses.get(self.Code, (self.user_id, 'reset')).expires_at = time.time()-1
            ses.commit()
        self.assertEqual(self.reset_submit().status_code, 200)
        self.assertTrue(self.module.get_user_by_id(self.user_id).check_password('Password123!'))

    def test_purposes_are_separate_and_registration_works(self):
        self.client.post('/register', data={'email':'new@example.com','name':'New',
            'password':'Password123!','password2':'Password123!'})
        new_id = self.module.find_user_by_email('new@example.com').id
        from auth_service import complete_code, issue_code
        self.assertFalse(complete_code(new_id, '123456', 'reset', 'NewPassword123!'))
        self.clear_limits()
        with patch('mail.generate_code', return_value='654321'):
            self.assertTrue(issue_code('new@example.com', 'reset')[0])
        self.assertFalse(complete_code(new_id, '123456', 'reset', 'NewPassword123!'))
        self.assertFalse(complete_code(new_id, '654321', 'verify'))
        self.assertEqual(self.client.post('/verify',data={'code':'123456'}).status_code,302)
        self.assertTrue(self.module.get_user_by_id(new_id).is_verified)
        self.assertEqual(self.client.get('/profile').location,'/subscriptions')

    def test_delivery_failure_preserves_old_code_and_account(self):
        self.reset_request(); self.clear_limits()
        import mail
        self.sender.side_effect = mail.MailError('Delivery failed')
        from auth_service import issue_code, complete_code
        self.assertFalse(issue_code('test@example.com','reset')[0])
        self.assertTrue(self.module.get_user_by_id(self.user_id).is_verified)
        self.assertTrue(complete_code(self.user_id,'123456','reset','NewPassword123!'))

    def test_email_cooldown_and_hourly_limit(self):
        from auth_service import issue_code
        self.assertTrue(issue_code('test@example.com','reset')[0])
        self.assertFalse(issue_code('test@example.com','reset')[0])
        self.assertEqual(self.sender.call_count,1)
        self.clear_limits()
        with patch('auth_service.time.time', return_value=3600*100+1):
            self.assertTrue(issue_code('test@example.com','reset')[0])
        for minute in range(1,5):
            with patch('auth_service.time.time', return_value=3600*100+minute*60+1):
                self.assertTrue(issue_code('test@example.com','reset')[0])
        with patch('auth_service.time.time', return_value=3600*100+301):
            self.assertFalse(issue_code('test@example.com','reset')[0])

    def test_mail_delay_does_not_reset_at_minute_boundary(self):
        from auth_service import issue_code
        with patch('auth_service.time.time', return_value=3659):
            self.assertTrue(issue_code('test@example.com', 'reset')[0])
        with patch('auth_service.time.time', return_value=3661):
            self.assertFalse(issue_code('test@example.com', 'reset')[0])
        with patch('auth_service.time.time', return_value=3719):
            self.assertTrue(issue_code('test@example.com', 'reset')[0])

    def test_login_account_and_ip_limits(self):
        for _ in range(10):
            r=self.client.post('/login',data={'email':'test@example.com','password':'wrong'})
            self.assertEqual(r.status_code,200)
        r=self.client.post('/login',data={'email':'test@example.com','password':'wrong'},environ_overrides={'REMOTE_ADDR':'192.0.2.1'})
        self.assertEqual(r.status_code,429); self.assertIn('Retry-After',r.headers)
        self.clear_limits()
        for i in range(30):
            self.client.post('/login',data={'email':f'{i}@example.com','password':'wrong'})
        self.assertEqual(self.client.post('/login',data={'email':'last@example.com'}).status_code,429)

    def test_valid_login_and_logout(self):
        self.assertEqual(self.client.post('/login',data={'email':'test@example.com','password':'Password123!'}).status_code,302)
        self.assertEqual(self.client.get('/profile').location,'/subscriptions')
        self.client.post('/logout')
        self.assertEqual(self.client.get('/profile').status_code,302)


if __name__ == '__main__': unittest.main()
