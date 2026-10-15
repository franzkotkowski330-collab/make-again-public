import io,wave,threading,time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from sqlalchemy import select,func
from PIL import Image
from conftest import account
from memory.db import _RawSessionLocal
from memory import models as m
from memory.b07_models import Operation,ActivationCode,Job


def test_auth_scope_and_disable(client):
    u,h,s=account(client);v,h2,s2=account(client,'other')
    for url in [f'/api/diary/{u}',f'/api/photo/{u}',f'/api/todo/{u}',f'/api/home/{u}','/api/state']:
        assert client.get(url).status_code==401
    assert client.get(f'/api/diary/{v}',headers=h).status_code==403
    assert client.get(f'/api/diary/{u}?companion_id={s2["current_companion_id"]}',headers=h).status_code==404
    assert client.get('/api/admin/users',headers=h).status_code==403
    with _RawSessionLocal() as db:db.get(m.User,v).role='admin';db.commit()
    assert client.patch(f'/api/admin/users/{u}',headers=h2,json={'account_status':'disabled','request_id':'disable'}).status_code==200
    assert client.get('/api/state',headers=h).status_code==401
    assert client.post('/api/auth/login',json={'username':'tester','password':'secure-test-password'}).status_code==403
    with _RawSessionLocal() as db:assert db.scalar(select(func.count()).select_from(m.AuthSession).where(m.AuthSession.user_id==u))==0


def test_diary_exact_text_replay_conflicts(client):
    u,h,s=account(client)
    body={'content':'  one\n\ntwo  ','request_id':'create-diary','companion_id':s['current_companion_id']}
    a=client.post('/api/diary',headers=h,json=body);assert a.status_code==200,a.json();d=a.json()['diary']
    assert d['content']==body['content']
    assert client.post('/api/diary',headers=h,json=body).json()==a.json()
    assert client.post('/api/diary',headers=h,json={**body,'content':'changed'}).status_code==409
    assert client.post('/api/diary',headers=h,json={**body,'request_id':'different'}).status_code==409
    patch={'content':'new\n  exact ','companion_id':d['companion_id'],'entry_date':d['entry_date'],'expected_revision':1,'request_id':'edit'}
    b=client.patch('/api/diary/'+d['id'],headers=h,json=patch);assert b.status_code==200,b.json()
    assert b.json()['diary']['revision']==2
    assert client.patch('/api/diary/'+d['id'],headers=h,json={**patch,'request_id':'stale'}).json()['code']=='REVISION_CONFLICT'
    op=client.get('/api/operations/edit',headers=h).json();assert op['result']==b.json()
    assert client.get('/api/diary/'+u,headers=h).json()['notes'][0]['content']==patch['content']


def test_validation_receipt_and_alias_mismatch(client):
    u,h,s=account(client)
    r=client.post('/api/diary',headers=h,json={'request_id':'invalid'});assert r.status_code==422,r.json()
    assert client.get('/api/operations/invalid',headers=h).json()['status']=='failed'
    assert client.post('/api/diary',headers=h,json={'content':'x','request_id':'a','client_request_id':'b'}).status_code==422


def test_parallel_diary_and_atomic_failure(client,monkeypatch):
    u,h,s=account(client)
    def create(i):return client.post('/api/diary',headers=h,json={'content':'x','request_id':'c'+str(i)}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(create,range(4)))
    assert sorted(out)==[200,409,409,409],out
    from memory import store
    original=store.add_memory
    def failing(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('after legacy commit')
    monkeypatch.setattr(store,'add_memory',failing)
    v,h2,s2=account(client,'atomic')
    r=client.post('/api/diary',headers=h2,json={'content':'must rollback','request_id':'atomic'})
    assert r.status_code==503
    with _RawSessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.DiaryNote).where(m.DiaryNote.user_id==v))==0
        assert db.scalar(select(func.count()).select_from(m.MemoryEntry).where(m.MemoryEntry.user_id==v))==0
        assert db.scalar(select(Operation).where(Operation.user_id==v,Operation.request_id=='atomic')).status=='failed'


