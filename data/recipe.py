"""Private recipe bookmarks, cooking state, nutrition edits and generation jobs."""
import time
import sqlalchemy as sa
from .db_session import SqlAlchemyBase

class RecipeState(SqlAlchemyBase):
    __tablename__='recipe_states'
    owner_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),primary_key=True)
    recipe_key=sa.Column(sa.String(24),primary_key=True)
    favorite=sa.Column(sa.Boolean,nullable=False,default=False,index=True)
    generated=sa.Column(sa.Boolean,nullable=False,default=False)
    snapshot=sa.Column(sa.JSON,nullable=False,default=dict)
    note=sa.Column(sa.String(2000),nullable=False,default='')
    checked=sa.Column(sa.JSON,nullable=False,default=list)
    nutrition=sa.Column(sa.JSON,nullable=False,default=dict)
    updated_at=sa.Column(sa.Float,nullable=False,default=time.time)

class RecipeGeneration(SqlAlchemyBase):
    __tablename__='recipe_generations'
    id=sa.Column(sa.String(32),primary_key=True)
    owner_id=sa.Column(sa.Integer,sa.ForeignKey('users.id'),nullable=False,index=True)
    period_start=sa.Column(sa.Float,nullable=False)
    status=sa.Column(sa.String(16),nullable=False)
    recipe_key=sa.Column(sa.String(24),nullable=True)
    error=sa.Column(sa.String(300),nullable=False,default='')
    tokens=sa.Column(sa.JSON,nullable=False,default=dict)
    created_at=sa.Column(sa.Float,nullable=False,default=time.time)
