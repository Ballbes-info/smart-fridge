"""Account activity: allowlisted changes only; never secrets, cards or photo payloads."""
import time
import uuid
import sqlalchemy as sa
from .db_session import SqlAlchemyBase

class ActivityEvent(SqlAlchemyBase):
    __tablename__='activity_events'
    id=sa.Column(sa.String(32),primary_key=True,default=lambda:uuid.uuid4().hex)
    owner_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),nullable=False,index=True)
    category=sa.Column(sa.String(24),nullable=False,index=True)
    action=sa.Column(sa.String(40),nullable=False)
    before=sa.Column(sa.JSON,nullable=False,default=dict)
    after=sa.Column(sa.JSON,nullable=False,default=dict)
    created_at=sa.Column(sa.Float,nullable=False,default=time.time,index=True)
