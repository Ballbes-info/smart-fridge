"""SmartFridge — начальный веб-сайт на Flask.

Главная страница, регистрация и вход по email, защищённый профиль.

Запуск:
    python app.py
"""

import click
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "smartfridge.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"

load_dotenv(BASE_DIR / ".env")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD_LENGTH = 8

# Проверятся, когда email не найден: время ответа остаётся прежним.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def _load_secret_key() -> str:
    """Секретный ключ берём из окружения, иначе храним в файле .secret_key."""
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

def get_db() -> sqlite3.Connection:
    """Соединение с SQLite на время обработки запроса."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


# Таблицы нужны при любом способе запуска — и `python app.py`, и `flask run`.
init_db()


@app.cli.command("init-db")
def init_db_command():
    """Создать таблицы отдельно: flask --app app init-db"""
    init_db()
    click.echo(f"Готово: {DB_PATH}")


def find_user_by_email(email: str):
    return get_db().execute(
        "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
    ).fetchone()


def create_user(email: str, password: str) -> int:
    """Создаёт пользователя и возвращает его id."""
    db = get_db()
    cursor = db.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (
            email.lower().strip(),
            generate_password_hash(password),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    return cursor.lastrowid


# --------------------------------------------------------------------------- #
# Защита: сессия, CSRF, доступ
# --------------------------------------------------------------------------- #

def current_user():
    """Текущий пользователь из сессии или None."""
    user_id = session.get("user_id")
    if user_id is None:
        return None

    return get_db().execute(
        "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()


@app.context_processor
def inject_globals():
    """Доступ к пользователю и CSRF-токену во всех шаблонах."""
    return {"current_user": current_user(), "csrf_token": csrf_token()}


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def csrf_valid() -> bool:
    sent = request.form.get("csrf_token", "")
    stored = session.get("_csrf", "")
    return bool(stored) and secrets.compare_digest(sent, stored)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            flash("Сначала войдите в аккаунт.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def safe_next_url(raw):
    """Разрешаем только внутренние пути, чтобы редирект не вёл на чужие сайты."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return url_for("profile")


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
        if not csrf_valid():
            abort(400)

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not EMAIL_RE.match(email):
            errors.append("Введите корректный email.")
        elif find_user_by_email(email) is not None:
            errors.append("Пользователь с таким email уже зарегистрирован.")

        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов.")

        if password != password2:
            errors.append("Пароли не совпадают.")

        if not errors:
            user_id = create_user(email, password)
            session.clear()
            session["user_id"] = user_id
            flash("Регистрация завершена. Добро пожаловать!", "success")
            return redirect(url_for("profile"))

        # Возвращаем введённый email, чтобы не переполнять форму заново.
        return render_template("register.html", email=email, errors=errors)

    return render_template("register.html", email="", errors=errors)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id") is not None:
        return redirect(url_for("profile"))

    errors = []
    next_url = safe_next_url(request.args.get("next"))

    if request.method == "POST":
        if not csrf_valid():
            abort(400)

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = find_user_by_email(email) if email else None

        # Хэш проверяем всегда: иначе по времени ответа видно,
        # зарегистрирован ли такой email.
        password_hash = user["password_hash"] if user else _DUMMY_HASH
        password_ok = check_password_hash(password_hash, password)

        if user is None or not password_ok:
            # Одинаковая ошибка, чтобы не подсвечивать существующие email.
            errors.append("Неверный email или пароль.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash("Вы вошли в аккаунт.", "success")
            return redirect(next_url)

        return render_template("login.html", errors=errors, next_url=next_url)

    return render_template("login.html", errors=errors, next_url=next_url)


@app.route("/logout", methods=["POST"])
def logout():
    if not csrf_valid():
        abort(400)

    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("index"))


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html", user=current_user())


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
