"""Account-scoped settings. Sensitive operations require password and fresh CSRF."""
import hmac
import re
import secrets
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from data import db_session
from data.account import AccountPreferences, EmailChange
from data.activity import ActivityEvent
from data.auth import AuthCode
from data.user import User
from services.auth_service import consume_limits
from services import mail

account = Blueprint('account', __name__)
EXPIRY_DAYS = (1, 2, 3, 5, 7, 14)


def preferences(owner, ses=None):
    if ses is None:
        with db_session.create_session() as own:
            return preferences(owner, own)
    row = ses.get(AccountPreferences, owner)
    return dict(expiry_days=row.expiry_days if row else 3,
                expiry_notifications=row.expiry_notifications if row else True,
                revision=row.revision if row else 0)


def csrf():
    expected = session.get('auth_csrf', '')
    supplied = request.form.get('auth_csrf', '')
    if not expected or not hmac.compare_digest(expected.encode('utf-8'), supplied.encode('utf-8')):
        abort(400, description='Форма устарела. Обновите страницу и повторите действие.')


def limit(scope, maximum=10, window=900):
    retry = consume_limits([(scope, session['user_id'], maximum, window),
                            (scope+'-ip', request.remote_addr or '', maximum*3, window)])
    if retry:
        abort(429, description=f'Слишком много попыток. Повторите через {retry} сек.')


def event(ses, owner, action, before=None, after=None):
    ses.add(ActivityEvent(owner_id=owner, category='account', action=action,
                          before=before or {}, after=after or {}))


def details(owner):
    from services.family_service import family_for, access_subscription
    from services.billing_service import active
    with db_session.create_session() as ses:
        group = family_for(ses, owner)
        family = dict(name=group.name, owner=group.owner_id == owner) if group else None
        pending = ses.get(EmailChange, owner)
        user = ses.get(User, owner)
        pending_email = (pending.email if pending and pending.expires_at > time.time()
                         and pending.attempts < 5 and pending.auth_version == user.auth_version else None)
    effective = access_subscription(owner)
    created = '—'
    try:
        stamp = datetime.fromisoformat(user.created_at)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        created = stamp.astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y')
    except (ValueError, TypeError):
        pass
    return dict(account_family=family, account_access=effective if active(effective) else None,
                account_pending_email=pending_email, account_created=created)


