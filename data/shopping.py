"""Personal shopping and shared item details; mutations are durable replay receipts."""
import time
import secrets
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class ShoppingDetails:
    quantity=sa.Column(sa.String(32),nullable=False,default='')
    unit=sa.Column(sa.String(8),nullable=False,default='')
    brand=sa.Column(sa.String(120),nullable=False,default='')
    category=sa.Column(sa.String(24),nullable=False,default='other')
    expected_price=sa.Column(sa.Integer,nullable=True)
    store=sa.Column(sa.String(120),nullable=False,default='')
    priority=sa.Column(sa.Boolean,nullable=False,default=False)
    stocked=sa.Column(sa.Boolean,nullable=False,default=False)
    source=sa.Column(sa.JSON,nullable=False,default=dict)


class ShoppingItem(ShoppingDetails,SqlAlchemyBase):
    __tablename__='shopping_items'
    __table_args__=(sa.UniqueConstraint('owner_id','submission_key',name='uq_shopping_owner_submission'),)
    id=sa.Column(sa.Integer,primary_key=True)
    owner_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),nullable=False,index=True)
    submission_key=sa.Column(sa.String(48),nullable=False,default=lambda:secrets.token_hex(24))
    name=sa.Column(sa.String(120),nullable=False)
    note=sa.Column(sa.String(160),nullable=False,default='')
    bought=sa.Column(sa.Boolean,nullable=False,default=False)
    revision=sa.Column(sa.Integer,nullable=False,default=1)
    created_by=sa.Column(sa.Integer,sa.ForeignKey('users.id'),nullable=False)
    created_at=sa.Column(sa.Float,nullable=False,default=time.time)


class ShoppingMutation(SqlAlchemyBase):
    __tablename__='shopping_mutations'
    __table_args__=(sa.UniqueConstraint('scope','submission_key',name='uq_shopping_mutation'),)
    id=sa.Column(sa.Integer,primary_key=True)
    scope=sa.Column(sa.String(40),nullable=False,index=True)
    submission_key=sa.Column(sa.String(48),nullable=False)
    payload_hash=sa.Column(sa.String(64),nullable=False)
    result=sa.Column(sa.JSON,nullable=False,default=dict)
    created_at=sa.Column(sa.Float,nullable=False,default=time.time)


class ShoppingStore(SqlAlchemyBase):
    __tablename__='shopping_stores'
    id=sa.Column(sa.Integer,primary_key=True)
    owner_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),nullable=False,index=True)
    name=sa.Column(sa.String(80),nullable=False)
    url=sa.Column(sa.String(600),nullable=False)
