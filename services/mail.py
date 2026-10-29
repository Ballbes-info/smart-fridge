"""Отправка писем с кодами подтверждения через SMTP.

В модуле нет работы с базой — только формирование и отправка письма.
Код генерируется здесь же, а хранится в БД уже в виде хэша.
"""

import os
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr


class MailError(Exception):
    """Не удалось отправить письмо."""


def _integer(name,default,minimum,maximum):
    try:
        value=int(os.environ.get(name,str(default)))
        if not minimum<=value<=maximum:raise ValueError
        return value
    except ValueError:raise MailError(f'Проверьте настройку {name} на сервере.') from None


def _config():
    """Настройки SMTP из окружения (.env)."""
    user = os.environ.get("SMTP_USER", "").strip()
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(),
        "port": _integer("SMTP_PORT",587,1,65535),
        "user": user,
        "password": os.environ.get("SMTP_PASSWORD", "").strip(),
        "sender": os.environ.get("SMTP_SENDER", "").strip() or user,
        "ttl_minutes": _integer("CODE_TTL_MINUTES",15,1,60),
    }


def code_ttl_seconds() -> int:
    return _integer("CODE_TTL_MINUTES",15,1,60)*60


def generate_code() -> str:
    """Шестизначный код из криптографически стойкого генератора."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _subject(purpose="verify") -> str:
    return "Сброс пароля СмартФридж" if purpose == "reset" else "Код подтверждения СмартФридж"


def _body(code: str, purpose="verify") -> str:
    minutes = _config()["ttl_minutes"]
    action = "сброса пароля" if purpose == "reset" else "подтверждения почты"
    return (
        f"Ваш код для {action} СмартФридж:\n\n"
        f"    {code}\n\n"
        f"Код действует {minutes} минут и подходит только один раз.\n"
        "Если вы не запрашивали этот код, письмо можно удалить.\n"
    )


def send_code(recipient: str, code: str, purpose="verify") -> None:
    """Отправить код подтверждения на адрес recipient."""
    cfg = _config()

    if not cfg["user"] or not cfg["password"]:
        raise MailError(
            "SMTP не настроен. Заполните SMTP_USER и SMTP_PASSWORD в файле .env."
        )

    if any("\n" in v or "\r" in v for v in [recipient,cfg["sender"]]):
        raise MailError("Некорректный адрес отправителя или получателя.")
    message = EmailMessage()
    message["Subject"] = _subject(purpose)
    message["From"] = formataddr(("СмартФридж", cfg["sender"]))
    message["To"] = recipient
    message.set_content(_body(code, purpose))

    try:
        use_ssl=cfg['port']==465 or os.environ.get('SMTP_USE_SSL','').lower() in ['1','true','yes']
        transport=smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        kwargs={'timeout':20}
        if use_ssl:kwargs['context']=ssl.create_default_context()
        with transport(cfg['host'],cfg['port'],**kwargs) as server:
            server.ehlo()
            if not use_ssl:
                if not server.has_extn('starttls'):raise MailError('Почтовый сервер не поддерживает защищённое соединение STARTTLS. Проверьте порт SMTP.')
                server.starttls(context=ssl.create_default_context());server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "SMTP отклонил логин или пароль. Проверьте SMTP_USER и SMTP_PASSWORD "
            "в .env — для Gmail нужен пароль приложения, не обычный пароль."
        ) from exc
    except (smtplib.SMTPException,OSError,ValueError) as exc:
        raise MailError('Не удалось отправить письмо. Проверьте соединение с почтовым сервером и настройки SMTP. Аккаунт сохранён; код можно запросить повторно.') from exc
