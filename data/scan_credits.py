"""Separate AI allowances and one-time paid photo packs."""
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class ScanCreditPack(SqlAlchemyBase):
    __tablename__='scan_credit_packs'
    order_id=sa.Column(sa.String,sa.ForeignKey('billing_orders.id'),primary_key=True)
    user_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),nullable=False,index=True)
    kind=sa.Column(sa.String,nullable=False)
    package_id=sa.Column(sa.String,nullable=False)
    quantity=sa.Column(sa.Integer,nullable=False)
    submission_key=sa.Column(sa.String,nullable=False)
    created_at=sa.Column(sa.Float,nullable=False)
    __table_args__=(sa.UniqueConstraint('user_id','submission_key'),)


class ScanRequest(SqlAlchemyBase):
    __tablename__='ai_scan_requests'
    id=sa.Column(sa.String,primary_key=True)
    user_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),nullable=False,index=True)
    kind=sa.Column(sa.String,nullable=False)
    period_start=sa.Column(sa.Float,nullable=False)
    credit_pack_id=sa.Column(sa.String,sa.ForeignKey('scan_credit_packs.order_id'),nullable=True,index=True)
    status=sa.Column(sa.String,nullable=False)
    created_at=sa.Column(sa.Float,nullable=False)
