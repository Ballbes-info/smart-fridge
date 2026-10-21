"""Persistent payment simulation. Stores only generated demo card references."""
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class LocalPayment(SqlAlchemyBase):
    __tablename__ = 'local_payments'
    id = sa.Column(sa.String, primary_key=True)
    idempotence_key = sa.Column(sa.String, unique=True, nullable=False)
    order_id = sa.Column(sa.String, nullable=False, index=True)
    user_id = sa.Column(sa.Integer, nullable=False)
    kind = sa.Column(sa.String, nullable=False)
    amount = sa.Column(sa.Integer, nullable=False)
    metadata_json = sa.Column(sa.JSON, nullable=False)
    return_path = sa.Column(sa.String, nullable=False)
    card_alias = sa.Column(sa.String, nullable=True)
    status = sa.Column(sa.String, default='pending', nullable=False)
    phase = sa.Column(sa.String, nullable=False)
    event = sa.Column(sa.String, nullable=True)
    reason = sa.Column(sa.String, nullable=True)
    ready_at = sa.Column(sa.Float, nullable=True)
    created_at = sa.Column(sa.Float, nullable=False)


class LocalRefund(SqlAlchemyBase):
    __tablename__ = 'local_refunds'
    id = sa.Column(sa.String, primary_key=True)
    idempotence_key = sa.Column(sa.String, unique=True, nullable=False)
    payment_id = sa.Column(sa.String, nullable=False)


class LocalEventState(SqlAlchemyBase):
    __tablename__ = 'local_event_state'
    key = sa.Column(sa.String, primary_key=True)
    successes_remaining = sa.Column(sa.Integer, nullable=False, default=0)
