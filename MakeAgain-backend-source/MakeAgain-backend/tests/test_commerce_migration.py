import base64,json,time,subprocess,os,sys
from pathlib import Path
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select,func
from conftest import account
from memory.db import _RawSessionLocal
from memory import models as m
from memory.b07_models import Plan,Order,MessageReceipt,Job,Operation
from config import get_settings
from gateway.payments import WeChatPay,PaymentError

@pytest.fixture
def pay_keys(tmp_path,monkeypatch):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    private=tmp_path/'merchant.pem';public=tmp_path/'platform.pem'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    s=get_settings()
    for name,value in dict(wechat_appid='test-app',wechat_pay_mchid='test-merchant',wechat_pay_serial_no='test-merchant-serial',wechat_pay_private_key_path=str(private),wechat_pay_platform_key_path=str(public),wechat_pay_platform_serial='test-platform-serial',wechat_pay_api_v3_key='01234567890123456789012345678901',wechat_pay_notify_url='https://test.invalid/api/payments/wechat/notify',payment_allowed_platforms='android').items():monkeypatch.setattr(s,name,value)
    return key

def notification(key,order,**overrides):
    data={'out_trade_no':order.id,'transaction_id':'tx-'+order.id,'appid':'test-app','mchid':'test-merchant','payer':{'openid':order.user_id},'amount':{'total':order.amount,'currency':'CNY'},'trade_state':'SUCCESS',**overrides}
    nonce='abcdef012345';aad='transaction';encrypted=AESGCM(get_settings().wechat_pay_api_v3_key.encode()).encrypt(nonce.encode(),json.dumps(data).encode(),aad.encode())
    raw=json.dumps({'event_type':'TRANSACTION.SUCCESS','resource':{'algorithm':'AEAD_AES_256_GCM','nonce':nonce,'associated_data':aad,'ciphertext':base64.b64encode(encrypted).decode()}},separators=(',',':')).encode()
    ts=str(int(time.time()));signature=key.sign(ts.encode()+b'\nnonce\n'+raw+b'\n',padding.PKCS1v15(),hashes.SHA256())
    headers={'Wechatpay-Timestamp':ts,'Wechatpay-Nonce':'nonce','Wechatpay-Serial':'test-platform-serial','Wechatpay-Signature':base64.b64encode(signature).decode(),'Content-Type':'application/json'}
    return raw,headers

def make_order(user):
    with _RawSessionLocal() as db:
        o=Order(user_id=user,request_id='payment',plan_id='test-plan',snapshot={'name':'Test only'},amount=123,duration_days=30,status='unpaid',expires_at=m.utcnow()+timedelta(minutes=30))
        db.add(o);db.commit();return o


def test_callback_signature_decryption_amount_owner_and_replay(client,pay_keys):
    u,h,s=account(client);o=make_order(u)
    raw,headers=notification(pay_keys,o)
    assert client.post('/api/payments/wechat/notify',content=raw+b' ',headers=headers).status_code==401
    wrong,hwrong=notification(pay_keys,o,amount={'total':1,'currency':'CNY'})
    assert client.post('/api/payments/wechat/notify',content=wrong,headers=hwrong).status_code==400
    wrong,hwrong=notification(pay_keys,o,payer={'openid':'other'})
    assert client.post('/api/payments/wechat/notify',content=wrong,headers=hwrong).status_code==400
    with ThreadPoolExecutor(max_workers=3) as pool:rs=list(pool.map(lambda _:client.post('/api/payments/wechat/notify',content=raw,headers=headers),range(3)))
    assert [r.status_code for r in rs]==[200,200,200],[r.json() for r in rs]
    me=client.get('/api/auth/me',headers=h).json();expiry=me['membership_expires_at'];assert me['membership']=='member'
    assert client.post('/api/payments/wechat/notify',content=raw,headers=headers).status_code==200
    assert client.get('/api/auth/me',headers=h).json()['membership_expires_at']==expiry
    params=WeChatPay().payment_params('prepay-test')['params']
    message=f'test-app\n{params["timeStamp"]}\n{params["nonceStr"]}\n{params["package"]}\n'.encode()
    pay_keys.public_key().verify(base64.b64decode(params['paySign']),message,padding.PKCS1v15(),hashes.SHA256())


