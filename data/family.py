"""Family membership and explicitly shared inventory. Personal records stay personal."""
import time
import secrets
import sqlalchemy as sa
from .db_session import SqlAlchemyBase


class Family(SqlAlchemyBase):
    __tablename__ = 'families'
    id = sa.Column(sa.Integer, primary_key=True)
    owner_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), unique=True, nullable=False)
    name = sa.Column(sa.String(60), nullable=False)
    created_at = sa.Column(sa.Float, default=time.time, nullable=False)


class FamilyMember(SqlAlchemyBase):
    __tablename__ = 'family_members'
    user_id = sa.Column(sa.Integer, sa.ForeignKey('users.id'), primary_key=True)
    family_id = sa.Column(sa.Integer, sa.ForeignKey('families.id'), index=True, nullable=False)
    joined_at = sa.Column(sa.Float, default=time.time, nullable=False)


class FamilyInvitation(SqlAlchemyBase):
    __tablename__ = 'family_invitations'
    id = sa.Column(sa.Integer, primary_key=True)
    family_id = sa.Column(sa.Integer, sa.ForeignKey('families.id'), index=True, nullable=False)
    email = sa.Column(sa.String(254), nullable=False, index=True)
    token_hash = sa.Column(sa.String(64), unique=True, nullable=False)
    status = sa.Column(sa.String(16), default='pending', nullable=False)
    created_at = sa.Column(sa.Float, default=time.time, nullable=False)
    expires_at = sa.Column(sa.Float, nullable=False)


class FamilyStorage(SqlAlchemyBase):
    __tablename__ = 'family_storages'
    storage_id = sa.Column(sa.Integer, sa.ForeignKey('storage_locations.id'), primary_key=True)
    family_id = sa.Column(sa.Integer, sa.ForeignKey('families.id'), index=True, nullable=False)
    permission = sa.Column(sa.String(8), nullable=False, default='edit')


class FamilyShoppingItem(SqlAlchemyBase):
    __tablename__ = 'family_shopping_items'
    __table_args__ = (sa.UniqueConstraint('family_id','submission_key',name='uq_family_shopping_submission'),)
    id = sa.Column(sa.Integer, primary_key=True)
    family_id = sa.Column(sa.Integer, sa.ForeignKey('families.id'), index=True, nullable=False)
    submission_key = sa.Column(sa.String(48), nullable=False, default=lambda: secrets.token_hex(24))
    name = sa.Column(sa.String(120), nullable=False)
    note = sa.Column(sa.String(160), nullable=False, default='')
    bought = sa.Column(sa.Boolean, default=False, nullable=False)
    revision = sa.Column(sa.Integer, default=1, nullable=False)
    created_by = sa.Column(sa.Integer, sa.ForeignKey('users.id'), nullable=False)
    created_at = sa.Column(sa.Float, default=time.time, nullable=False)


class FamilyEvent(SqlAlchemyBase):
    __tablename__ = 'family_events'
    id = sa.Column(sa.Integer, primary_key=True)
    family_id = sa.Column(sa.Integer, index=True, nullable=False)
    actor_id = sa.Column(sa.Integer, nullable=False)
    description = sa.Column(sa.String(300), nullable=False)
    created_at = sa.Column(sa.Float, default=time.time, nullable=False)
