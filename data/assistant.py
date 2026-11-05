"""Private assistant exchanges and account-level companion preference."""
import time
import sqlalchemy as sa
from .db_session import SqlAlchemyBase

class AssistantPreferences(SqlAlchemyBase):
    __tablename__='assistant_preferences'
    owner_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),primary_key=True)
    pet_enabled=sa.Column(sa.Boolean,nullable=False,default=False)
    pet_roaming=sa.Column(sa.Boolean,nullable=False,default=True)
    pet_hints=sa.Column(sa.Boolean,nullable=False,default=True)

class AssistantExchange(SqlAlchemyBase):
    __tablename__='assistant_exchanges'
    id=sa.Column(sa.String(32),primary_key=True)
    owner_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),nullable=False,index=True)
    nonce=sa.Column(sa.String(48),nullable=False)
    mode=sa.Column(sa.String(16),nullable=False)
    question=sa.Column(sa.Text,nullable=False)
    answer=sa.Column(sa.Text,nullable=False,default='')
    status=sa.Column(sa.String(16),nullable=False,default='pending')
    error=sa.Column(sa.String(300),nullable=False,default='')
    tokens=sa.Column(sa.JSON,nullable=False,default=dict)
    period_start=sa.Column(sa.Float,nullable=False)
    created_at=sa.Column(sa.Float,nullable=False,default=time.time,index=True)
    hidden=sa.Column(sa.Boolean,nullable=False,default=False)
    __table_args__=(sa.UniqueConstraint('owner_id','nonce'),sa.Index('ix_assistant_owner_period','owner_id','period_start','status'),sa.Index('ix_assistant_owner_history','owner_id','mode','hidden','created_at','id'))

class AssistantContext(SqlAlchemyBase):
    __tablename__='assistant_contexts'
    exchange_id=sa.Column(sa.String(32),sa.ForeignKey('assistant_exchanges.id'),primary_key=True)
    data=sa.Column(sa.JSON,nullable=False,default=dict)
