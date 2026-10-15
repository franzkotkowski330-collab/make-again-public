import os,tempfile,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
ROOT=tempfile.mkdtemp(prefix='makeagain-b07-tests-')
os.environ.update(DATABASE_URL='sqlite:///'+ROOT+'/test.sqlite3',ENVIRONMENT='test',MOCK_LLM='true',MOCK_WECHAT='true',MOCK_SPEECH='true',MOCK_VISION='true',MOCK_STORAGE='true',MEDIA_SIGNING_KEY='isolated-test-key-01234567890123456789',STORAGE_LOCAL_DIR=ROOT+'/media',PUBLIC_BASE_URL='http://testserver')
import pytest,httpx
from fastapi.testclient import TestClient
from main import app
from memory.db import init_db,engine,Base
from api.security import _buckets

@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    Base.metadata.drop_all(engine)
    from sqlalchemy import text
    with engine.begin() as c:c.execute(text('DROP TABLE IF EXISTS schema_migrations'))
    init_db();_buckets.clear()
    def blocked(*a,**kw):raise AssertionError('External network forbidden in isolated tests')
    # TestClient uses its own transport; only outbound gateway convenience methods are blocked.
    monkeypatch.setattr(httpx,'get',blocked);monkeypatch.setattr(httpx,'post',blocked);monkeypatch.setattr(httpx,'request',blocked)
    yield

@pytest.fixture
def client():
    with TestClient(app) as c:yield c

def account(c,name='tester'):
    r=c.post('/api/auth/register',json={'username':name,'password':'secure-test-password'})
    assert r.status_code==200,r.json()
    u=r.json()['user_id'];h={'Authorization':'Bearer '+r.json()['token']}
    s=c.get('/api/state',headers=h).json()
    return u,h,s