def test_slow_chat_does_not_block_other_write(client,monkeypatch):
    u,h,s=account(client);v,h2,s2=account(client,'writer')
    from gateway.client import LLMClient
    original=LLMClient.chat;started=threading.Event();release=threading.Event()
    def slow(self,*args,**kwargs):
        started.set();assert release.wait(5);return original(self,*args,**kwargs)
    monkeypatch.setattr(LLMClient,'chat',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(client.post,'/api/chat',headers=h,json={'message':'今天想到一些回忆','session_id':s['chat_session_id'],'request_id':'chat'})
        assert started.wait(3)
        before=time.monotonic();r=client.post('/api/diary',headers=h2,json={'content':'another user','request_id':'other'})
        elapsed=time.monotonic()-before;release.set()
        assert r.status_code==200,r.json();assert elapsed<2,elapsed
        chat=future.result();assert chat.status_code==200,chat.json()
    assert client.get('/api/state',headers=h).json()['chat_session_id']==s['chat_session_id']
    assert client.post('/api/chat',headers=h,json={'message':'今天想到一些回忆','session_id':s['chat_session_id'],'request_id':'chat'}).json()==chat.json()
    with _RawSessionLocal() as db:assert db.scalar(select(func.count()).select_from(m.ChatMessage).where(m.ChatMessage.user_id==u))==2
    from memory.session_context import get_turns
    assert len(get_turns(u,s['chat_session_id']))==2


def test_activation_concurrency_and_expiry(client):
    admin,ah,ss=account(client,'admin');a,h,s=account(client,'a1');b,h2,s2=account(client,'b1')
    with _RawSessionLocal() as db:db.get(m.User,admin).role='admin';db.commit()
    payload={'name':'isolated','count':2,'duration_days':30,'expires_at':(m.utcnow()+timedelta(days=1)).isoformat()+'Z','request_id':'batch'}
    r=client.post('/api/admin/activation/batches',headers=ah,json=payload);assert r.status_code==200,r.json();code=r.json()['codes'][0]['code']
    def redeem(headers):return client.post('/api/activation/redeem',headers=headers,json={'code':code,'request_id':'redeem'})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(redeem,[h,h2]))
    assert sorted(x.status_code for x in results)==[200,409]
    winner=h if results[0].status_code==200 else h2
    assert redeem(winner).status_code==200
    assert client.get('/api/auth/me',headers=winner).json()['membership']=='member'
    assert client.get('/api/auth/me',headers=winner).json()['membership_expires_at']
    cid=r.json()['codes'][1]['id']
    assert client.patch('/api/admin/activation/codes/'+cid,headers=ah,json={'disabled':True}).status_code==200
    assert client.post('/api/activation/redeem',headers=h,json={'code':r.json()['codes'][1]['code'],'request_id':'disabled'}).json()['code']=='CODE_DISABLED'


def test_archive_and_new_scope(client):
    u,h,s=account(client)
    r=client.post('/api/relationship',headers=h,json={'relationship_type':'relative','subject_name':'奶奶','request_id':'rel'});assert r.status_code==200,r.json();s=r.json()
    assert client.post('/api/todo',headers=h,json={'content':'old task','request_id':'todo'}).status_code==200
    body={'ritual':'skipped','companion_id':s['current_companion_id'],'expected_revision':s['companion_revision'],'request_id':'archive'}
    r=client.post('/api/ending/commit',headers=h,json=body);assert r.status_code==200,r.json()
    assert client.post('/api/diary',headers=h,json={'content':'archived','request_id':'bad'}).status_code==409
    new=client.post('/api/companions',headers=h,json={'relationship_type':'pet','request_id':'new'});assert new.status_code==200,new.json()
    assert client.get('/api/todo/'+u,headers=h).json()['queue']==[]
    assert len(client.get('/api/todo/'+u+'?companion_id='+s['current_companion_id'],headers=h).json()['queue'])==1
    assert client.post('/api/ending/commit',headers=h,json=body).json()==r.json()
    assert client.post('/api/companions',headers=h,json={'request_id':'other-new'}).status_code==409


def test_photo_format_receipt_and_private_url(client):
    u,h,s=account(client);out=io.BytesIO();Image.new('RGB',(10,10)).save(out,'PNG');data=out.getvalue()
    fields={'user_id':u,'companion_id':s['current_companion_id'],'client_request_id':'photo','title':'测试'}
    r=client.post('/api/photo/upload',headers=h,data=fields,files={'file':('a.jpg',data,'image/jpeg')});assert r.status_code==200,r.json()
    assert r.json()['photo']['client_request_id']=='photo'
    assert client.post('/api/photo/upload',headers=h,data=fields,files={'file':('a.jpg',data,'image/jpeg')}).json()==r.json()
    row=client.get('/api/photo/'+u,headers=h).json()['photos'][0]
    assert row['id']==r.json()['photo']['id']
    assert client.get(row['photo_url']).status_code==200
    assert client.get(row['photo_url'].replace('signature=','signature=x')).status_code==403
    assert client.post('/api/photo/upload',headers=h,data={**fields,'client_request_id':'bad'},files={'file':('x.png',b'fake','image/png')}).status_code==415


