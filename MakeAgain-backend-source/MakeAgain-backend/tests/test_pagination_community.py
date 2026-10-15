from datetime import timedelta
from urllib.parse import urlencode
import pytest
from sqlalchemy import select,func
from conftest import account
from memory.db import _RawSessionLocal
from memory import models as m
from memory.b07_models import CommunityReport,CommunityBlock


def seed_collections(user,companion,count=7):
    ts=m.utcnow()-timedelta(minutes=1)
    with _RawSessionLocal() as db:
        for i in range(count):
            key=f'{i:03d}-{companion}'
            db.add(m.PhotoMemory(id='p'+key,user_id=user,companion_id=companion,title='test',description='',photo_key='test.png',ts=ts))
            db.add(m.ItemMemory(id='i'+key,user_id=user,companion_id=companion,item_name='test',intent='keep',description='',original_key='original.png',cutout_key='cutout.png',ts=ts))
            db.add(m.DiaryNote(id='d'+key,user_id=user,companion_id=companion,content='test',entry_date=f'2020-01-{i+1:02d}',ts=ts,updated_at=ts))
            db.add(m.WeeklyReport(id='w'+key,user_id=user,companion_id=companion,week_key=f'2020-W{i+1:02d}',content={},created_at=ts))
            db.add(m.Report(id='r'+key,user_id=user,companion_id=companion,kind='initial',content={'title':'test','summary':'test'},created_at=ts))
            db.add(m.TreeholeLetter(id='l'+key,author_user_id=user,companion_id=companion,content='test',status='published',created_at=ts))
        db.commit()

@pytest.mark.parametrize('endpoint,field',[('photo','photos'),('item','items'),('diary','notes'),('weekly-report','reports'),('reports','reports'),('treehole/letters','letters')])
def test_keyset_ties_full_legacy_and_new_insert_exclusion(client,endpoint,field):
    user,headers,state=account(client);cid=state['current_companion_id'];seed_collections(user,cid)
    url=f'/api/{endpoint}/{user}'
    legacy=client.get(url,headers=headers).json();assert len(legacy[field])==7 and 'next_cursor' not in legacy
    result=client.get(url,headers=headers,params={'limit':2}).json()
    assert len(result[field])==2 and result['has_more'] is True and result['next_cursor']
    ids=[r['id'] for r in result[field]]
    # Insert after the first page (immutable creation times are assigned by the server).
    if endpoint=='diary':
        r=client.post('/api/diary',headers=headers,json={'content':'new after page','request_id':'new-after-page'});assert r.status_code==200,r.json()
    cursor=result['next_cursor'];seen_cursors=set()
    while cursor:
        assert cursor not in seen_cursors;seen_cursors.add(cursor)
        response=client.get(url,headers=headers,params={'limit':2,'cursor':cursor});assert response.status_code==200,response.json()
        result=response.json();ids.extend(r['id'] for r in result[field]);cursor=result['next_cursor']
    assert len(ids)==len(set(ids))==7
    assert ids==sorted(ids,reverse=True)
    assert result['has_more'] is False


def test_cursor_account_collection_scope_archive_and_tampering(client):
    user,h,s=account(client);cid=s['current_companion_id'];seed_collections(user,cid)
    first=client.get('/api/diary/'+user,headers=h,params={'limit':2}).json();cursor=first['next_cursor']
    other,h2,s2=account(client,'other');seed_collections(other,s2['current_companion_id'])
    assert client.get('/api/diary/'+other,headers=h2,params={'cursor':cursor}).json()['code']=='CURSOR_SCOPE_MISMATCH'
    assert client.get('/api/photo/'+user,headers=h,params={'cursor':cursor}).json()['code']=='CURSOR_SCOPE_MISMATCH'
    assert client.get('/api/diary/'+user,headers=h,params={'cursor':cursor+'x'}).status_code==422
    for limit in (0,-1,101):assert client.get('/api/diary/'+user,headers=h,params={'limit':limit}).status_code==422
    # Existing archive cursor remains valid only with that explicit archive scope.
    with _RawSessionLocal() as db:
        old=db.get(m.Companion,cid);old.archived_at=m.utcnow()
        new=m.Companion(user_id=user,relationship_type='pet');db.add(new);db.flush();db.get(m.UserState,user).current_companion_id=new.id;db.commit()
    assert client.get('/api/diary/'+user,headers=h,params={'cursor':cursor}).status_code==409
    archive=client.get('/api/diary/'+user,headers=h,params={'cursor':cursor,'companion_id':cid});assert archive.status_code==200,archive.json()
    assert all(r['companion_id']==cid for r in archive.json()['notes'])


