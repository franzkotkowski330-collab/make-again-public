"""Deny-by-default API boundary, resource authorization and durable receipts."""
import hashlib
import json
import re
import time
import threading
from collections import defaultdict, deque
from uuid import uuid4
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from memory.db import _RawSessionLocal
from memory.scope import current_db
from memory import models as m
from memory.b07_models import Operation, Audit


def fail(status, code, message, **extra):
    raise HTTPException(status, dict(code=code,message=message,retryable=status in (429,503),**extra))


def iso(dt):
    return dt.isoformat()+'Z' if dt else None


def membership(user):
    active = user.membership == 'member' and (user.membership_expires_at is None or user.membership_expires_at>m.utcnow())
    return {'membership':'member' if active else 'free','membership_expires_at':iso(user.membership_expires_at),
            'membership_source':user.membership_source,'entitlement_source':user.membership_source,
            'account_status':user.account_status,'entitlements':{'member':active}}


def authenticate(db, header):
    parts=(header or '').split()
    if len(parts)!=2 or parts[0].lower()!='bearer': fail(401,'UNAUTHENTICATED','请先登录')
    token=parts[1]
    row=db.get(m.AuthSession,token)
    if row is None or (row.expires_at and row.expires_at<=m.utcnow()): fail(401,'SESSION_EXPIRED','登录已失效')
    user=db.get(m.User,row.user_id)
    if not user: fail(401,'SESSION_EXPIRED','登录已失效')
    if user.account_status!='active': fail(403,'ACCOUNT_DISABLED','账号已停用')
    return user

_buckets=defaultdict(deque)
_lock=threading.Lock()
def rate_limit(key, limit):
    now=time.monotonic()
    with _lock:
        q=_buckets[key]
        while q and q[0]<now-60: q.popleft()
        if len(q)>=limit: fail(429,'RATE_LIMITED','操作频繁，请稍后再试')
        q.append(now)
        if len(_buckets)>10000:
            for k in list(_buckets):
                if not _buckets[k] or _buckets[k][-1]<now-60: _buckets.pop(k,None)

PUBLIC={'/api/auth/login','/api/auth/register','/api/auth/wechat','/api/health','/api/payments/wechat/notify'}
ACCOUNT_PREFIXES=('/api/treehole/report','/api/treehole/block','/api/auth/','/api/account','/api/activation/','/api/subscriptions/','/api/operations/','/api/feedback','/api/speech/','/api/admin/','/api/media/')

def op_view(op):
    return {'ok':True,'request_id':op.request_id,'client_request_id':op.request_id,'status':op.status,
            'resource_id':op.resource_id,'companion_id':op.companion_id,'result':op.result,'error':op.error}

class SecureRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handle(request: Request):
            db=_RawSessionLocal(); db.info.update(managed=True,borrowed=True)
            ctx=current_db.set(db); op=None; rid=None; owns_op=False
            try:
                path=request.url.path; write=request.method not in ('GET','HEAD','OPTIONS')
                admin=path.startswith('/api/admin/') or '/treehole/admin/' in path or '/treehole/review/' in path or (path.startswith('/api/feedback') and request.method!='POST')
                public=path in PUBLIC or path.startswith("/api/media/local/")
                remote = path in ("/api/chat","/api/photo/upload","/api/item/upload","/api/speech/recognize","/api/speech/tts","/api/auth/wechat","/api/payments/wechat/notify","/api/relationship/infer","/api/ending/content","/api/daily/theme-guide","/api/treehole/letter","/api/treehole/reply","/api/treehole/admin/reply") or path.startswith("/api/subscriptions/orders")
                user=None if public else authenticate(db,request.headers.get('authorization'))
                if admin and user.role!='admin': fail(403,'ADMIN_REQUIRED','需要管理员权限')
                if write:
                    rate_limit((user.id if user else request.client.host,path), 20 if '/auth/' in path else 60)
                # No body printing/logging. Multipart bytes are bounded before hashing.
                payload={}
                if write and 'multipart/form-data' in request.headers.get('content-type',''):
                    form=await request.form(max_files=1,max_fields=20,max_part_size=24*1024*1024)
                    for k,v in form.items():
                        if hasattr(v,'read'):
                            blob=await v.read(21*1024*1024+1)
                            if len(blob)>21*1024*1024: fail(413,'UPLOAD_TOO_LARGE','文件过大')
                            await v.seek(0)
                            payload[k]={'sha256':hashlib.sha256(blob).hexdigest()}
                        else: payload[k]=v
                elif write:
                    raw=await request.body()
                    if len(raw)>256*1024: fail(413,'BODY_TOO_LARGE','请求过大')
                    if raw:
                        try: payload=json.loads(raw)
                        except ValueError: fail(422,'INVALID_JSON','请求格式错误')
                        if not isinstance(payload,dict): fail(422,'INVALID_JSON','请求必须是对象')
                a=payload.get('request_id'); b=payload.get('client_request_id')
                if a and b and a!=b: fail(422,'REQUEST_ID_MISMATCH','请求标识不一致')
                rid=a or b
                if rid and (not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_.:\-]{1,128}',rid)):
                    fail(422,'INVALID_REQUEST_ID','请求标识格式错误')
                uid=user.id if user else None
                if user and not admin:
                    for requested in (payload.get('user_id'),request.path_params.get('user_id')):
                        if requested is not None and requested!=uid: fail(403,'OWNER_MISMATCH','无法访问此账号数据')
                    if path=='/api/auth/logout' and payload.get('token'):
                        s=db.get(m.AuthSession,payload['token'])
                        if s and s.user_id!=uid: fail(403,'OWNER_MISMATCH','无法撤销其他账号会话')
                # End authentication read transaction before obtaining write lock.
                db.rollback()
                if write and not public:
                    await run_in_threadpool(db.execute,text('BEGIN IMMEDIATE'))
                    # Revalidate after waiting for another transaction (disable/revoke races).
                    if not public:
                        user=authenticate(db,request.headers.get('authorization'))
                        if admin and user.role!='admin': fail(403,'ADMIN_REQUIRED','需要管理员权限')
                if user and write and not public:
                    rid=rid or uuid4().hex
                    fingerprint=hashlib.sha256(json.dumps({'method':request.method,'path':path,'payload':payload},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                    op=db.scalar(select(Operation).where(Operation.user_id==uid,Operation.request_id==rid))
                    if op:
                        if op.fingerprint!=fingerprint: fail(409,'IDEMPOTENCY_MISMATCH','相同请求标识对应不同内容')
                        if op.status=='processing' and (not op.lease_expires_at or op.lease_expires_at<=m.utcnow()):
                            await run_in_threadpool(db.finish)
                            from api.b07 import recover_operation
                            recover_operation(db,op);await run_in_threadpool(db.finish)
                        if op.status=='completed': return JSONResponse(op.result,status_code=op.http_status)
                        if op.status=='failed': return JSONResponse(op.error,status_code=op.http_status)
                        return JSONResponse(op_view(op),status_code=202)
                    if path in ('/api/chat','/api/interview/answer'):
                        busy=db.scalar(select(Operation.id).where(Operation.user_id==uid,Operation.endpoint==path,Operation.status=='processing',Operation.lease_expires_at>m.utcnow()))
                        if busy:fail(409,'CONVERSATION_BUSY','上一条消息仍在处理中，请先查询回执')
                    op=Operation(user_id=uid,request_id=rid,fingerprint=fingerprint,endpoint=path)
                    db.add(op); await run_in_threadpool(db.flush); op_id=op.id; await run_in_threadpool(db.finish); owns_op=True
                    if not remote: await run_in_threadpool(db.execute,text('BEGIN IMMEDIATE'))
                    op=db.get(Operation,op_id)
                    authenticate(db,request.headers.get('authorization'))
                    db.info['request_id']=rid
                db.info['user_id']=uid
                if user and not admin and not path.startswith(ACCOUNT_PREFIXES) and path!='/api/companions':
                    cid=payload.get('companion_id') or request.query_params.get('companion_id')
                    state=db.get(m.UserState,uid)
                    current=state.current_companion_id if state else None
                    # IDs received without user_id still need independent ownership checks.
                    resource=None
                    if path.startswith('/api/interview/') and (payload.get('session_id') or request.path_params.get('session_id')):
                        resource=db.get(m.InterviewSession,payload.get('session_id') or request.path_params.get('session_id'))
                    elif path=='/api/todo/done': resource=db.get(m.TodoItem,payload.get('todo_id'))
                    elif path.startswith("/api/echo/") and payload.get("echo_id"): resource=db.get(m.EchoEntry,payload["echo_id"])
                    elif request.method=='PATCH' and path.startswith('/api/diary/'):
                        resource=db.get(m.DiaryNote,request.path_params.get('diary_id') or request.path_params.get('id'))
                    if (path=='/api/todo/done' or path=='/api/interview/answer' or (request.method=='PATCH' and path.startswith('/api/diary/'))) and resource is None:
                        fail(404,'NOT_FOUND','记录不存在')
                    if resource:
                        if resource.user_id!=uid: fail(404,'NOT_FOUND','记录不存在')
                        if cid and resource.companion_id!=cid: fail(403,'COMPANION_MISMATCH','记录不属于此陪伴')
                        cid=resource.companion_id
                    cid=cid or current
                    if not cid and path not in ("/api/state","/api/relationship","/api/relationship/infer"):
                        fail(409,"COMPANION_REQUIRED","请先读取状态并开始陪伴")
                    if cid:
                        c=db.get(m.Companion,cid)
                        if not c or c.user_id!=uid: fail(404,'COMPANION_NOT_FOUND','陪伴不存在')
                        if write and path!='/api/chat/session/clear':
                            if c.archived_at and path not in ('/api/ending/commit','/api/treehole/reply'): fail(409,'COMPANION_ARCHIVED','此陪伴已归档')
                            if cid!=current: fail(409,'COMPANION_CHANGED','当前陪伴已变化')
                        db.info['companion_id']=cid
                        if op: op.companion_id=cid
                response=await original(request)
                if response.status_code>=400:
                    db.rollback()
                    return response
                result=json.loads(response.body) if 'application/json' in response.headers.get('content-type','') else None
                if isinstance(result,dict) and result.get('ok') is False:
                    fail(422,'OPERATION_REJECTED',result.get('message') or result.get('error') or result.get('reason') or '操作未完成')
                if isinstance(result,dict) and op:
                    # Lock/flush first, then fence a late provider response after lease recovery.
                    await run_in_threadpool(db.flush)
                    db.refresh(op)
                    if op.status!='processing': fail(409,'OPERATION_LEASE_EXPIRED','请求已恢复为中断状态，请查询回执')
                    result.setdefault('ok',True);result.setdefault('request_id',rid);result.setdefault('client_request_id',rid)
                    result.setdefault('status','completed')
                    op.result=result;op.status='completed';op.http_status=response.status_code;op.updated_at=m.utcnow()
                    res=result.get('diary') or result.get('photo') or result.get('item') or result
                    op.resource_id=str(res.get('id') or res.get('note_id') or res.get('order_id') or res.get('redemption_id') or '') or None
                    response=JSONResponse(result,status_code=response.status_code)
                # Provider calls have finished. Acquire a short write lock before the final
                # account/scope recheck and commit; legacy commit() only flushes in this UoW.
                from memory.jobs import flush_intents
                flush_intents(db)
                if user and write:
                    await run_in_threadpool(db.flush)
                    if remote:
                        db.expire_all()
                        fresh=authenticate(db,request.headers.get("authorization"))
                        db.refresh(fresh)
                        if fresh.account_status!="active": fail(403,"ACCOUNT_DISABLED","账号已停用")
                    scope=db.info.get('companion_id')
                    if scope and remote and path!="/api/treehole/reply":
                        db.expire_all()
                        c=db.get(m.Companion,scope); st=db.get(m.UserState,uid)
                        if not c or c.archived_at or not st or st.current_companion_id!=scope:
                            fail(409,'COMPANION_CHANGED','陪伴状态已变化，未保存本次结果')
                if admin:
                    db.add(Audit(actor_id=uid,action=request.method+' '+self.path,target_id=next(iter(request.path_params.values()),None)))
                await run_in_threadpool(db.finish)
                return response
            except Exception as exc:
                db.rollback()
                from api.limits import BodyTooLarge
                if isinstance(exc,BodyTooLarge):
                    status=413;error=dict(code="BODY_TOO_LARGE",message="请求超过大小限制",retryable=False)
                elif isinstance(exc,RequestValidationError):
                    status=422;error=dict(code="VALIDATION_ERROR",message="请求字段不符合接口要求",retryable=False)
                elif isinstance(exc,HTTPException):
                    status=exc.status_code
                    error=exc.detail if isinstance(exc.detail,dict) else dict(code='REQUEST_REJECTED',message=str(exc.detail),retryable=False)
                elif isinstance(exc,(IntegrityError,OperationalError)):
                    status=409 if isinstance(exc,IntegrityError) else 503
                    error=dict(code='CONCURRENT_CONFLICT' if status==409 else 'DATABASE_BUSY',message='操作冲突，请先查询结果',retryable=status==503)
                else:
                    status=503;error=dict(code='SERVICE_UNAVAILABLE',message='服务暂时不可用，请先查询操作结果',retryable=True)
                    # Private content, tokens, SQL parameters and provider bodies are never logged.
                    import logging
                    logging.getLogger(__name__).error('route failure %s %s',self.path,type(exc).__name__)
                error={**error,'request_id':rid}
                if owns_op and op and getattr(op,'id',None):
                    # Do not change an existing receipt on mismatched/replayed requests.
                    await run_in_threadpool(db.execute,text('BEGIN IMMEDIATE'))
                    saved=db.get(Operation,op.id)
                    if saved and saved.status=='processing' and error.get('code')!='IDEMPOTENCY_MISMATCH':
                        saved.status='failed';saved.error=error;saved.http_status=status;saved.updated_at=m.utcnow();await run_in_threadpool(db.finish)
                return JSONResponse({**error,'detail':error},status_code=status)
            finally:
                current_db.reset(ctx);db.info['borrowed']=False;db.close()
        return handle
