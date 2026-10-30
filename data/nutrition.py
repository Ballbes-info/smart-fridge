"""Private food diary, personal targets, water, weight and meal planning."""
import time
import sqlalchemy as sa
from .db_session import SqlAlchemyBase

class NutritionProfile(SqlAlchemyBase):
    __tablename__ = 'nutrition_profiles'
    owner_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), primary_key=True)
    values = sa.Column(sa.JSON, nullable=False, default=dict)
    revision = sa.Column(sa.Integer, nullable=False, default=1)

class NutritionEntry(SqlAlchemyBase):
    __tablename__ = 'nutrition_entries'
    __table_args__ = (sa.UniqueConstraint('owner_id', 'submission_key'), sa.Index('ix_nutrition_day', 'owner_id', 'day', 'meal'))
    id = sa.Column(sa.Integer, primary_key=True)
    owner_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False)
    day = sa.Column(sa.Date, nullable=False)
    meal = sa.Column(sa.String(16), nullable=False)
    name = sa.Column(sa.String(160), nullable=False)
    quantity = sa.Column(sa.String(24), nullable=False)
    unit = sa.Column(sa.String(12), nullable=False)
    nutrients = sa.Column(sa.JSON, nullable=False, default=dict)
    source = sa.Column(sa.JSON, nullable=False, default=dict)
    note = sa.Column(sa.String(1000), nullable=False, default='')
    stock_deducted = sa.Column(sa.Boolean, nullable=False, default=False)
    submission_key = sa.Column(sa.String(48), nullable=False)
    revision = sa.Column(sa.Integer, nullable=False, default=1)
    deleted = sa.Column(sa.Boolean, nullable=False, default=False)
    created_at = sa.Column(sa.Float, nullable=False, default=time.time)

class NutritionMeasure(SqlAlchemyBase):
    __tablename__ = 'nutrition_measures'
    __table_args__ = (sa.UniqueConstraint('owner_id', 'submission_key'), sa.Index('ix_nutrition_measure_day', 'owner_id', 'day', 'kind'))
    id = sa.Column(sa.Integer, primary_key=True)
    owner_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False)
    day = sa.Column(sa.Date, nullable=False)
    kind = sa.Column(sa.String(8), nullable=False)
    value = sa.Column(sa.Float, nullable=False)
    submission_key = sa.Column(sa.String(48), nullable=False)

class NutritionMenu(SqlAlchemyBase):
    __tablename__ = 'nutrition_menu'
    __table_args__ = (sa.UniqueConstraint('owner_id', 'day', 'meal'),)
    id = sa.Column(sa.Integer, primary_key=True)
    owner_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True)
    day = sa.Column(sa.Date, nullable=False)
    meal = sa.Column(sa.String(16), nullable=False)
    recipe_key = sa.Column(sa.String(24), nullable=False)
    snapshot = sa.Column(sa.JSON, nullable=False)
    portions = sa.Column(sa.Float, nullable=False, default=1)
    revision = sa.Column(sa.Integer, nullable=False, default=1)