def test_todo_bucket_pagination_order_and_mutation_requires_refresh(client):
    user,h,s=account(client);cid=s['current_companion_id'];ts=m.utcnow()-timedelta(minutes=1)
    with _RawSessionLocal() as db:
        for i in range(12):db.add(m.TodoItem(id=f'task-{i:02d}',user_id=user,companion_id=cid,content='test',done=i>=6,ts=ts,done_at=ts if i>=6 else None))
        db.commit()
    legacy=client.get('/api/todo/'+user,headers=h).json();assert len(legacy['queue'])==6 and len(legacy['history'])==4
    for bucket,reverse in [('queue',False),('history',True)]:
        cursor=None;ids=[]
        while True:
            params={'limit':2,'bucket':bucket}
            if cursor:params['cursor']=cursor
            r=client.get('/api/todo/'+user,headers=h,params=params);assert r.status_code==200,r.json()
            result=r.json();ids.extend(x['id'] for x in result[bucket]);cursor=result['next_cursor']
            assert result['history' if bucket=='queue' else 'queue']==[]
            if not cursor:break
        assert len(ids)==len(set(ids))==6 and ids==sorted(ids,reverse=reverse)
    first=client.get('/api/todo/'+user,headers=h,params={'limit':2,'bucket':'queue'}).json()
    r=client.post('/api/todo/done',headers=h,json={'todo_id':'task-04','request_id':'done'});assert r.status_code==200,r.json()
    stale=client.get('/api/todo/'+user,headers=h,params={'cursor':first['next_cursor'],'bucket':'queue'})
    assert stale.status_code==409 and stale.json()['code']=='CURSOR_STALE'


def community_seed(client):
    receiver,h,s=account(client,'receiver');sender,sh,ss=account(client,'sender')
    with _RawSessionLocal() as db:
        original=m.TreeholeLetter(author_user_id=receiver,companion_id=s['current_companion_id'],content='synthetic original',status='published');db.add(original);db.flush()
        reply=m.TreeholeReply(letter_id=original.id,author_user_id=sender,content='synthetic delivered reply',status='delivered',reviewed_at=m.utcnow());db.add(reply)
        peer=m.TreeholeLetter(author_user_id=sender,companion_id=ss['current_companion_id'],content='synthetic peer',status='published');db.add(peer);db.commit()
        return receiver,h,s,sender,sh,ss,original.id,reply.id,peer.id


def test_report_received_reply_anonymously_and_block_suppresses_delivery(client):
    user,h,s,peer,ph,ps,letter,reply,published=community_seed(client)
    letters=client.get('/api/treehole/letters/'+user,headers=h).json()
    assert letters['letters'][0]['replies'][0]['reply_id']==reply
    body=str(letters);assert peer not in body and 'author_user_id' not in body
    report=client.post('/api/treehole/report',headers=h,json={'reply_id':reply,'reason':'不想收到此类回信','block_author':True,'request_id':'report-reply'})
    assert report.status_code==200,report.json();assert report.json()['blocked'] is True
    assert peer not in str(report.json()) and 'author_user_id' not in str(report.json())
    assert client.post('/api/treehole/report',headers=h,json={'reply_id':reply,'reason':'不想收到此类回信','block_author':True,'request_id':'report-reply'}).json()==report.json()
    assert client.get('/api/treehole/letters/'+user,headers=h).json()['letters'][0]['replies']==[]
    assert all(p['kind']!='reply_received' for p in client.get('/api/treehole/popup/'+user,headers=h).json()['popups'])
    with _RawSessionLocal() as db:
        row=db.scalar(select(CommunityReport));assert row.reply_id==reply and row.letter_id==letter
        assert db.scalar(select(func.count()).select_from(CommunityReport))==1
        from memory import store
        db.info['user_id']=user
        assert published not in [r.id for r in store.list_published_letters(db)]


def test_report_and_block_support_archive_and_reject_unrelated_targets(client):
    user,h,s,peer,ph,ps,letter,reply,published=community_seed(client)
    with _RawSessionLocal() as db:db.get(m.Companion,s['current_companion_id']).archived_at=m.utcnow();db.commit()
    r=client.post('/api/treehole/block',headers=h,json={'reply_id':reply,'request_id':'block'});assert r.status_code==200,r.json()
    assert client.post('/api/treehole/report',headers=ph,json={'reply_id':reply,'reason':'other account','request_id':'wrong'}).status_code==404
    assert client.post('/api/treehole/report',headers=h,json={'letter_id':letter,'reason':'own letter','request_id':'own'}).status_code==409
    assert client.post('/api/treehole/report',headers=h,json={'letter_id':published,'reply_id':reply,'reason':'both','request_id':'both'}).status_code==422
    assert client.post('/api/treehole/report',headers=h,json={'letter_id':published,'reason':'   ','request_id':'empty'}).status_code==422
    with _RawSessionLocal() as db:assert db.scalar(select(func.count()).select_from(CommunityBlock))==1