def test_original_order_survives_unknown_prepay(client,pay_keys,monkeypatch):
    u,h,s=account(client)
    with _RawSessionLocal() as db:
        db.delete(db.get(m.UserAuth,u));db.add(Plan(id='plan',name='Test only',amount=123,duration_days=7,period_label='7 test days',benefits='Test',terms='No actual sale',platforms=['android'],enabled=True));db.commit()
    ids=[]
    def unknown(self,o):ids.append(o.id);raise PaymentError('PAYMENT_RESULT_UNKNOWN')
    monkeypatch.setattr(WeChatPay,'prepay',unknown)
    body={'plan_id':'plan','client_request_id':'original','platform':'android'}
    r=client.post('/api/subscriptions/orders',headers=h,json=body);assert r.status_code==200,r.json();order_id=r.json()['order_id']
    assert r.json()['status']=='processing'
    assert client.post('/api/subscriptions/orders',headers=h,json=body).json()==r.json()
    assert len(ids)==1
    with _RawSessionLocal() as db:
        o=db.get(Order,order_id);assert o.request_id=='original'
        assert db.scalar(select(func.count()).select_from(Order))==1
    def query(self,o):return {'trade_state':'NOTPAY'}
    monkeypatch.setattr(WeChatPay,'query',query)
    monkeypatch.setattr(WeChatPay,'prepay',lambda self,o:'same-original-prepay')
    r=client.post('/api/subscriptions/orders/'+order_id+'/resume',headers=h,json={'request_id':'resume'})
    assert r.status_code==200,r.json();assert r.json()['order_id']==order_id;assert r.json()['payment']['params']['package']=='prepay_id=same-original-prepay'
    v,h2,s2=account(client,'other')
    assert client.get('/api/subscriptions/orders/by-request/original',headers=h2).status_code==404


def test_message_receipts_credit_retry_unknown_and_reject(client,monkeypatch):
    s=get_settings();monkeypatch.setattr(s,'wechat_subscribe_templates',json.dumps([{'id':'test-template','title':'Test','description':'Test only','purpose':'test-purpose','page':'pages/home/index','data':{'thing1':{'value':'Test only'}}}]))
    u,h,state=account(client);admin,ah,ss=account(client,'admin')
    with _RawSessionLocal() as db:db.get(m.User,admin).role='admin';db.commit()
    body={'template_id':'test-template','choice':'accept','client_request_id':'accept'}
    assert client.post('/api/subscriptions/messages/receipts',headers=h,json=body).status_code==200
    assert client.post('/api/subscriptions/messages/receipts',headers=h,json=body).status_code==200
    assert client.get('/api/subscriptions/messages/status',headers=h).json()['templates'][0]['remaining']==1
    j=client.post('/api/admin/subscriptions/messages/jobs',headers=ah,json={'user_id':u,'template_id':'test-template','purpose':'test-purpose','request_id':'job'});assert j.status_code==200,j.json()
    assert client.get('/api/subscriptions/messages/status',headers=h).json()['templates'][0]['remaining']==0
    from gateway import messages
    from admin_cli import run_one
    def transient(*a):raise messages.MessageError('TOKEN_UNAVAILABLE',retryable=True)
    monkeypatch.setattr(messages,'send_message',transient);assert run_one()
    with _RawSessionLocal() as db:
        row=db.get(Job,j.json()['id']);assert row.status=='retry';row.due_at=m.utcnow()-timedelta(seconds=1);db.commit()
    def unknown(*a):raise messages.MessageError('SEND_RESULT_UNKNOWN',unknown=True)
    monkeypatch.setattr(messages,'send_message',unknown);assert run_one()
    with _RawSessionLocal() as db:assert db.get(Job,j.json()['id']).status=='unknown'
    assert not run_one()
    assert client.post('/api/subscriptions/messages/receipts',headers=h,json={**body,'choice':'reject','client_request_id':'reject'}).status_code==200
    assert client.get('/api/subscriptions/messages/status',headers=h).json()['templates'][0]['choice']=='reject'


