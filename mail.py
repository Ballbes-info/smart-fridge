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


def _config():
    """Настройки SMTP из окружения (.env)."""
    user = os.environ.get("SMTP_USER", "").strip()
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": user,
        "password": os.environ.get("SMTP_PASSWORD", "").strip(),
        "sender": os.environ.get("SMTP_SENDER", "").strip() or user,
        "ttl_minutes": int(os.environ.get("CODE_TTL_MINUTES", "15")),
    }


def is_configured() -> bool:
    """True, если заданы логин и пароль SMTP."""
    cfg = _config()
    return bool(cfg["user"] and cfg["password"])


def code_ttl_seconds() -> int:
    return _config()["ttl_minutes"] * 60


def generate_code() -> str:
    """Шестизначный код из криптографически стойкого генератора."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _subject(purpose="verify") -> str:
    return "Сброс пароля SmartFridge" if purpose == "reset" else "Код подтверждения SmartFridge"


def _body(code: str, purpose="verify") -> str:
    minutes = _config()["ttl_minutes"]
    action = "сброса пароля" if purpose == "reset" else "подтверждения почты"
    return (
        f"Ваш код для {action} SmartFridge:\n\n"
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

    message = EmailMessage()
    message["Subject"] = _subject(purpose)
    message["From"] = formataddr(("SmartFridge", cfg["sender"]))
    message["To"] = recipient
    message.set_content(_body(code, purpose))

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "SMTP отклонил логин или пароль. Проверьте SMTP_USER и SMTP_PASSWORD "
            "в .env — для Gmail нужен пароль приложения, не обычный пароль."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Не удалось отправить письмо: {exc}") from exc
