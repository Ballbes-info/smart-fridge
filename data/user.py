from datetime import datetime, timezone

import sqlalchemy
from werkzeug.security import check_password_hash, generate_password_hash

from .db_session import SqlAlchemyBase


class User(SqlAlchemyBase):
    __tablename__ = "users"

    id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True, autoincrement=True)
    email = sqlalchemy.Column(sqlalchemy.String, unique=True, nullable=False, index=True)
    name = sqlalchemy.Column(sqlalchemy.String, nullable=False)
    password_hash = sqlalchemy.Column(sqlalchemy.String, nullable=False)
    created_at = sqlalchemy.Column(
        sqlalchemy.String,
        default=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    is_verified = sqlalchemy.Column(sqlalchemy.Boolean, default=False, nullable=False)
    auth_version = sqlalchemy.Column(sqlalchemy.Integer, nullable=False, default=0, server_default="0")
    verify_code_hash = sqlalchemy.Column(sqlalchemy.String, nullable=True)
    verify_code_expires = sqlalchemy.Column(sqlalchemy.String, nullable=True)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)