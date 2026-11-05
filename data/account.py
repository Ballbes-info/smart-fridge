"""Private account preferences and separately confirmed email changes."""
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class AccountPreferences(SqlAlchemyBase):
    __tablename__ = 'account_preferences'
    user_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), primary_key=True)
    expiry_days = sa.Column(sa.Integer, nullable=False, default=3)
    expiry_notifications = sa.Column(sa.Boolean, nullable=False, default=True)
    revision = sa.Column(sa.Integer, nullable=False, default=1)


class EmailChange(SqlAlchemyBase):
    __tablename__ = 'account_email_changes'
    user_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), primary_key=True)
    email = sa.Column(sa.String(254), nullable=False)
    code_hash = sa.Column(sa.String, nullable=False)
    expires_at = sa.Column(sa.Float, nullable=False)
    attempts = sa.Column(sa.Integer, nullable=False, default=0)
    auth_version = sa.Column(sa.Integer, nullable=False)
