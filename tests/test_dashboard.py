"""Run with python -m unittest discover -s tests. All records live in a temporary DB."""
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from data import db_session

_db = tempfile.TemporaryDirectory()
db_session.global_init(_db.name + '/dashboard.db')
from app import app
from data.user import User
from data.storage import StorageLocation
from data.product import ProductGroup, ProductBatch, ProductMutation
from data.shopping import ShoppingItem
from data.billing import Subscription
from data.nutrition import NutritionMenu, NutritionEntry
from data.family import Family, FamilyMember, FamilyStorage, FamilyStorageAccess, FamilyShoppingItem
from services.dashboard import overview
from services.product_service import today

app.config.update(TESTING=True, LOCAL_PAYMENT_WORKER=False)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        with db_session.create_session() as s:
            users = [User(email=uuid.uuid4().hex+'@example.test', name='Test', password_hash='unused', is_verified=True) for _ in range(2)]
            s.add_all(users); s.flush()
            self.owner, self.other = [u.id for u in users]
            for u in users:
                s.add(Subscription(user_id=u.id, plan='premium', status='active', period_end=time.time()+86400))
            s.commit()

    def product(self, owner, days, quantity=1000000, name='Продукт', place_id=None):
        with db_session.create_session() as s:
            if place_id is None:
                place = StorageLocation(owner_id=owner, name='Кухня', name_key=uuid.uuid4().hex, kind='fridge')
                s.add(place); s.flush(); place_id = place.id
            group = ProductGroup(owner_id=owner, storage_id=place_id, name=name, dimension='pieces', identity_key=uuid.uuid4().hex, search_key=name.lower(), details={})
            s.add(group); s.flush()
            s.add(ProductBatch(owner_id=owner, group_id=group.id, quantity_micro=quantity, entered_quantity='1', unit='pcs', expiry_date=today()+timedelta(days=days) if days is not None else None, submission_key=uuid.uuid4().hex, payload_hash='test'))
            s.commit()
            return place_id

    def test_empty_overview_is_honest(self):
        result = overview(self.owner)
        self.assertEqual(result['products'], 0)
        self.assertEqual(result['pending'], 0)
        self.assertEqual(result['weekly_total'], 0)
        self.assertEqual(result['diary']['entries'], [])
        self.assertFalse(any(m['planned'] for m in result['meals']))

    def test_expiry_boundaries_and_private_stock(self):
        for day in [-1, 0, 1, 3, 4, None]: self.product(self.owner, day)
        self.product(self.owner, 0, quantity=0)
        self.product(self.other, 0, name='Чужой продукт')
        result = overview(self.owner)
        self.assertEqual(result['counts'], dict(expired=1, today=1, soon=2, fresh=1, unknown=1))
        self.assertEqual(result['products'], 6)
        self.assertEqual(len(result['urgent']), 3)
        self.assertEqual(len(result['expired']), 1)
        self.assertNotIn('Чужой продукт', str(result))

    def test_expired_stock_does_not_hide_upcoming_expiry(self):
        for i in range(8): self.product(self.owner,-i-1,name='Просрочка '+str(i))
        self.product(self.owner,0,name='Использовать сегодня')
        result=overview(self.owner)
        self.assertEqual(result['counts']['expired'],8)
        self.assertEqual(result['urgent'][0]['name'],'Использовать сегодня')
        self.assertTrue(all(item['state']=='expired' for item in result['expired']))

    def test_dashboard_links_target_the_selected_item_and_meal(self):
        from html.parser import HTMLParser
        class Links(HTMLParser):
            def __init__(self): super().__init__(); self.urls=[]
            def handle_starttag(self,tag,attrs):
                if tag=='a': self.urls.append(dict(attrs).get('href',''))
        with db_session.create_session() as s:
            item=ShoppingItem(owner_id=self.owner,created_by=self.owner,name='Молоко')
            s.add(item); s.commit(); item_id=item.id
        with app.test_client() as client:
            with client.session_transaction() as session: session['user_id']=self.owner
            html=client.get('/home').get_data(as_text=True)
            links=Links(); links.feed(html)
            self.assertIn('/shopping/'+str(item_id)+'/edit?scope=personal',links.urls)
            for meal in ('breakfast','lunch','dinner','snack'):
                self.assertIn('/home/nutrition/menu#meal-'+str(today())+'-'+meal,links.urls)
            plan=client.get('/home/nutrition/menu').get_data(as_text=True)
            self.assertIn('id="meal-'+str(today())+'-lunch"',plan)
            shopping_html=client.get('/home/shopping?add=1').get_data(as_text=True)
            self.assertIn('class="sh-panel sh-add" open',shopping_html)
            self.assertNotIn('Больше времени для себя',html)
            self.assertNotIn('Начните с того',html)

    def test_shared_access_revocation_and_family_purchases(self):
        place = self.product(self.other, 0, name='Общее молоко')
        with db_session.create_session() as s:
            s.get(Subscription, self.other).plan = 'family_premium'
            family = Family(owner_id=self.other, name='Дом'); s.add(family); s.flush()
            s.add_all([FamilyMember(user_id=uid, family_id=family.id) for uid in (self.owner,self.other)])
            s.add(FamilyStorage(storage_id=place, family_id=family.id, permission='view'))
            s.add(FamilyShoppingItem(family_id=family.id, created_by=self.other, name='Общий хлеб'))
            s.commit()
        self.assertEqual(overview(self.owner)['products'], 1)
        self.assertEqual(overview(self.owner)['pending'], 1)
        with db_session.create_session() as s:
            s.add(FamilyStorageAccess(storage_id=place,user_id=self.owner,permission='none')); s.commit()
        self.assertEqual(overview(self.owner)['products'], 0)
        with db_session.create_session() as s:
            s.delete(s.get(FamilyMember,self.owner)); s.commit()
        self.assertEqual(overview(self.owner)['pending'], 0)

    def test_pending_purchases_and_priority(self):
        with db_session.create_session() as s:
            for owner,name,bought,stocked,priority in [(self.owner,'Обычное',False,False,False),(self.owner,'Важное',False,False,True),(self.owner,'Куплено',True,False,False),(self.owner,'В запасах',True,True,False),(self.other,'Чужое',False,False,True)]:
                s.add(ShoppingItem(owner_id=owner,created_by=owner,name=name,bought=bought,stocked=stocked,priority=priority))
            s.commit()
        result=overview(self.owner)
        self.assertEqual(result['pending'],2)
        self.assertEqual([i['name'] for i in result['groceries']],['Важное','Обычное'])

    def test_menu_is_personal_today_and_entitled(self):
        with db_session.create_session() as s:
            for owner,day,title in [(self.owner,today(),'Мой завтрак'),(self.owner,today()+timedelta(days=1),'Завтра'),(self.other,today(),'Чужой завтрак')]:
                s.add(NutritionMenu(owner_id=owner,day=day,meal='breakfast',recipe_key='a'*24,snapshot={'title':title}))
            s.add(NutritionEntry(owner_id=self.owner,day=today(),meal='breakfast',name='Еда',quantity='1',unit='portion',nutrients={'calories':None},submission_key=uuid.uuid4().hex))
            s.commit()
        result=overview(self.owner,menu_allowed=True)
        self.assertEqual(result['meals'][0]['planned'],'Мой завтрак')
        self.assertEqual(result['diary']['missing']['calories'],1)
        self.assertFalse(any(m['planned'] for m in overview(self.owner,menu_allowed=False)['meals']))


    def test_selected_meal_photo_and_missing_photo_fallback(self):
        from unittest.mock import patch
        with db_session.create_session() as s:
            for meal, key in [('breakfast', 'a'*24), ('lunch', 'b'*24), ('dinner', 'c'*24)]:
                s.add(NutritionMenu(owner_id=self.owner, day=today(), meal=meal,
                    recipe_key=key, snapshot={'title': 'Блюдо ' + meal}))
            s.commit()
        def lookup(owner, key):
            self.assertEqual(owner, self.owner)
            return {'image': 'https://www.povarenok.ru/images/meal.jpg'} if key == 'a'*24 else (
                {'image': 'javascript:alert(1)'} if key == 'b'*24 else None)
        with patch('services.dashboard.recipe_library.recipe', side_effect=lookup) as recipe:
            result = overview(self.owner, menu_allowed=True)
            self.assertEqual(result['meals'][0]['image'], 'https://www.povarenok.ru/images/meal.jpg')
            self.assertEqual(result['meals'][1]['image'], '')
            self.assertEqual(result['meals'][2]['image'], '')
            recipe.reset_mock()
            self.assertTrue(all(not m['image'] for m in overview(self.owner)['meals']))
            recipe.assert_not_called()
            with app.test_client() as client:
                with client.session_transaction() as session: session['user_id'] = self.owner
                html = client.get('/home').get_data(as_text=True)
                self.assertEqual(html.count('data-daily-photo'), 1)
                self.assertIn('Блюдо dinner', html)
                self.assertNotIn('javascript:alert', html)

    def test_week_chart_has_moscow_boundaries_and_does_not_write(self):
        midnight=datetime.combine(today(),datetime.min.time(),tzinfo=timezone(timedelta(hours=3))).timestamp()
        with db_session.create_session() as s:
            for owner,stamp,action in [(self.owner,midnight,'use'),(self.owner,midnight-1,'waste'),(self.owner,midnight-7*86400,'use'),(self.owner,midnight+86400,'use'),(self.other,midnight,'use')]:
                s.add(ProductMutation(owner_id=owner,batch_id=1,action=action,submission_key=uuid.uuid4().hex,payload_hash='test',before={},after={},created_at=stamp))
            s.commit()
        with db_session.create_session() as s:
            before={t.name:s.scalar(select(func.count()).select_from(t)) for t in db_session.SqlAlchemyBase.metadata.sorted_tables}
        result=overview(self.owner)
        self.assertEqual(result['weekly_total'],2)
        self.assertEqual(result['weekly'][-1]['use'],1)
        self.assertEqual(result['weekly'][-2]['waste'],1)
        with db_session.create_session() as s:
            after={t.name:s.scalar(select(func.count()).select_from(t)) for t in db_session.SqlAlchemyBase.metadata.sorted_tables}
        self.assertEqual(before,after)

    def test_routes_links_and_basic_plan(self):
        with app.test_client() as client:
            with client.session_transaction() as session:session['user_id']=self.owner
            response=client.get('/home')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            for path in ['/home/fridges','/home/fridges/storage','/home/fridges/scan','/home/shopping','/home/recipes','/home/nutrition','/home/nutrition/menu','/home/statistics','/home/family','/settings','/profile']:
                self.assertEqual(client.get(path).status_code,200,path)
            with db_session.create_session() as s:s.get(Subscription,self.owner).plan='basic';s.commit()
            response=client.get('/home')
            self.assertEqual(response.status_code,200)
            self.assertIn('Питание сегодня',response.get_data(as_text=True))
            self.assertEqual(client.get('/home/nutrition/menu').status_code,403)
        self.assertEqual(app.test_client().get('/home').status_code,302)


if __name__ == '__main__': unittest.main()
