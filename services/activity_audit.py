"""Transaction-bound audit hooks for durable account settings and locations."""
from sqlalchemy import event,inspect
from sqlalchemy.orm import Session
from data.activity import ActivityEvent
from data.storage import StorageLocation
from data.billing import Subscription
from data.product import ProductPreferences
from data.user import User

RULES={StorageLocation:('storage','storage',['name','kind','location','note']),
       Subscription:('subscription','subscription',['plan','interval','extra_seats','status','period_start','period_end','cycle_amount','auto_renew','card_last4']),
       ProductPreferences:('account','default_storage',['default_storage_id']),
       User:('account','account',['name','email','is_verified'])}


def capture(ses,flush_context,instances):
    for obj in list(ses.new)+list(ses.dirty)+list(ses.deleted):
        rule=RULES.get(type(obj))
        if not rule:continue
        category,prefix,keys=rule
        new=obj in ses.new;deleted=obj in ses.deleted;state=inspect(obj)
        before={};after={}
        for key in keys:
            attr=state.attrs[key];value=getattr(obj,key)
            if not deleted:after[key]=value
            if not new:
                # Force a real pre-change value from the DB if SQLAlchemy did not load it.
                history=attr.history
                if history.deleted:before[key]=history.deleted[0]
                elif history.has_changes() and state.identity:
                    table=obj.__table__;identity=state.identity[0]
                    from sqlalchemy import select
                    before[key]=ses.connection().execute(select(table.c[key]).where(list(table.primary_key)[0]==identity)).scalar()
                else:before[key]=value
        password_changed=isinstance(obj,User) and not new and state.attrs.password_hash.history.has_changes()
        if not new and not deleted and before==after and not password_changed:continue
        owner=getattr(obj,'owner_id',None) or getattr(obj,'user_id',None) or (obj.id if isinstance(obj,User) else None)
        # Newly registered users get their registration event explicitly after their ID is assigned.
        if owner is None:continue
        action=prefix+('_created' if new else '_deleted' if deleted else '_changed')
        if password_changed:after['password_changed']=True
        ses.add(ActivityEvent(owner_id=owner,category=category,action=action,before=before,after=after))


def install():
    if not event.contains(Session,'before_flush',capture):event.listen(Session,'before_flush',capture)


def record(owner,action,*,category='account',before=None,after=None):
    from data import db_session
    with db_session.create_session() as ses:
        ses.add(ActivityEvent(owner_id=owner,category=category,action=action,before=before or {},after=after or {}));ses.commit()
