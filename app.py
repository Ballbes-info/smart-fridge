"""SmartFridge — веб-приложение на Flask.

Регистрация (почта + пароль + имя), вход, подтверждение почты,
смена пароля по почте, профиль.

Запуск:
    python app.py
"""

import os
import re
import secrets
from functools import wraps
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
from services.auth_service import complete_code, consume_limits, issue_code

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
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()

    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    return key


app = Flask(__name__)
app.secret_key = _load_secret_key()
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


def find_user_by_email(email: str):
    ses = db_session.create_session()
    try:
        return ses.query(User).filter(User.email == email.lower().strip()).first()
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


@app.before_request
def limit_auth_requests():
    if request.method != "POST":
        return None
    ip = request.remote_addr or "unknown"
    email = request.form.get("email", "").lower().strip()
    limits = {
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
    return {"current_user": current_user()}


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

        if not EMAIL_RE.match(email):
            errors.append("Введите корректный email.")
        elif find_user_by_email(email) is not None:
            errors.append("Пользователь с таким email уже зарегистрирован.")

        if not name:
            errors.append("Введите имя.")

        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов.")

        if password != password2:
            errors.append("Пароли не совпадают.")

        if not errors:
            ses = db_session.create_session()
            try:
                user = User(email=email.lower().strip(), name=name)
                user.set_password(password)
                ses.add(user)
                ses.commit()
                user_id = user.id
            finally:
                ses.close()

            session.clear()
            session["user_id"] = user_id
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
    next_url = request.args.get("next") or url_for("workspace.home")

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = find_user_by_email(email)

        if user is None or not user.check_password(password):
            errors.append("Неверный email или пароль.")
        elif not user.is_verified:
            session.clear()
            session["user_id"] = user.id
            flash("Подтвердите email, чтобы войти.", "error")
            return redirect(url_for("verify"))
        else:
            session.clear()
            session["user_id"] = user.id
            flash("Вы вошли в аккаунт.", "success")
            return redirect(next_url)

    return render_template("login.html", errors=errors, next_url=next_url)


@app.route("/logout", methods=["POST"])
def logout():
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

    return render_template("verify.html", email=user.email)


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
    if session.get("user_id") is not None:
        return redirect(url_for("workspace.home"))

    errors = []

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        user = find_user_by_email(email)

        if user is None:
            errors.append("Аккаунт с таким email не найден.")
        else:
            ok, msg = issue_code(email, "reset")
            if ok:
                session["reset_user_id"] = user.id
                flash(f"Код отправлен на {email}.", "success")
                return redirect(url_for("reset_password_verify"))
            errors.append(msg)

    return render_template("reset_password.html", errors=errors)


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

        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов.")

        if password != password2:
            errors.append("Пароли не совпадают.")

        if not errors and not complete_code(user.id, code, "reset", password):
            errors.append("Неверный или истёкший код либо превышен лимит попыток.")

        if not errors:
            session.clear()
            flash("Пароль изменён. Войдите с новым паролем.", "success")
            return redirect(url_for("login"))

    return render_template("reset_password_verify.html", email=user.email, errors=errors)


if __name__ == "__main__":
    app.run(debug=True, port=5000)