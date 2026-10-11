"""SmartFridge — веб-приложение на Flask.

Регистрация (почта + пароль + имя), вход, подтверждение почты,
смена пароля по почте, профиль.

Запуск:
    python app.py
"""

import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import mail
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
from werkzeug.security import check_password_hash, generate_password_hash

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

def set_verify_code(user_id: int) -> str:
    """Генерирует и сохраняет код подтверждения. Возвращает код для письма."""
    code = mail.generate_code()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=mail.code_ttl_seconds())).isoformat(
        timespec="seconds"
    )

    ses = db_session.create_session()
    try:
        user = ses.get(User, user_id)
        user.verify_code_hash = generate_password_hash(code)
        user.verify_code_expires = expires
        user.is_verified = False
        ses.commit()
    finally:
        ses.close()

    return code


def verify_code(user_id: int, code: str) -> bool:
    """Проверяет код подтверждения."""
    ses = db_session.create_session()
    try:
        user = ses.get(User, user_id)
        if user is None or not user.verify_code_hash or not user.verify_code_expires:
            return False

        try:
            expired = datetime.fromisoformat(user.verify_code_expires) < datetime.now(timezone.utc)
        except ValueError:
            return False

        if expired:
            return False

        if not check_password_hash(user.verify_code_hash, code.strip()):
            return False

        user.is_verified = True
        user.verify_code_hash = None
        user.verify_code_expires = None
        ses.commit()
        return True
    finally:
        ses.close()


def issue_code(email: str) -> tuple[bool, str]:
    """Отправляет код подтверждения. Возвращает (успех, сообщение)."""
    user = find_user_by_email(email)
    if user is None:
        return False, "Аккаунт не найден."

    code = set_verify_code(user.id)

    try:
        mail.send_code(user.email, code)
    except mail.MailError as exc:
        return False, str(exc)

    return True, ""


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


# --------------------------------------------------------------------------- #
# Маршруты
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id") is not None:
        return redirect(url_for("profile"))

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
        return redirect(url_for("profile"))

    errors = []
    next_url = request.args.get("next") or url_for("profile")

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
@login_required
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
        return redirect(url_for("profile"))

    if request.method == "POST":
        code = request.form.get("code", "")
        if verify_code(user.id, code):
            flash("Email подтверждён, аккаунт активирован.", "success")
            return redirect(url_for("profile"))
        flash("Неверный или истёкший код.", "error")

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
        return redirect(url_for("profile"))

    errors = []

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        user = find_user_by_email(email)

        if user is None:
            errors.append("Аккаунт с таким email не найден.")
        else:
            ok, msg = issue_code(email)
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

        if not verify_code(user.id, code):
            errors.append("Неверный или истёкший код.")

        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов.")

        if password != password2:
            errors.append("Пароли не совпадают.")

        if not errors:
            ses = db_session.create_session()
            try:
                u = ses.get(User, user.id)
                u.set_password(password)
                u.is_verified = True
                ses.commit()
            finally:
                ses.close()

            session.clear()
            flash("Пароль изменён. Войдите с новым паролем.", "success")
            return redirect(url_for("login"))

    return render_template("reset_password_verify.html", email=user.email, errors=errors)


if __name__ == "__main__":
    app.run(debug=True, port=5000)