def test_audio_wav_and_invalid_mp3(client):
    u,h,s=account(client);b=io.BytesIO()
    with wave.open(b,'wb') as w:w.setparams((1,2,16000,1600,'NONE',''));w.writeframes(b'\0\0'*1600)
    assert client.post('/api/speech/recognize',headers=h,files={'file':('voice.wav',b.getvalue(),'audio/wav')}).status_code==200
    assert client.post('/api/speech/recognize',headers=h,files={'file':('voice.mp3',b'ID3bogus','audio/mpeg')}).status_code in (415,503)


def test_logout(client):
    u,h,s=account(client)
    assert client.post('/api/auth/logout',headers=h,json={'token':h['Authorization'].split()[1]}).status_code==200
    assert client.get('/api/state',headers=h).status_code==401


def test_unconfigured_commerce_and_production(client):
    u,h,s=account(client)
    r=client.get('/api/subscriptions/config?platform=android',headers=h);assert r.status_code==200,r.json()
    assert r.json()['plans']==[] and not r.json()['membership_available']
    from config import Settings,validate_runtime,get_settings
    import pytest
    s=get_settings();old=s.environment;s.environment='production'
    try:
        with pytest.raises(RuntimeError):validate_runtime()
    finally:s.environment=old


def test_interview_restore_completion_report_retry_and_worker(client,monkeypatch):
    u,h,s=account(client)
    first=client.post('/api/interview/start',headers=h,json={'loss_type':'loved_one','request_id':'start'});assert first.status_code==200,first.json()
    again=client.post('/api/interview/start',headers=h,json={'loss_type':'loved_one','request_id':'resume'})
    assert again.json()['session_id']==first.json()['session_id']
    session_id=first.json()['session_id']
    v,h2,s2=account(client,'other')
    assert client.get('/api/interview/'+session_id,headers=h2).status_code==404
    with _RawSessionLocal() as db:
        session=db.get(m.InterviewSession,session_id);session.status='completed';session.state={**session.state,'done':True,'report_failed':True,'generating':False};db.commit()
    restored=client.post('/api/interview/start',headers=h,json={'loss_type':'loved_one','request_id':'again'})
    assert restored.json()['session_id']==session_id and restored.json()['done']
    assert client.get('/api/state',headers=h).json()['destination']=='004'
    r=client.post('/api/initial-report/'+u+'/retry',headers=h,json={'request_id':'retry-report'});assert r.status_code==200,r.json()
    assert r.json()['status']=='generating'
    from agent import async_report
    def synthetic_report(user_id,session_id,loss_type,history):
        from memory.db import SessionLocal
        db=SessionLocal();session=db.get(m.InterviewSession,session_id);session.state={**session.state,'report_ready':True,'generating':False}
        db.add(m.Report(user_id=user_id,kind='initial',content={'test':'synthetic'}));db.commit()
    monkeypatch.setattr(async_report,'_do_report',synthetic_report)
    from admin_cli import run_one
    assert run_one()
    state=client.get('/api/interview/'+session_id,headers=h).json();assert state['report_ready']
    with _RawSessionLocal() as db:
        reports=list(db.scalars(select(m.Report).where(m.Report.user_id==u)));assert len(reports)==1 and reports[0].companion_id==s['current_companion_id']


