"""Test subscriptions, checkout operations and shared promotion history."""
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class Subscription(SqlAlchemyBase):
    __tablename__ = 'subscriptions'
    user_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), primary_key=True)
    plan = sa.Column(sa.String, nullable=True)
    interval = sa.Column(sa.String, default='month', nullable=False)
    extra_seats = sa.Column(sa.Integer, default=0, nullable=False)
    status = sa.Column(sa.String, default='inactive', nullable=False)
    period_start = sa.Column(sa.Float, nullable=True)
    period_end = sa.Column(sa.Float, nullable=True)
    cycle_amount = sa.Column(sa.Integer, default=0, nullable=False)
    trial_used = sa.Column(sa.Boolean, default=False, nullable=False)
    first_paid = sa.Column(sa.Boolean, default=False, nullable=False)
    auto_renew = sa.Column(sa.Boolean, default=False, nullable=False)
    consent_at = sa.Column(sa.Float, nullable=True)
    card_key = sa.Column(sa.String, nullable=True)
    method_id = sa.Column(sa.String, nullable=True)
    card_last4 = sa.Column(sa.String, nullable=True)
    pending_order_id = sa.Column(sa.String, nullable=True)
    revision = sa.Column(sa.Integer, default=0, nullable=False)


class CardPromotion(SqlAlchemyBase):
    __tablename__ = 'card_promotions'
    key = sa.Column(sa.String, primary_key=True)
    trial_used = sa.Column(sa.Boolean, default=False, nullable=False)
    first_paid = sa.Column(sa.Boolean, default=False, nullable=False)
    discount_owner = sa.Column(sa.Integer, nullable=True)
    discount_used = sa.Column(sa.Boolean, default=False, nullable=False)
    reserved_order_id = sa.Column(sa.String, nullable=True)


class BillingOrder(SqlAlchemyBase):
    __tablename__ = 'billing_orders'
    id = sa.Column(sa.String, primary_key=True)
    user_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True)
    kind = sa.Column(sa.String, nullable=False)  # bind, purchase, renewal
    plan = sa.Column(sa.String, nullable=False)
    interval = sa.Column(sa.String, nullable=False)
    extra_seats = sa.Column(sa.Integer, default=0, nullable=False)
    wants_trial = sa.Column(sa.Boolean, default=False, nullable=False)
    status = sa.Column(sa.String, default='created', nullable=False)
    amount = sa.Column(sa.Integer, default=0, nullable=False)  # kopecks
    regular_amount = sa.Column(sa.Integer, default=0, nullable=False)
    discounted = sa.Column(sa.Boolean, default=False, nullable=False)
    starts_at = sa.Column(sa.Float, nullable=True)
    ends_at = sa.Column(sa.Float, nullable=True)
    keeps_period = sa.Column(sa.Boolean, default=False, nullable=False)
    subscription_revision = sa.Column(sa.Integer, nullable=False)
    method_id = sa.Column(sa.String, nullable=True)
    card_key = sa.Column(sa.String, nullable=True)
    card_last4 = sa.Column(sa.String, nullable=True)
    provider_id = sa.Column(sa.String, unique=True, nullable=True)
    refund_id = sa.Column(sa.String, nullable=True)
    confirmation_url = sa.Column(sa.String, nullable=True)
    error = sa.Column(sa.String, nullable=True)
    created_at = sa.Column(sa.Float, nullable=False)
    finished_at = sa.Column(sa.Float, nullable=True)
    consent_at = sa.Column(sa.Float, nullable=True)
