"""YooKassa test API only. Card numbers and CVC never pass through this app."""
import base64
import json
import urllib.request
import urllib.error
from urllib.parse import urlparse


class PaymentError(Exception):
    pass


class YooKassaTest:
    def __init__(self, shop_id, secret):
        if not shop_id or not secret or not secret.startswith('test_'):
            raise PaymentError('Тестовая оплата пока не подключена.')
        self.authorization = 'Basic ' + base64.b64encode(f'{shop_id}:{secret}'.encode()).decode()
        self.shop_id = str(shop_id)

    def request(self, method, path, payload=None, key=None):
        headers = {'Authorization': self.authorization, 'Content-Type': 'application/json'}
        if key:
            headers['Idempotence-Key'] = key
        req = urllib.request.Request('https://api.yookassa.ru/v3/' + path,
            data=None if payload is None else json.dumps(payload).encode(), headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                obj = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise PaymentError('Не удалось получить подтверждение ЮKassa. Повторите проверку позже.') from exc
        if path.startswith('payments'):
            if obj.get('test') is not True:
                raise PaymentError('Операция не является тестовой. Доступ не изменён.')
            if str(obj.get('recipient', {}).get('account_id')) != self.shop_id:
                raise PaymentError('Платёж относится к другому магазину.')
        return obj

    def payment(self, payment_id):
        return self.request('GET', 'payments/' + payment_id)

    def create_payment(self, payload, key):
        return self.request('POST', 'payments', payload, key)

    def create_refund(self, payment_id, key):
        return self.request('POST', 'refunds', {'payment_id': payment_id,
            'amount': {'value': '1.00', 'currency': 'RUB'}}, key)

    def refund(self, refund_id):
        return self.request('GET', 'refunds/' + refund_id)


def valid_confirmation_url(value):
    url = urlparse(value or '')
    return url.scheme == 'https' and any(url.hostname == host or
        (url.hostname or '').endswith('.' + host) for host in ['yoomoney.ru', 'yookassa.ru'])
