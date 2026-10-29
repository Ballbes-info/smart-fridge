"""SmartFridge — веб-приложение на Flask.

Регистрация (почта + пароль + имя), вход, подтверждение почты,
смена пароля по почте, профиль.

Запуск:
    python app.py
"""

import os
import re
import secrets
import hmac
import time
from urllib.parse import urlsplit,unquote
from functools import wraps
from sqlalchemy.exc import IntegrityError
from pathlib import Path

from data import db_session
from data.user import User
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from services.auth_service import complete_code, consume_limits, issue_code,code_ready,change_password as update_password
from services.activity_audit import record as record_activity

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db" / "smartfridge.db"

load_dotenv(BASE_DIR / ".env")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD_LENGTH = 8


def _load_secret_key() -> str:
    """Секретный ключ из окружения или из файла .secret_key."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key

    key_file = BASE_DIR / ".secret_key"
    # The first server process creates the key; others must read that same key.
    # Never overwrite it during concurrent startup, which would invalidate sessions.
    for _ in range(50):
        try:
            fd=os.open(key_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:
            try:stored=key_file.read_text(encoding='utf-8').strip()
            except FileNotFoundError:continue
            if stored:return stored
            time.sleep(.02)
        else:
            key=secrets.token_hex(32)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                stream.write(key);stream.flush();os.fsync(stream.fileno())
            return key
    raise RuntimeError('Файл .secret_key пуст. Восстановите его или задайте SECRET_KEY в .env.')


app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax",MAX_FORM_MEMORY_SIZE=1024*1024)
app.config["LOCAL_PAYMENT_WORKER"] = True
app.config["PAYMENT_CARD_HASH_KEY"] = os.environ.get("PAYMENT_CARD_HASH_KEY", "").strip()

from services.billing_routes import billing
app.register_blueprint(billing)
from services.local_payment_routes import local_payment
app.register_blueprint(local_payment)


# --------------------------------------------------------------------------- #
# База данных
# --------------------------------------------------------------------------- #

DB_PATH.parent.mkdir(exist_ok=True)
db_session.global_init(str(DB_PATH))


def valid_email(value):
    if len(value)>254 or not EMAIL_RE.fullmatch(value) or not value.isascii():return False
    local,domain=value.rsplit('@',1)
    if len(local)>64 or local.startswith('.') or local.endswith('.') or '..' in local:return False
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+",local):return False
    labels=domain.split('.')
    return len(labels)>1 and len(labels[-1])>=2 and not labels[-1].isdigit() and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in labels)


def safe_next(value):
    value=value or url_for('workspace.home')
    if len(value)>2048:return url_for('workspace.home')
    decoded=unquote(value)
    try:parts=urlsplit(decoded)
    except ValueError:return url_for('workspace.home')
    if not decoded.startswith('/') or decoded.startswith('//') or parts.scheme or parts.netloc or '\\' in decoded or any(ord(c)<32 for c in decoded):
        return url_for('workspace.home')
    return value


def find_user_by_email(email: str):
    ses = db_session.create_session()
    try:
        from sqlalchemy import func
        return ses.query(User).filter(func.lower(func.trim(User.email)) == email.lower().strip()).order_by(User.id).first()
    finally:
        ses.close()


def get_user_by_id(user_id: int):
    ses = db_session.create_session()
    try:
        return ses.get(User, user_id)
    finally:
        ses.close()


# --------------------------------------------------------------------------- #
# Коды подтверждения
# --------------------------------------------------------------------------- #

@app.before_request
def start_local_payment_worker():
    from services.local_payment_worker import start_worker
    start_worker(app)


AUTH_ENDPOINTS={'login','register','reset_password','resend','verify','reset_password_verify','reset_resend','change_password','logout'}

@app.before_request
def protect_auth_forms():
    if session.get('user_id') is not None:
        user=get_user_by_id(session['user_id'])
        if user is None or session.get('auth_version',0)!=(user.auth_version or 0):
            session.clear()
            flash('Пароль изменён или сессия завершена. Войдите снова.','error')
    if request.method=='POST' and request.endpoint in AUTH_ENDPOINTS:
        expected=session.get('auth_csrf','');actual=request.form.get('auth_csrf','')
        if not expected or not hmac.compare_digest(expected.encode(),actual.encode()):
            return render_template('auth_form_expired.html'),400


@app.before_request
def limit_auth_requests():
    if request.method != "POST" or request.endpoint not in AUTH_ENDPOINTS:
        return None
    ip = request.remote_addr or "unknown"
    email = request.form.get("email", "").lower().strip()
    limits = {
        "change_password": [("change-ip",ip,20,900),("change-user",session.get("user_id",ip),10,900)],
        "reset_resend": [("reset-resend-ip",ip,10,3600)],
        "login": [("login-ip", ip, 30, 900), ("login-email", email, 10, 900)],
        "register": [("register-ip", ip, 10, 3600)],
        "reset_password": [("reset-ip", ip, 10, 900), ("reset-email", email, 5, 900)],
        "resend": [("resend-ip", ip, 10, 3600)],
        "verify": [("verify-ip", ip, 30, 900)],
        "reset_password_verify": [("reset-verify-ip", ip, 30, 900)],
    }.get(request.endpoint, [])
    if limits:
        retry = consume_limits(limits)
        if retry:
            return render_template("rate_limit.html", retry_after=retry), 429, {"Retry-After": str(retry)}
    return None


# --------------------------------------------------------------------------- #
# Сессия, доступ
# --------------------------------------------------------------------------- #

def current_user():
    """Текущий пользователь из сессии или None."""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return get_user_by_id(user_id)


@app.context_processor
def inject_globals():
    session.setdefault("auth_csrf",secrets.token_hex(32))
    return {"current_user": current_user(),"auth_csrf":session["auth_csrf"]}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            flash("Сначала войдите в аккаунт.", "error")
            return redirect(url_for("login", next=request.path))

        user = current_user()
        if user is None:
            session.clear()
            flash("Сессия истекла. Войдите снова.", "error")
            return redirect(url_for("login"))

        if not user.is_verified:
            return redirect(url_for("verify"))

        return view(*args, **kwargs)

    return wrapped


def subscription_required(view):
    @login_required
    @wraps(view)
    def wrapped(*args, **kwargs):
        from services.billing_service import active, get_subscription
        if not active(get_subscription(session["user_id"])):
            return redirect(url_for("billing.plans"))
        return view(*args, **kwargs)
    return wrapped


from services.workspace import register_workspace
register_workspace(app, login_required, subscription_required)
from services.storage import register_storage
register_storage(app, subscription_required)
from services.products import register_products
register_products(app, subscription_required, login_required)
from services.product_import_routes import register_imports
register_imports(app, subscription_required)
from services.activity_routes import register_activity
register_activity(app, login_required)
from services.recipes import register_recipes
register_recipes(app, subscription_required)


# --------------------------------------------------------------------------- #
# Маршруты
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    if session.get("user_id") is not None:
        return redirect(url_for("workspace.home"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id") is not None:
        return redirect(url_for("workspace.home"))

    errors = []

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not valid_email(email):
            errors.append("Введите корректный email.")
        existing = find_user_by_email(email) if valid_email(email) else None
        if existing is not None and existing.is_verified:
            errors.append("Пользователь с таким email уже зарегистрирован. Войдите или восстановите пароль.")

        if not name or len(name)>80 or any(ord(c)<32 for c in name):
            errors.append("Введите имя: от 1 до 80 символов без управляющих знаков.")

        if not MIN_PASSWORD_LENGTH <= len(password) <= 256 or password.isspace():
            errors.append("Пароль должен содержать от 8 до 256 символов и не состоять только из пробелов.")

        if password != password2:
            errors.append("Пароли не совпадают.")

        if not errors and existing is not None and not existing.check_password(password):
            errors.append("Регистрация с этой почтой уже начата, но почта ещё не подтверждена. Введите прежний пароль или восстановите его через страницу входа.")

        if not errors:
            if existing is not None:
                # Proof of the original password permits resuming verification,
                # never replacing an account's password, name or verification code.
                user_id = existing.id
            else:
                with db_session.create_session() as ses:
                    user = User(email=email.lower().strip(), name=name)
                    user.set_password(password)
                    ses.add(user)
                    try:
                        ses.commit()
                    except IntegrityError:
                        ses.rollback()
                        # A double submit may have created the pending account meanwhile.
                        concurrent = find_user_by_email(email)
                        if concurrent is None or concurrent.is_verified or not concurrent.check_password(password):
                            errors.append("Пользователь с таким email уже зарегистрирован. Войдите или восстановите пароль.")
                            return render_template("register.html", email=email, name=name, errors=errors)
                        user_id = concurrent.id
                    else:
                        user_id = user.id
                        record_activity(user_id, "account_created", after={"name":user.name,"email":user.email})

            session.clear()
            session["user_id"] = user_id
            session["auth_version"]=get_user_by_id(user_id).auth_version or 0
            ok, msg = issue_code(email)
            if ok:
                flash(f"Мы отправили шестизначный код на {email}.", "success")
            else:
                flash(msg, "error")
            return redirect(url_for("verify"))

        return render_template("register.html", email=email, name=name, errors=errors)

    return render_template("register.html", email="", name="", errors=errors)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id") is not None:
        return redirect(url_for("workspace.home"))

    errors = []
    next_url = safe_next(request.args.get("next"))
    email=""

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = find_user_by_email(email) if len(email)<=254 and len(password)<=256 else None

        if user is None or not user.check_password(password):
            errors.append("Неверный email или пароль.")
        elif not user.is_verified:
            session.clear()
            session["user_id"] = user.id
            session["auth_version"]=user.auth_version or 0
            if not code_ready(user.id):
                ok,message=issue_code(user.email)
                if not ok:flash(message,"error")
            flash("Подтвердите email, чтобы войти.", "error")
            return redirect(url_for("verify"))
        else:
            session.clear()
            session["user_id"] = user.id
            session["auth_version"]=user.auth_version or 0
            record_activity(user.id, "login")
            flash("Вы вошли в аккаунт.", "success")
            return redirect(next_url)

    return render_template("login.html", errors=errors, next_url=next_url,email=email)


@app.route("/logout", methods=["POST"])
def logout():
    if session.get("user_id"):
        record_activity(session["user_id"], "logout")
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("index"))


@app.route("/profile")
@subscription_required
def profile():
    return render_template("profile.html", user=current_user())


@app.route("/verify", methods=["GET", "POST"])
def verify():
    user_id = session.get("user_id")
    if user_id is None:
        return redirect(url_for("login"))

    user = get_user_by_id(user_id)
    if user is None:
        session.clear()
        return redirect(url_for("register"))

    if user.is_verified:
        return redirect(url_for("workspace.home"))

    if request.method == "POST":
        code = request.form.get("code", "")
        if complete_code(user.id, code, "verify"):
            flash("Почта подтверждена. Выберите тариф, чтобы завершить регистрацию.", "success")
            return redirect(url_for("billing.plans"))
        flash("Неверный или истёкший код либо превышен лимит попыток.", "error")

    return render_template("verify.html", email=user.email,code_sent=code_ready(user.id))


@app.route("/resend", methods=["POST"])
def resend():
    user_id = session.get("user_id")
    if user_id is None:
        return redirect(url_for("login"))

    user = get_user_by_id(user_id)
    if user is None:
        session.clear()
        return redirect(url_for("register"))

    ok, message = issue_code(user.email)
    flash(f"Код отправлен на {user.email}." if ok else message,
          "success" if ok else "error")
    return redirect(url_for("verify"))


# --------------------------------------------------------------------------- #
# Смена пароля по почте
# --------------------------------------------------------------------------- #

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    errors = []
    email=""

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        user = find_user_by_email(email) if len(email)<=254 else None

        if user is None:
            errors.append("Аккаунт с таким email не найден.")
        else:
            # An already delivered, usable code can be entered even during cooldown.
            ready=code_ready(user.id,"reset")
            ok, msg = (True,"") if ready else issue_code(user.email, "reset")
            if ok:
                session["reset_user_id"] = user.id
                flash("Действующий код уже отправлен. Введите код из последнего письма." if ready else f"Код отправлен на {email}.", "success")
                return redirect(url_for("reset_password_verify"))
            errors.append(msg)

    return render_template("reset_password.html", errors=errors,email=email)


@app.route("/reset-password/verify", methods=["GET", "POST"])
def reset_password_verify():
    user_id = session.get("reset_user_id")
    if user_id is None:
        return redirect(url_for("reset_password"))

    user = get_user_by_id(user_id)
    if user is None:
        session.pop("reset_user_id", None)
        return redirect(url_for("reset_password"))

    errors = []

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not MIN_PASSWORD_LENGTH <= len(password) <= 256 or password.isspace():
            errors.append("Пароль должен содержать от 8 до 256 символов и не состоять только из пробелов.")

        if password != password2:
            errors.append("Пароли не совпадают.")

        if not errors and not complete_code(user.id, code, "reset", password):
            errors.append("Неверный или истёкший код либо превышен лимит попыток.")

        if not errors:
            session.clear()
            flash("Пароль изменён. Войдите с новым паролем.", "success")
            return redirect(url_for("login"))

    return render_template("reset_password_verify.html", email=user.email, errors=errors)


@app.route('/reset-password/resend',methods=['POST'])
def reset_resend():
    user=get_user_by_id(session.get('reset_user_id')) if session.get('reset_user_id') else None
    if user is None:return redirect(url_for('reset_password'))
    ok,message=issue_code(user.email,'reset')
    flash('Код отправлен повторно.' if ok else message,'success' if ok else 'error')
    return redirect(url_for('reset_password_verify'))


@app.route('/settings/password',methods=['GET','POST'])
@login_required
def change_password():
    errors=[]
    if request.method=='POST':
        password=request.form.get('password','');password2=request.form.get('password2','');current=request.form.get('current_password','')
        if password!=password2:errors.append('Пароли не совпадают.')
        elif len(current)>256:errors.append('Текущий пароль неверен.')
        else:
            ok,message,version=update_password(session['user_id'],current,password)
            if ok:
                uid=session['user_id'];session.clear();session.update(user_id=uid,auth_version=version)
                flash('Пароль изменён. На других устройствах потребуется войти снова.','success')
                return redirect(url_for('workspace.settings'))
            errors.append(message)
    return render_template('change_password.html',errors=errors,active_section='settings',workspace_back_url=url_for('workspace.settings'))


if __name__ == "__main__":
    app.run(debug=True, port=5000)