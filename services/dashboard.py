"""Read-only daily overview, scoped to the user's currently accessible records."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from data import db_session
from data.product import ProductGroup, ProductBatch, ProductMutation
from data.storage import StorageLocation
from data.nutrition import NutritionMenu
from services import product_service as stock, family_service as family, shopping_service as shopping
from services import nutrition_service as nutrition
from services import recipe_library, recipe_catalog


def overview(owner, *, menu_allowed=False):
    today = stock.today()
    counts = dict(expired=0, today=0, soon=0, fresh=0, unknown=0)
    urgent = []
    expired = []
    groceries = []
    pending = 0
    shopping_counts = {}
    planned = {}
    planned_keys = {}
    with db_session.create_session() as ses:
        own_places = list(ses.scalars(select(StorageLocation).where(StorageLocation.owner_id == owner)))
        shared_places = [place for _, place, _ in family.accessible_places(ses, owner) if place.owner_id != owner]
        place_ids = [p.id for p in own_places + shared_places]
        rows = ses.execute(select(ProductBatch, ProductGroup, StorageLocation)
            .join(ProductGroup, ProductGroup.id == ProductBatch.group_id)
            .join(StorageLocation, StorageLocation.id == ProductGroup.storage_id)
            .where(StorageLocation.id.in_(place_ids), ProductBatch.owner_id == ProductGroup.owner_id,
                   ProductGroup.owner_id == StorageLocation.owner_id, ProductBatch.quantity_micro > 0)
            .order_by(ProductBatch.expiry_date.is_(None), ProductBatch.expiry_date, ProductBatch.id))
        groups = set()
        personal_groups = set()
        for batch, group, place in rows:
            groups.add(group.id)
            if group.owner_id == owner:
                personal_groups.add(group.id)
            days = (batch.expiry_date - today).days if batch.expiry_date else None
            state = 'unknown' if days is None else 'expired' if days < 0 else 'today' if days == 0 else 'soon' if days <= 3 else 'fresh'
            counts[state] += 1
            target = expired if state == 'expired' else urgent
            if state in ('today', 'soon', 'expired') and len(target) < 5:
                target.append(dict(name=group.name, group_id=group.id, batch_id=batch.id, storage_id=place.id,
                    shared=place.owner_id != owner, place=place.name, state=state,
                    label='Срок истёк' if days < 0 else 'Сегодня' if days == 0 else 'Через '+str(days)+' дн.',
                    quantity=stock.quantity_text(batch.quantity_micro, group.dimension)))
        for scope in ('personal', 'family'):
            try:
                model, condition, _, _ = shopping.scope(ses, owner, scope)
            except shopping.ShoppingError:
                continue
            condition = condition & model.bought.is_(False) & model.stocked.is_(False)
            shopping_counts[scope] = ses.scalar(select(func.count()).select_from(model).where(condition))
            pending += shopping_counts[scope]
            for item in ses.scalars(select(model).where(condition).order_by(model.priority.desc(), model.created_at, model.id).limit(4)):
                groceries.append(dict(id=item.id, created_at=item.created_at, name=item.name, quantity=item.quantity,
                    unit=stock.UNITS.get(item.unit, ('',))[0], priority=item.priority, scope=scope))
        groceries.sort(key=lambda item: (not item['priority'], item['created_at'], item['scope'], item['id']))
        if menu_allowed:
            for row in ses.scalars(select(NutritionMenu).where(
                    NutritionMenu.owner_id == owner, NutritionMenu.day == today)):
                planned[row.meal] = row.snapshot.get('title', 'Блюдо')
                planned_keys[row.meal] = row.recipe_key
        # Count operations, never mix kilograms, pieces and money in one graph.
        start = today - timedelta(days=6)
        msk = timezone(timedelta(hours=3))
        since = datetime.combine(start, datetime.min.time(), tzinfo=msk).timestamp()
        until = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=msk).timestamp()
        events = ses.execute(select(ProductMutation.created_at, ProductMutation.action).where(
            ProductMutation.owner_id == owner, ProductMutation.created_at >= since,
            ProductMutation.created_at < until, ProductMutation.action.in_(('use', 'waste'))))
        days = {start + timedelta(days=i): dict(use=0, waste=0) for i in range(7)}
        for stamp, action in events:
            days[datetime.fromtimestamp(stamp, msk).date()][action] += 1
    maximum = max([max(day.values()) for day in days.values()] + [1])
    weekly = [dict(label=day.strftime('%d.%m'), **values,
                   use_height=values['use'] / maximum * 100, waste_height=values['waste'] / maximum * 100)
              for day, values in days.items()]
    # Resolve only this owner's selected dishes; old menu snapshots need no migration.
    photos = {}
    for key in set(planned_keys.values()):
        recipe = recipe_library.recipe(owner, key)
        photos[key] = recipe_catalog.safe_url(recipe.get('image')) if recipe else ''
    diary = nutrition.summary(owner, today)
    return dict(day=today.isoformat(), date=today.strftime('%d.%m.%Y'), counts=counts, total_batches=sum(counts.values()),
        products=len(groups), personal_products=len(personal_groups), shared_products=len(groups-personal_groups), places=len(place_ids), shared=bool(shared_places), urgent=urgent, expired=expired,
        pending=pending, shopping_counts=shopping_counts, groceries=groceries[:4], menu_allowed=menu_allowed,
        meals=[dict(key=key, label=label, planned=planned.get(key), image=photos.get(planned_keys.get(key), ''), recorded=len(diary['meals'][key]))
               for key, label in nutrition.MEALS.items()], diary=diary,
        weekly=weekly, weekly_total=sum(d['use'] + d['waste'] for d in days.values()))