def test_late_chat_cannot_commit_after_lease_recovery(client,monkeypatch):
    u,h,s=account(client)
    from gateway.client import LLMClient
    original=LLMClient.chat;started=threading.Event();release=threading.Event()
    def slow(self,*args,**kwargs):started.set();assert release.wait(5);return original(self,*args,**kwargs)
    monkeypatch.setattr(LLMClient,'chat',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(client.post,'/api/chat',headers=h,json={'message':'今天有些想念','request_id':'late'})
        assert started.wait(2)
        with _RawSessionLocal() as db:
            op=db.scalar(select(Operation).where(Operation.user_id==u,Operation.request_id=='late'));op.lease_expires_at=m.utcnow()-timedelta(seconds=1);db.commit()
        recovered=client.get('/api/operations/late',headers=h);assert recovered.json()['status']=='failed';release.set()
        response=future.result();assert response.status_code==409,response.json()
    with _RawSessionLocal() as db:assert db.scalar(select(func.count()).select_from(m.ChatMessage).where(m.ChatMessage.user_id==u))==0


def test_echo_resource_ownership_and_state_capability(client):
    u,h,s=account(client);v,h2,s2=account(client,'other')
    assert s['capabilities']['diary_update'] is True
    with _RawSessionLocal() as db:
        echo=m.EchoEntry(user_id=v,companion_id=s2['current_companion_id'],content='synthetic');db.add(echo);db.commit();eid=echo.id
    r=client.post('/api/echo/popup/'+u+'/seen',headers=h,json={'echo_id':eid,'request_id':'wrong-echo'})
    assert r.status_code==404,r.json()


def test_item_original_fallback_is_truthful_and_locatable(client):
    u,h,s=account(client);out=io.BytesIO();Image.new('RGB',(10,10)).save(out,'JPEG')
    r=client.post('/api/item/upload',headers=h,data={'user_id':u,'companion_id':s['current_companion_id'],'client_request_id':'item','intent':'keep'},files={'file':('a.jpg',out.getvalue(),'image/jpeg')})
    assert r.status_code==200,r.json();row=r.json()['item']
    assert row['status']=='original_fallback' and row['original_url']
    assert client.get('/api/item/'+u,headers=h).json()['items'][0]['id']==row['id']
    assert client.get(row['cutout_url']).content.startswith(b'\x89PNG')


def test_actual_mp3_decode_and_upload_limit(client):
    import shutil,subprocess
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:
        import pytest;pytest.skip('FFmpeg required for real MP3 decode evidence')
    u,h,s=account(client)
    audio=subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','sine=frequency=400:duration=0.15','-f','mp3','pipe:1'],capture_output=True,check=True).stdout
    r=client.post('/api/speech/recognize',headers=h,files={'file':('wrong.bin',audio,'application/octet-stream')});assert r.status_code==200,r.json()
    from config import get_settings
    settings=get_settings();old=settings.item_max_upload_mb;settings.item_max_upload_mb=0
    try:assert client.post('/api/photo/upload',headers=h,data={'user_id':u},files={'file':('x.png',b'x','image/png')}).status_code==413
    finally:settings.item_max_upload_mb=old


def test_continuous_worker_process_consumes_and_stops(client):
    import subprocess,sys,os,signal
    from pathlib import Path
    u,h,s=account(client)
    with _RawSessionLocal() as db:
        j=Job(kind='memory',user_id=u,companion_id=s['current_companion_id'],dedupe_key='worker-process',payload={'message':'合成测试内容','type':'chat','extractor':None});db.add(j);db.commit();jid=j.id
    process=subprocess.Popen([sys.executable,'admin_cli.py','worker','--forever','--poll-seconds','0.2'],cwd=Path(__file__).resolve().parents[1],env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            with _RawSessionLocal() as db:state=db.get(Job,jid).status
            if state=='completed':break
            if process.poll() is not None:break
            time.sleep(.1)
        assert state=='completed',state
        assert process.poll() is None
    finally:
        process.terminate()
        out,err=process.communicate(timeout=5)
    assert process.returncode==0,(out,err)


def test_worker_restart_recovery_preserves_unknown_send(client):
    from admin_cli import recover_jobs
    u,h,s=account(client)
    with _RawSessionLocal() as db:
        for kind in ('memory','message'):
            db.add(Job(kind=kind,user_id=u,status='running',dedupe_key='crashed-'+kind,updated_at=m.utcnow()-timedelta(hours=1)))
        db.commit()
    recover_jobs()
    with _RawSessionLocal() as db:
        states={j.kind:j.status for j in db.scalars(select(Job).where(Job.dedupe_key.like('crashed-%')))}
    assert states=={'memory':'retry','message':'unknown'}


def test_invalid_report_output_retries_without_fake_ready(client,monkeypatch):
    u,h,s=account(client)
    start=client.post('/api/interview/start',headers=h,json={'request_id':'start'});sid=start.json()['session_id']
    with _RawSessionLocal() as db:
        session=db.get(m.InterviewSession,sid);session.status='completed';session.state={**session.state,'done':True,'generating':True,'report_ready':False}
        j=Job(kind='report',user_id=u,companion_id=s['current_companion_id'],dedupe_key='report:'+sid,payload={'session_id':sid,'loss_type':'breakup','history':[]});db.add(j);db.commit();jid=j.id
    from gateway.client import LLMClient
    monkeypatch.setattr(LLMClient,'chat_json',lambda *a,**kw:({'title':'','summary':123},{}))
    from admin_cli import run_one
    for attempt in range(3):
        assert run_one()
        with _RawSessionLocal() as db:
            row=db.get(Job,jid);assert row.error_code=='ReportGenerationError'
            assert db.scalar(select(func.count()).select_from(m.Report))==0
            assert not db.get(m.InterviewSession,sid).state['report_ready']
            if attempt<2:row.due_at=m.utcnow()-timedelta(seconds=1);db.commit()
    r=client.get('/api/initial-report/'+u,headers=h);assert r.json()['status']=='failed',r.json()