def test_legacy_migration_preserves_duplicates_and_perpetual(tmp_path):
    root=Path(__file__).resolve().parents[1]
    import sqlite3
    dbpath=tmp_path/'legacy.db';env=dict(os.environ,DATABASE_URL='sqlite:///'+str(dbpath),PYTHONDONTWRITEBYTECODE='1')
    with sqlite3.connect(dbpath) as db:
        db.executescript((root/'tests/fixtures/legacy-schema.sql').read_text())
        now=m.utcnow().isoformat(sep=' ')
        db.execute("INSERT INTO users(id,membership,created_at) VALUES ('legacy','member',?)",(now,))
        values={"user_id":"legacy","relationship_type":"relative","subject_name":"合成测试","created_at":now,"updated_at":now}
        for _,name,typ,required,default,_ in db.execute("PRAGMA table_info(user_states)"):
            if required and name not in values:values[name]='[]' if typ=='JSON' else 0 if typ in ('BOOLEAN','FLOAT','INTEGER') else ''
        names=','.join(values);marks=','.join('?' for _ in values)
        db.execute(f"INSERT INTO user_states({names}) VALUES ({marks})",tuple(values.values()))
        db.execute("INSERT INTO diary_notes(id,user_id,content,ts) VALUES ('old-1','legacy',?,?)",('  preserved\n\nfirst  ',now))
        db.execute("INSERT INTO diary_notes(id,user_id,content,ts) VALUES ('old-2','legacy','second',?)",(now,))
        db.commit()
    check='''from memory.db import init_db,SessionLocal
from memory.models import User,UserState,DiaryNote
from sqlalchemy import select
init_db();init_db()
with SessionLocal() as db:
 u=db.get(User,"legacy");assert u.membership=="member" and u.membership_source=="legacy" and u.membership_expires_at is None
 cid=db.get(UserState,"legacy").current_companion_id;assert cid
 rows=list(db.scalars(select(DiaryNote).order_by(DiaryNote.id)));assert len(rows)==2
 assert rows[0].content=="  preserved\\n\\nfirst  "
 assert all(r.companion_id==cid for r in rows)
 assert sum(r.is_current for r in rows)==1
 assert all(r.canonical_diary_id=="old-2" for r in rows)
 print("legacy rows and membership preserved; migration repeated")
'''
    r=subprocess.run([sys.executable,'-c',check],cwd=root,env=env,capture_output=True,text=True);assert r.returncode==0,r.stderr


def test_path_and_body_identity_both_checked(client):
    u,h,s=account(client);v,h2,s2=account(client,'other')
    r=client.post('/api/onboarding/'+v+'/enter-main',headers=h,json={'user_id':u,'request_id':'spoof'})
    assert r.status_code==403


def test_abandoned_operation_recovers_definite_local_failure(client):
    u,h,s=account(client)
    with _RawSessionLocal() as db:
        db.add(Operation(user_id=u,request_id='interrupted',endpoint='/api/diary',fingerprint='f'*64,status='processing',lease_expires_at=m.utcnow()-timedelta(days=1)))
        db.commit()
    r=client.get('/api/operations/interrupted',headers=h);assert r.status_code==200,r.json()
    assert r.json()['status']=='failed' and r.json()['error']['code']=='OPERATION_INTERRUPTED'
    with _RawSessionLocal() as db:assert db.scalar(select(func.count()).select_from(m.DiaryNote))==0


def test_stale_query_and_resume_cannot_downgrade_paid(client,pay_keys,monkeypatch):
    from api.subscriptions import apply_paid
    u,h,s=account(client);o=make_order(u)
    def paid_from_callback(order_id):
        with _RawSessionLocal() as db:
            row=db.get(Order,order_id)
            apply_paid(db,row,{'out_trade_no':row.id,'transaction_id':'callback-'+row.id,'appid':'test-app','mchid':'test-merchant','payer':{'openid':row.user_id},'amount':{'total':row.amount,'currency':'CNY'},'trade_state':'SUCCESS'})
            db.commit()
    def stale_query(self,row):paid_from_callback(row.id);return {'trade_state':'NOTPAY'}
    monkeypatch.setattr(WeChatPay,'query',stale_query)
    r=client.get('/api/subscriptions/orders/by-request/payment',headers=h)
    assert r.status_code==200 and r.json()['status']=='paid',r.json()
    expiry=client.get('/api/auth/me',headers=h).json()['membership_expires_at']
    paid_from_callback(o.id)
    assert client.get('/api/auth/me',headers=h).json()['membership_expires_at']==expiry
    with _RawSessionLocal() as db:
        o2=Order(user_id=u,request_id='second',plan_id='test-plan',snapshot={'name':'Test'},amount=123,duration_days=7,status='unpaid',expires_at=m.utcnow()+timedelta(minutes=30));db.add(o2);db.commit();oid=o2.id
    monkeypatch.setattr(WeChatPay,'query',lambda self,o:{'trade_state':'NOTPAY'})
    def slow_prepay(self,row):paid_from_callback(row.id);return 'prepay-after-callback'
    monkeypatch.setattr(WeChatPay,'prepay',slow_prepay)
    r=client.post('/api/subscriptions/orders/'+oid+'/resume',headers=h,json={'request_id':'resume-second'})
    assert r.status_code==200 and r.json()['status']=='paid',r.json()
    expiry=client.get('/api/auth/me',headers=h).json()['membership_expires_at'];paid_from_callback(oid)
    assert client.get('/api/auth/me',headers=h).json()['membership_expires_at']==expiry
