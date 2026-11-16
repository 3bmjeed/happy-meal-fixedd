import pytest
from app import create_app, db, User, Restaurant, FoodListing
from seed import seed_demo_data
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta

@pytest.fixture()
def client():
    app=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':'sqlite://','JWT_SECRET_KEY':'test-secret','SECRET_KEY':'test-secret'})
    with app.app_context(): db.create_all()
    with app.test_client() as client: yield client
    with app.app_context(): db.drop_all()

def register(client,email='user@example.com',role='customer'):
    return client.post('/api/auth/register',json={'name':'Test User','email':email,'password':'password123','role':role})
def token(response): return {'Authorization':'Bearer '+response.json['access_token']}

def test_register_and_duplicate_email(client):
    assert register(client).status_code==201
    assert register(client).status_code==409

def test_login_rejects_wrong_password(client):
    register(client)
    assert client.post('/api/auth/login',json={'email':'user@example.com','password':'wrong'}).status_code==401

def test_role_permissions(client):
    response=register(client)
    assert client.post('/api/listings',headers=token(response),json={}).status_code==403

def test_listing_requires_approved_restaurant(client):
    owner=register(client,'owner@example.com','restaurant')
    assert client.post('/api/listings',headers=token(owner),json={}).status_code==403

def test_listing_browse_returns_only_available_meals(client):
    owner=register(client,'owner@example.com','restaurant')
    with client.application.app_context():
        restaurant=Restaurant(owner_id=owner.json['user']['id'],name='Approved Kitchen',approved=True)
        db.session.add(restaurant); db.session.flush()
        db.session.add_all([
            FoodListing(restaurant_id=restaurant.id,title='Fresh meal',category='Meals',
                        original_price=1000,discounted_price=500,quantity=2,
                        pickup_deadline=datetime.utcnow()+timedelta(days=1)),
            FoodListing(restaurant_id=restaurant.id,title='Expired meal',category='Meals',
                        original_price=1000,discounted_price=500,quantity=2,
                        pickup_deadline=datetime.utcnow()-timedelta(days=1)),
        ])
        db.session.commit()

    response=client.get('/api/listings')

    assert response.status_code==200
    assert [item['title'] for item in response.json['items']]==['Fresh meal']

def test_seed_refreshes_expired_demo_meals_without_replenishing(client):
    with client.application.app_context():
        owner=User(name='Green Oven',email='restaurant@saveameal.local',
                   password_hash=generate_password_hash('password123'),role='restaurant')
        db.session.add(owner); db.session.flush()
        restaurant=Restaurant(owner_id=owner.id,name='Green Oven Bakery',approved=True)
        db.session.add(restaurant); db.session.flush()
        listing=FoodListing(restaurant_id=restaurant.id,title='Bakery Surprise Box',
                            category='Bakery',original_price=15000,discounted_price=7000,
                            quantity=4,pickup_deadline=datetime.utcnow()-timedelta(days=1))
        db.session.add(listing); db.session.commit()

    seed_demo_data(client.application)
    seed_demo_data(client.application)

    response=client.get('/api/listings')
    assert response.status_code==200
    refreshed=next(item for item in response.json['items'] if item['title']=='Bakery Surprise Box')
    assert refreshed['quantity']==4
    with client.application.app_context():
        assert FoodListing.query.filter_by(title='Bakery Surprise Box').count()==1

def test_health(client): assert client.get('/api/health').json['status']=='ok'


def test_order_cannot_be_reopened_after_cancel(client):
    from datetime import datetime, timedelta
    owner=register(client,'owner2@example.com','restaurant')
    with client.application.app_context():
        r=Restaurant(owner_id=owner.json['user']['id'],name='K',approved=True); db.session.add(r); db.session.flush()
        l=FoodListing(restaurant_id=r.id,title='M',category='Meals',original_price=1000,discounted_price=500,quantity=2,pickup_deadline=datetime.utcnow()+timedelta(days=1))
        db.session.add(l); db.session.commit(); lid=l.id
    cu=register(client,'cust@example.com'); h=token(cu)
    pt=(datetime.utcnow()+timedelta(hours=1)).isoformat()+'Z'
    oid=client.post('/api/orders',headers=h,json={'pickup_time':pt,'items':[{'listing_id':lid,'quantity':2}]}).json['order']['id']
    assert client.patch(f'/api/orders/{oid}',headers=h,json={'status':'Cancelled'}).status_code==200
    assert client.patch(f'/api/orders/{oid}',headers=h,json={'status':'Confirmed'}).status_code in (403,409)
    assert client.patch(f'/api/orders/{oid}',headers=h,json={'status':'Cancelled'}).status_code==409
    with client.application.app_context(): assert db.session.get(FoodListing,lid).quantity==2

def test_admin_can_approve_charity(client):
    from app import CharityPartner
    ch=register(client,'charity@example.com','charity')
    assert client.get('/api/donations',headers=token(ch)).status_code==403
    with client.application.app_context():
        db.session.add(User(name='A',email='admin@example.com',password_hash=generate_password_hash('password123'),role='admin')); db.session.commit()
    ad=client.post('/api/auth/login',json={'email':'admin@example.com','password':'password123'})
    cid=client.get('/api/admin/charities',headers=token(ad)).json['items'][0]['id']
    assert client.patch(f'/api/admin/charities/{cid}',headers=token(ad),json={'approved':True}).status_code==200
    assert client.get('/api/donations',headers=token(ch)).status_code==200
