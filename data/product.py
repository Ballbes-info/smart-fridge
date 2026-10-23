"""A product identity is separate from its dated additions (batches)."""
import time
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class ProductGroup(SqlAlchemyBase):
    __tablename__ = 'product_groups'
    __table_args__ = (sa.UniqueConstraint('owner_id', 'storage_id', 'identity_key', name='uq_product_identity'),)
    id = sa.Column(sa.Integer, primary_key=True)
    owner_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True)
    storage_id = sa.Column(sa.Integer, sa.ForeignKey('storage_locations.id', ondelete='RESTRICT'), nullable=False, index=True)
    name = sa.Column(sa.String(120), nullable=False)
    brand = sa.Column(sa.String(120), nullable=False, default='')
    manufacturer = sa.Column(sa.String(160), nullable=False, default='')
    dimension = sa.Column(sa.String(12), nullable=False)
    identity_key = sa.Column(sa.String(64), nullable=False)
    search_key = sa.Column(sa.Text, nullable=False)
    details = sa.Column(sa.JSON, nullable=False)
    created_at = sa.Column(sa.Float, nullable=False, default=time.time)


class ProductBatch(SqlAlchemyBase):
    __tablename__ = 'product_batches'
    __table_args__ = (sa.UniqueConstraint('owner_id', 'submission_key', name='uq_product_submission'),)
    id = sa.Column(sa.Integer, primary_key=True)
    owner_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True)
    group_id = sa.Column(sa.Integer, sa.ForeignKey('product_groups.id', ondelete='RESTRICT'), nullable=False, index=True)
    quantity_micro = sa.Column(sa.BigInteger, nullable=False)
    entered_quantity = sa.Column(sa.String(32), nullable=False)
    unit = sa.Column(sa.String(8), nullable=False)
    expiry_date = sa.Column(sa.Date, nullable=True)
    manufactured_date = sa.Column(sa.Date, nullable=True)
    shelf_value = sa.Column(sa.Integer, nullable=True)
    shelf_unit = sa.Column(sa.String(8), nullable=True)
    opened_date = sa.Column(sa.Date, nullable=True)
    price_kopecks = sa.Column(sa.BigInteger, nullable=True)
    note = sa.Column(sa.Text, nullable=False, default='')
    added_at = sa.Column(sa.Float, nullable=False, default=time.time)
    submission_key = sa.Column(sa.String(64), nullable=False)
    payload_hash = sa.Column(sa.String(64), nullable=False)


class ProductPreferences(SqlAlchemyBase):
    __tablename__ = 'product_preferences'
    user_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), primary_key=True)
    default_storage_id = sa.Column(sa.Integer, sa.ForeignKey('storage_locations.id', ondelete='SET NULL'), nullable=True)
