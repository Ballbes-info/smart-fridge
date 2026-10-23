"""User-owned storage locations; no products or device readings are stored here."""
import time
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class StorageLocation(SqlAlchemyBase):
    __tablename__ = 'storage_locations'
    __table_args__ = (sa.UniqueConstraint('owner_id', 'name_key', name='uq_storage_owner_name'),)

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    owner_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True)
    name = sa.Column(sa.String(60), nullable=False)
    name_key = sa.Column(sa.String(120), nullable=False)
    kind = sa.Column(sa.String(20), nullable=False)
    location = sa.Column(sa.String(80), nullable=False, default='')
    note = sa.Column(sa.String(300), nullable=False, default='')
    revision = sa.Column(sa.Integer, nullable=False, default=1)
    created_at = sa.Column(sa.Float, nullable=False, default=time.time)
    updated_at = sa.Column(sa.Float, nullable=False, default=time.time)