def register_account(app, login_required, valid_email):
    @account.before_request
    def protect():
        if request.method == 'POST':
            request.max_content_length = 8192
            csrf()

    @account.errorhandler(400)
    @account.errorhandler(413)
    @account.errorhandler(429)
    def form_error(error):
        if not session.get('user_id'):
            return redirect(url_for('login'))
        profile_page = request.path.startswith('/profile')
        titles = {400: 'Обновите форму', 413: 'Слишком большой запрос', 429: 'Подождите перед повтором'}
        message = (error.description if error.code in (400, 429) else
                   'Введите данные в форму на странице. Фотографии и файлы в эти поля не загружаются.')
        return render_template('account_form_error.html', error_title=titles[error.code],
            error_message=message, active_section='profile' if profile_page else 'settings',
            return_url=url_for('profile') if profile_page else url_for('workspace.settings')), error.code

    @account.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @account.route('/profile/name', methods=['POST'])
    @login_required
    def name():
        limit('profile-name', 30)
        raw = request.form.get('name', '')
        name = ' '.join(raw.split())
        error = None
        if not 1 <= len(name) <= 80 or any(unicodedata.category(c).startswith('C') for c in raw):
            error = 'Введите имя от 1 до 80 символов без управляющих знаков.'
        with db_session.create_session() as ses:
            ses.execute(text('BEGIN IMMEDIATE'))
            user = ses.get(User, session['user_id'])
            if not error and request.form.get('original_name') != user.name:
                error = 'Имя уже изменилось в другой вкладке. Обновите страницу перед сохранением.'
            if not error:
                user.name = name
                ses.commit()
        if error:
            return render_template('profile.html', **details(session['user_id']),
                                   name_error=error, name_value=raw[:200], active_section='profile'), 422
        flash('Имя сохранено.', 'success')
        return redirect(url_for('profile'))

    @account.route('/settings/notifications', methods=['POST'])
    @login_required
    def notifications():
        value = request.form.get('expiry_days', '')
        if value not in [str(d) for d in EXPIRY_DAYS]:
            flash('Выберите срок предупреждения из списка.', 'error')
            return redirect(url_for('workspace.settings', _anchor='notifications'))
        with db_session.create_session() as ses:
            ses.execute(text('BEGIN IMMEDIATE'))
            old = preferences(session['user_id'], ses)
            if request.form.get('revision') != str(old['revision']):
                flash('Настройки изменились в другой вкладке. Проверьте актуальные значения.', 'error')
                return redirect(url_for('workspace.settings', _anchor='notifications'))
            row = ses.get(AccountPreferences, session['user_id'])
            if row is None:
                row = AccountPreferences(user_id=session['user_id']); ses.add(row)
            row.expiry_days = int(value)
            row.expiry_notifications = request.form.get('expiry_notifications') == '1'
            row.revision = old['revision'] + 1
            event(ses, session['user_id'], 'notification_settings_changed', old,
                  dict(expiry_days=row.expiry_days, expiry_notifications=row.expiry_notifications))
            ses.commit()
        flash('Уведомления настроены.', 'success')
        return redirect(url_for('workspace.settings', _anchor='notifications'))

    @account.route('/settings/sessions', methods=['POST'])
    @login_required
    def sessions():
        limit('account-sessions')
        with db_session.create_session() as ses:
            ses.execute(text('BEGIN IMMEDIATE'))
            user = ses.get(User, session['user_id'])
            password = request.form.get('current_password', '')
            if len(password) > 256 or not user.check_password(password):
                flash('Текущий пароль неверен. Другие сеансы не изменены.', 'error')
                return redirect(url_for('workspace.settings', _anchor='security'))
            user.auth_version += 1
            version = user.auth_version
            ses.query(AuthCode).filter(AuthCode.user_id == user.id).delete()
            ses.query(EmailChange).filter(EmailChange.user_id == user.id).delete()
            event(ses, user.id, 'other_sessions_closed')
            ses.commit()
        uid = session['user_id']
        session.clear(); session.update(user_id=uid, auth_version=version)
        flash('На других устройствах потребуется войти снова. Этот сеанс сохранён.', 'success')
        return redirect(url_for('workspace.settings', _anchor='security'))

    @account.route('/profile/email', methods=['POST'])
    @login_required
    def email():
        limit('account-email', 5, 3600)
        target = request.form.get('email', '').strip().lower()
        password = request.form.get('current_password', '')
        error = None
        with db_session.create_session() as ses:
            user = ses.get(User, session['user_id'])
            version = user.auth_version
            if len(password) > 256 or not user.check_password(password):
                error = 'Текущий пароль неверен.'
            elif not valid_email(target):
                error = 'Введите корректный адрес электронной почты.'
            elif target == user.email.strip().lower():
                error = 'Этот адрес уже используется в вашем аккаунте.'
            elif ses.scalar(select(User.id).where(func.lower(func.trim(User.email)) == target)):
                error = 'Этот адрес уже занят другим аккаунтом.'
        if error:
            flash(error, 'error')
            return redirect(url_for('profile', _anchor='email'))
        if consume_limits([('mail-cooldown', session['user_id'], 1, 60),
                           ('account-email-send', session['user_id'], 5, 3600)]):
            flash('Подождите минуту перед повторной отправкой кода.', 'error')
            return redirect(url_for('profile', _anchor='email'))
        code = mail.generate_code()
        try:
            expires = time.time() + mail.code_ttl_seconds()
            mail.send_code(target, code, 'email_change')
        except mail.MailError:
            flash('Не удалось отправить код. Попробуйте позже. Текущая почта не изменилась.', 'error')
            return redirect(url_for('profile', _anchor='email'))
        with db_session.create_session() as ses:
            ses.execute(text('BEGIN IMMEDIATE'))
            user = ses.get(User, session['user_id'])
            if user.auth_version != version:
                abort(409, description='Настройки безопасности изменились. Запросите новый код.')
            ses.merge(EmailChange(user_id=user.id, email=target, code_hash=generate_password_hash(code),
                                 expires_at=expires, attempts=0, auth_version=version))
            ses.commit()
        flash('Код отправлен на новую почту. До подтверждения входите по прежнему адресу.', 'success')
        return redirect(url_for('profile', _anchor='email'))

    @account.route('/profile/email/confirm', methods=['POST'])
    @login_required
    def confirm_email():
        limit('account-email-confirm', 15)
        with db_session.create_session() as ses:
            ses.execute(text('BEGIN IMMEDIATE'))
            user = ses.get(User, session['user_id'])
            pending = ses.get(EmailChange, user.id)
            code = request.form.get('code', '').strip()
            if not pending or pending.expires_at <= time.time() or pending.attempts >= 5 or pending.auth_version != user.auth_version:
                flash('Код истёк или отменён. Запросите новый.', 'error')
                return redirect(url_for('profile', _anchor='email'))
            if not re.fullmatch(r'[0-9]{6}', code) or not check_password_hash(pending.code_hash, code):
                pending.attempts += 1; ses.commit()
                flash('Код неверен. Осталось попыток: ' + str(max(0, 5-pending.attempts)) + '.', 'error')
                return redirect(url_for('profile', _anchor='email'))
            if ses.scalar(select(User.id).where(User.id != user.id, func.lower(func.trim(User.email)) == pending.email)):
                ses.delete(pending); ses.commit()
                flash('Адрес уже занят. Укажите другую почту.', 'error')
                return redirect(url_for('profile', _anchor='email'))
            user.email = pending.email
            user.auth_version += 1
            version = user.auth_version
            ses.delete(pending)
            ses.query(AuthCode).filter(AuthCode.user_id == user.id).delete()
            try:
                ses.commit()
            except IntegrityError:
                ses.rollback()
                flash('Адрес уже занят. Укажите другую почту.', 'error')
                return redirect(url_for('profile', _anchor='email'))
        uid = session['user_id']; session.clear(); session.update(user_id=uid, auth_version=version)
        flash('Почта изменена и подтверждена. На других устройствах войдите заново.', 'success')
        return redirect(url_for('profile', _anchor='email'))

    @account.route('/profile/email/cancel', methods=['POST'])
    @login_required
    def cancel_email():
        with db_session.create_session() as ses:
            ses.query(EmailChange).filter(EmailChange.user_id == session['user_id']).delete(); ses.commit()
        flash('Смена почты отменена.', 'success')
        return redirect(url_for('profile', _anchor='email'))

    app.register_blueprint(account)
