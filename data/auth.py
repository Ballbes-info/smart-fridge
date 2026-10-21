"""Одноразовые коды и ограничения запросов; отдельные таблицы для существующей БД."""
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class AuthCode(SqlAlchemyBase):
    __tablename__ = 'auth_codes'
    user_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), primary_key=True)
    purpose = sa.Column(sa.String, primary_key=True)
    code_hash = sa.Column(sa.String, nullable=False)
    expires_at = sa.Column(sa.Float, nullable=False)
    failed_attempts = sa.Column(sa.Integer, nullable=False, default=0)


class AuthRateLimit(SqlAlchemyBase):
    __tablename__ = 'auth_rate_limits'
    key = sa.Column(sa.String, primary_key=True)
    count = sa.Column(sa.Integer, nullable=False)
    expires_at = sa.Column(sa.Float, nullable=False)