def test_real_completed_turns_unlock_writing_with_stable_session(client):
    user,h,s=account(client)
    sid=s['chat_session_id'];url='/api/treehole/popup/'+user
    assert not any(p['kind']=='write' for p in client.get(url,headers=h).json()['popups'])
    for i in range(2):
        r=client.post('/api/chat',headers=h,json={'message':'合成对话，想聊聊今天的感受','request_id':f'chat-{i}','session_id':sid});assert r.status_code==200,r.json()
    popups=client.get(url,headers=h).json()['popups']
    assert any(p['kind']=='write' for p in popups),popups
    assert client.get('/api/state',headers=h).json()['chat_session_id']==sid
    with _RawSessionLocal() as db:
        db.info.update(user_id=user,companion_id=s['current_companion_id'])
        from agent.treehole import write_eligibility
        elig=write_eligibility(db,user);assert elig['completed_turns']==2 and elig['chat_sessions']==1 and elig['eligible']
    r=client.post('/api/treehole/letter',headers=h,json={'content':'合成信件，我想记录此刻的心情。','request_id':'letter'});assert r.status_code==200,r.json()
    assert r.json()['status']=='pending_review'
    letters=client.get('/api/treehole/letters/'+user,headers=h).json()['letters'];assert len(letters)==1


def test_page_empty_and_report_filter_binding(client):
    user,h,s=account(client)
    for endpoint,array in [('diary','notes'),('photo','photos'),('item','items'),('weekly-report','reports'),('reports','reports'),('treehole/letters','letters')]:
        r=client.get('/api/'+endpoint+'/'+user,headers=h,params={'limit':20});assert r.status_code==200,r.json()
        assert r.json()[array]==[] and r.json()['next_cursor'] is None and r.json()['has_more'] is False
    seed_collections(user,s['current_companion_id'])
    first=client.get('/api/reports/'+user,headers=h,params={'kind':'initial','limit':2}).json()
    wrong=client.get('/api/reports/'+user,headers=h,params={'kind':'tracking','cursor':first['next_cursor']})
    assert wrong.status_code==409 and wrong.json()['code']=='CURSOR_SCOPE_MISMATCH'


def test_reply_read_receipt_and_pending_visibility(client):
    user,h,s,peer,ph,ps,letter,reply,published=community_seed(client)
    with _RawSessionLocal() as db:
        pending=m.TreeholeReply(letter_id=letter,author_user_id=peer,content='pending synthetic',status='pending_review');db.add(pending);db.commit();pending_id=pending.id
    visible=client.get('/api/treehole/letters/'+user,headers=h).json()['letters'][0]['replies']
    assert [r['reply_id'] for r in visible]==[reply]
    assert client.post('/api/treehole/report',headers=h,json={'reply_id':pending_id,'reason':'pending','request_id':'pending-report'}).status_code==404
    seen=client.post('/api/treehole/popup/'+user+'/seen',headers=h,json={'kind':'reply_received','ref_id':reply,'request_id':'seen'})
    assert seen.status_code==200,seen.json()
    assert all(p['kind']!='reply_received' for p in client.get('/api/treehole/popup/'+user,headers=h).json()['popups'])
    other=client.post('/api/treehole/popup/'+peer+'/seen',headers=ph,json={'kind':'reply_received','ref_id':reply,'request_id':'wrong-seen'})
    assert other.status_code==404


def test_environment_example_is_explicit_and_has_no_fake_commerce():
    from dotenv import dotenv_values
    from pathlib import Path
    values=dotenv_values(Path(__file__).resolve().parents[1]/'.env.example')
    assert values['ENVIRONMENT']=='development'
    assert 'b07-development' in values['DATABASE_URL']
    assert all(values[key]=='true' for key in ('MOCK_LLM','MOCK_WECHAT','MOCK_SPEECH','MOCK_VISION','MOCK_STORAGE'))
    assert values['MEDIA_SIGNING_KEY']=='' and values['WECHAT_PAY_MCHID']=='' and values['WECHAT_SUBSCRIBE_TEMPLATES']=='[]'


def test_candidate01_community_schema_upgrade_preserves_report():
    from memory.db import engine,init_db
    from sqlalchemy import text
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE community_reports'))
        connection.execute(text('CREATE TABLE community_reports (id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64) NOT NULL, letter_id VARCHAR(64) NOT NULL, reason VARCHAR(256) NOT NULL, status VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL)'))
        connection.execute(text("INSERT INTO community_reports VALUES ('old-report','synthetic-user','synthetic-letter','synthetic-reason','pending','2026-01-01 00:00:00')"))
    init_db();init_db()
    with _RawSessionLocal() as db:
        row=db.get(CommunityReport,'old-report')
        assert row.reason=='synthetic-reason' and row.letter_id=='synthetic-letter' and row.reply_id is None
