"""Коды разных назначений и сохраняемые в SQLite лимиты запросов."""
import hashlib
import math
import re
import time
from sqlalchemy.dialects.sqlite import insert
from werkzeug.security import check_password_hash, generate_password_hash
from data import db_session
from data.auth import AuthCode, AuthRateLimit
from data.user import User
from services import mail

CODE_ATTEMPTS = 5


def consume_limits(limits):
    """limits: (scope, identifier, maximum, window_seconds). Return retry seconds."""
    now = time.time()
    retry = 0
    with db_session.create_session() as ses:
        ses.query(AuthRateLimit).filter(AuthRateLimit.expires_at <= now).delete()
        for scope, identifier, maximum, window in limits:
            period = int(now // window)
            rolling = scope == 'mail-cooldown'
            expires = now + window if rolling else (period + 1) * window
            digest = hashlib.sha256(str(identifier).encode()).hexdigest()
            key = f'{scope}:{digest}' if rolling else f'{scope}:{digest}:{period}'
            statement = insert(AuthRateLimit).values(key=key, count=1, expires_at=expires)
            statement = statement.on_conflict_do_update(
                index_elements=['key'], set_={'count': AuthRateLimit.count + 1}
            ).returning(AuthRateLimit.count, AuthRateLimit.expires_at)
            count, expires = ses.execute(statement).one()
            if count > maximum:
                retry = max(retry, math.ceil(expires - now))
        ses.commit()
    return retry


def issue_code(email, purpose='verify'):
    if purpose not in ('verify', 'reset'):
        raise ValueError('Неизвестное назначение кода')
    with db_session.create_session() as ses:
        from sqlalchemy import func
        user = ses.query(User).filter(func.lower(func.trim(User.email)) == email.lower().strip()).order_by(User.id).first()
        if user is None:
            return False, 'Аккаунт не найден.'
        if purpose == 'verify' and user.is_verified:
            return False, 'Почта уже подтверждена.'
        # A shared email limit also prevents alternating reset/verification sends.
        retry = consume_limits([
            ('mail-cooldown', user.id, 1, 60),
            ('mail-hour', user.id, 5, 3600),
        ])
        if retry:
            return False, f'Повторите отправку через {retry} сек.'
        code = mail.generate_code()
        code_hash = generate_password_hash(code)
        try:
            mail.send_code(user.email, code, purpose=purpose)
        except mail.MailError as exc:
            # A failed send must not prevent retrying after fixing SMTP.
            # Keep the hourly limit, and preserve any previously delivered code.
            cooldown='mail-cooldown:'+hashlib.sha256(str(user.id).encode()).hexdigest()
            ses.query(AuthRateLimit).filter(AuthRateLimit.key==cooldown).delete(synchronize_session=False)
            ses.commit()
            return False, str(exc)
        # Failed delivery leaves both account status and the previous code intact.
        ses.merge(AuthCode(user_id=user.id, purpose=purpose, code_hash=code_hash,
                           expires_at=time.time() + mail.code_ttl_seconds(), failed_attempts=0))
        ses.commit()
    return True, ''


def complete_code(user_id, code, purpose='verify', password=None):
    """Validate once and apply the action and code consumption in one transaction."""
    if purpose not in ('verify', 'reset'):
        raise ValueError('Неизвестное назначение кода')
    if not re.fullmatch(r'[0-9]{6}', str(code).strip()):return False
    if purpose == 'reset' and (password is None or not 8 <= len(password) <= 256 or password.isspace()):
        return False
    # Begin with a write lock so two requests cannot consume the same code.
    with db_session.create_session() as ses:
        from sqlalchemy import text
        ses.execute(text('BEGIN IMMEDIATE'))
        entry = ses.get(AuthCode, (user_id, purpose))
        user = ses.get(User, user_id)
        if user is None or entry is None:
            return False
        if entry.expires_at <= time.time() or entry.failed_attempts >= CODE_ATTEMPTS:
            return False
        if not check_password_hash(entry.code_hash, code.strip()):
            entry.failed_attempts += 1
            ses.commit()
            return False
        if purpose == 'reset':
            user.set_password(password)
            user.auth_version=(user.auth_version or 0)+1
        user.is_verified = True
        # Successful ownership confirmation also invalidates an older verify code.
        if purpose == 'reset':
            ses.query(AuthCode).filter(AuthCode.user_id == user_id).delete(synchronize_session=False)
        else:
            ses.delete(entry)
        ses.commit()
        return True


def code_ready(user_id,purpose='verify'):
    with db_session.create_session() as ses:
        entry=ses.get(AuthCode,(user_id,purpose))
        return bool(entry and entry.expires_at>time.time() and entry.failed_attempts<CODE_ATTEMPTS)


def change_password(user_id,current,password):
    if not 8<=len(password)<=256 or password.isspace():return False,'Пароль должен содержать от 8 до 256 символов и не состоять только из пробелов.',None
    with db_session.create_session() as ses:
        from sqlalchemy import text
        ses.execute(text('BEGIN IMMEDIATE'))
        user=ses.get(User,user_id)
        if not user or not user.is_verified or not user.check_password(current):return False,'Текущий пароль неверен.',None
        if user.check_password(password):return False,'Новый пароль должен отличаться от текущего.',None
        user.set_password(password);user.auth_version=(user.auth_version or 0)+1
        ses.query(AuthCode).filter(AuthCode.user_id==user_id).delete(synchronize_session=False)
        version=user.auth_version;ses.commit();return True,'',version
