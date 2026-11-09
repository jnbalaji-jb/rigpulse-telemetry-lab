from fastapi.testclient import TestClient

from rigpulse.api import create_app


def test_health_and_dashboard(populated):
    db,_,_,_=populated
    client=TestClient(create_app(db))
    assert client.get('/health').json()['status']=='ok'
    page=client.get('/')
    assert page.status_code==200
    assert 'Fleet health overview' in page.text
    assert client.get('/static/app.js').status_code==200
    assert client.get('/docs').status_code==200


def test_api_works_and_validates(populated):
    db,_,_,_=populated
    client=TestClient(create_app(db))
    assert client.get('/api/v1/summary').json()['readings']==1200
    assert len(client.get('/api/v1/rigs').json()['rigs'])==3
    items=client.get('/api/v1/alerts?limit=5').json()['items']
    assert 0<len(items)<=5
    data=client.get('/api/v1/series?rig_id=RIG-001&metric=temperature_c&limit=25').json()
    assert len(data['points'])==25
    assert client.get('/api/v1/series?rig_id=RIG-001&metric=wrong').status_code==422
    assert client.get('/api/v1/alerts?limit=9000').status_code==422
