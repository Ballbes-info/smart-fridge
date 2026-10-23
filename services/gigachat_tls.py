"""Verified TLS for GigaChat, including Windows roots and the official Russian CA chain.

No global TLS changes, no verify=False, and no credentials are sent to CA hosts.
"""
import logging
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading
import time
from urllib.request import Request, urlopen

import certifi
from requests.adapters import HTTPAdapter

log=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[1]
CA_URLS=(
    'https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt',
    'https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt',
)
_lock=threading.Lock()
_last_download=0.0


def cache_path():
    return ROOT/'instance'/'gigachat-ca.pem'


def pem_bytes(raw):
    """PowerShell can save a PEM as UTF-16. Convert safely before passing it to OpenSSL."""
    if len(raw)>256*1024:raise ValueError('Certificate file is too large')
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):content=raw.decode('utf-16')
    else:content=raw.decode('utf-8-sig')
    blocks=re.findall(r'-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----',content)
    if not blocks:raise ValueError('No PEM certificates')
    result=('\n'.join(blocks)+'\n').encode('ascii')
    # Parsing checks the actual X.509 data, not just its PEM markers.
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=result.decode('ascii'))
    return result


def context():
    # On Windows this also loads the system ROOT and CA certificate stores.
    ctx=ssl.create_default_context()
    ctx.load_verify_locations(cafile=certifi.where())
    custom=os.environ.get('GIGACHAT_CA_BUNDLE','').strip().strip('"').strip("'")
    paths=[]
    if custom:
        path=Path(os.path.expandvars(os.path.expanduser(custom)))
        paths.append(path if path.is_absolute() else ROOT/path)
    paths.extend([ROOT/'certificates'/'gigachat-ca.pem',cache_path()])
    for path in dict.fromkeys(paths):
        try:ctx.load_verify_locations(cadata=pem_bytes(path.read_bytes()).decode('ascii'))
        except (OSError,ValueError,UnicodeError,ssl.SSLError):
            if custom and path==paths[0]:
                log.warning('GigaChat: дополнительный CA-файл не прочитан; используются системные сертификаты и официальный кеш.')
    return ctx


class VerifiedAdapter(HTTPAdapter):
    def __init__(self, ctx):
        self.ctx=ctx
        super().__init__(max_retries=0)

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs['ssl_context']=self.ctx
        return super().init_poolmanager(connections,maxsize,block=block,**kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host,kwargs=super().build_connection_pool_key_attributes(request,verify,cert)
        # Requests versions may otherwise substitute their preloaded certifi-only context.
        kwargs['ssl_context']=self.ctx
        kwargs['cert_reqs']='CERT_REQUIRED'
        return host,kwargs

    def proxy_manager_for(self, proxy, **kwargs):
        kwargs['ssl_context']=self.ctx
        return super().proxy_manager_for(proxy,**kwargs)


def configure(client):
    # Limit added CA trust to the two provider hosts; other clients are unaffected.
    ctx=context()
    for prefix in ('https://api.giga.chat/','https://ngw.devices.sberbank.ru:9443/'):
        previous=client.adapters.get(prefix)
        client.mount(prefix,VerifiedAdapter(ctx))
        if previous:previous.close()
    client.verify=True


def repair(client):
    """Refresh official CA files once after a certificate-verification failure.

    Downloads use independently verified HTTPS. An untrusted certificate is never
    taken from the failed GigaChat connection itself. Any failure keeps TLS strict.
    """
    global _last_download
    with _lock:
        if time.monotonic()-_last_download<300:return False
        _last_download=time.monotonic()
        try:
            download_context=ssl.create_default_context()
            download_context.load_verify_locations(cafile=certifi.where())
            chunks=[]
            for url in CA_URLS:
                req=Request(url,headers={'User-Agent':'SmartFridge/1.0'})
                with urlopen(req,context=download_context,timeout=10) as response:
                    # Never accept a redirect onto HTTP or a different certificate host.
                    if not response.geturl().startswith('https://gu-st.ru/'):raise ValueError('Unexpected CA host')
                    chunks.append(pem_bytes(response.read(256*1024+1)))
            data=pem_bytes(b'\n'.join(chunks))
            path=cache_path();path.parent.mkdir(parents=True,exist_ok=True)
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent,prefix='gigachat-ca-',suffix='.tmp',delete=False) as handle:
                    temporary=Path(handle.name);handle.write(data)
                os.replace(temporary,path)
            finally:
                if temporary:temporary.unlink(missing_ok=True)
            configure(client)
            return True
        except (OSError,ValueError,UnicodeError,ssl.SSLError):
            log.warning('GigaChat: не удалось получить цепочку CA по проверенному HTTPS с официального сайта.')
            return False


def verification_failure(exc):
    # Only retry failures during certificate verification, before the HTTP request.
    return 'CERTIFICATE_VERIFY_FAILED' in str(exc) or 'certificate verify failed' in str(exc).lower()


def error_message(exc):
    reason=str(exc).lower()
    if 'hostname mismatch' in reason or 'not valid for' in reason:
        return 'Сертификат сервера GigaChat выдан для другого адреса. Проверьте прокси и сеть.'
    if 'expired' in reason or 'not yet valid' in reason:
        return 'Сертификат GigaChat просрочен или ещё не действует. Проверьте дату и время компьютера.'
    if 'unable to get local issuer' in reason or 'self-signed' in reason or verification_failure(exc):
        return ('Не удалось построить доверенную цепочку GigaChat. Автоматическая загрузка CA не помогла. '
                'Проверьте доступ к gu-st.ru и доверенные сертификаты антивируса или прокси.')
    return 'Не удалось установить защищённое соединение с GigaChat. Проверьте сеть и поддержку TLS в Python.'
