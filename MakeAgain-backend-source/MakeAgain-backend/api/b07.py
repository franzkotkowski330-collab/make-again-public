"""Account administration, activation codes and revisioned diary operations."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from memory.db import get_session
from memory import models as m, store
from memory.b07_models import Operation, Audit, ActivationBatch, ActivationCode
from api.security import SecureRoute, fail, iso, membership, op_view
from auth import get_current_user
router=APIRouter(prefix='/api',route_class=SecureRoute)


def uid(db): return db.info['user_id']
def cid(db):
    c=store.get_current_companion(db,uid(db))
    if not c: fail(409,'COMPANION_REQUIRED','请先开始陪伴')
    if c.archived_at: fail(409,'COMPANION_ARCHIVED','此陪伴已归档')
    return db.info.get('companion_id') or c.id

@router.get('/operations/{request_id}')
def operation_get(request_id:str,db=Depends(get_session)):
    op=db.scalar(select(Operation).where(Operation.user_id==uid(db),Operation.request_id==request_id))
    if not op: fail(404,'OPERATION_NOT_FOUND','未找到该请求')
    return op_view(recover_operation(db,op))

@router.get('/activation/receipts/{request_id}')
def activation_receipt(request_id:str,db=Depends(get_session)):
    return operation_get(request_id,db)

@router.post('/auth/revoke-sessions')
def revoke_own(db=Depends(get_session)):
    db.execute(delete(m.AuthSession).where(m.AuthSession.user_id==uid(db)))
    return {'ok':True}

@router.get('/admin/users')
def users(q:str='',offset:int=0,limit:int=50,db=Depends(get_session)):
    if offset<0 or not 1<=limit<=100: fail(422,'INVALID_PAGE','分页参数无效')
    query=select(m.User)
    if q: query=query.where(m.User.id.contains(q)|m.User.nickname.contains(q))
    rows=db.scalars(query.order_by(m.User.created_at.desc(),m.User.id).offset(offset).limit(limit+1)).all()
    return {'users':[{'id':u.id,'nickname':u.nickname,'role':u.role,**membership(u)} for u in rows[:limit]],'has_more':len(rows)>limit,'next_offset':offset+limit if len(rows)>limit else None}

@router.get('/admin/users/{user_id}')
def user_get(user_id:str,db=Depends(get_session)):
    u=db.get(m.User,user_id)
    if not u: fail(404,'NOT_FOUND','账号不存在')
    return {'id':u.id,'nickname':u.nickname,'role':u.role,'created_at':iso(u.created_at),**membership(u)}

class UserPatch(BaseModel):
    account_status: Literal['active','disabled']|None=None
    nickname:str|None=Field(None,min_length=1,max_length=24)
    request_id:str|None=None

@router.patch('/admin/users/{user_id}')
def user_patch(user_id:str,req:UserPatch,db=Depends(get_session)):
    u=db.get(m.User,user_id)
    if not u: fail(404,'NOT_FOUND','账号不存在')
    if u.id==uid(db) and req.account_status=='disabled': fail(409,'SELF_DISABLE','不能停用当前管理员')
    if req.nickname is not None:u.nickname=req.nickname
    if req.account_status:
        u.account_status=req.account_status
        if u.account_status=='disabled':db.execute(delete(m.AuthSession).where(m.AuthSession.user_id==u.id))
    return user_get(user_id,db)

@router.post('/admin/users/{user_id}/revoke-sessions')
def revoke(user_id:str,db=Depends(get_session)):
    if not db.get(m.User,user_id): fail(404,'NOT_FOUND','账号不存在')
    n=db.execute(delete(m.AuthSession).where(m.AuthSession.user_id==user_id)).rowcount
    return {'ok':True,'revoked':n}

@router.get('/admin/audit')
def audit(limit:int=50,offset:int=0,db=Depends(get_session)):
    if offset<0 or not 1<=limit<=100: fail(422,'INVALID_PAGE','分页参数无效')
    rows=db.scalars(select(Audit).order_by(Audit.created_at.desc(),Audit.id).offset(offset).limit(limit))
    return {'audit':[{'id':a.id,'actor_id':a.actor_id,'action':a.action,'target_id':a.target_id,'created_at':iso(a.created_at)} for a in rows]}

class BatchReq(BaseModel):
    name:str=Field(min_length=1,max_length=128)
    count:int=Field(ge=1,le=500)
    duration_days:int=Field(ge=1,le=3650)
    expires_at:datetime
    request_id:str|None=None

@router.post('/admin/activation/batches')
def batch_create(req:BatchReq,db=Depends(get_session)):
    if not req.expires_at.tzinfo: fail(422,'TIMEZONE_REQUIRED','兑换截止时间需包含时区')
    expires=req.expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    if expires<=m.utcnow(): fail(422,'INVALID_EXPIRY','兑换截止时间需晚于现在')
    batch=ActivationBatch(name=req.name,duration_days=req.duration_days,expires_at=expires)
    db.add(batch);db.flush();codes=[]
    for _ in range(req.count):
        code=secrets.token_hex(16).upper()
        row=ActivationCode(batch_id=batch.id,digest=hashlib.sha256(code.encode()).hexdigest(),suffix=code[-6:])
        db.add(row);db.flush();codes.append({'id':row.id,'code':code})
    return {'ok':True,'batch_id':batch.id,'duration_days':batch.duration_days,'expires_at':iso(expires),'codes':codes}

@router.get('/admin/activation/batches')
def batches(offset:int=0,limit:int=50,db=Depends(get_session)):
    if offset<0 or not 1<=limit<=100: fail(422,'INVALID_PAGE','分页参数无效')
    return {'batches':[{'id':b.id,'name':b.name,'duration_days':b.duration_days,'expires_at':iso(b.expires_at),'disabled':b.disabled} for b in db.scalars(select(ActivationBatch).order_by(ActivationBatch.created_at.desc()).offset(offset).limit(limit))]}

class DisableReq(BaseModel):
    disabled:bool
    request_id:str|None=None

@router.patch('/admin/activation/batches/{batch_id}')
def disable_batch(batch_id:str,req:DisableReq,db=Depends(get_session)):
    b=db.get(ActivationBatch,batch_id)
    if not b: fail(404,'NOT_FOUND','批次不存在')
    b.disabled=req.disabled
    return {'id':b.id,'disabled':b.disabled}

@router.get('/admin/activation/codes')
def codes(batch_id:str|None=None,q:str='',offset:int=0,limit:int=50,db=Depends(get_session)):
    if offset<0 or not 1<=limit<=100:fail(422,'INVALID_PAGE','分页参数无效')
    stmt=select(ActivationCode)
    if batch_id:stmt=stmt.where(ActivationCode.batch_id==batch_id)
    if q:stmt=stmt.where((ActivationCode.digest==hashlib.sha256(q.strip().upper().encode()).hexdigest())|(ActivationCode.suffix==q.upper()))
    return {'codes':[{'id':c.id,'batch_id':c.batch_id,'suffix':c.suffix,'disabled':c.disabled,'used_by':c.used_by,'used_at':iso(c.used_at),'entitlement_expires_at':iso(c.entitlement_expires_at)} for c in db.scalars(stmt.order_by(ActivationCode.id).offset(offset).limit(limit))]}

@router.patch('/admin/activation/codes/{code_id}')
def disable_code(code_id:str,req:DisableReq,db=Depends(get_session)):
    c=db.get(ActivationCode,code_id)
    if not c:fail(404,'NOT_FOUND','激活码不存在')
    c.disabled=req.disabled
    return {'id':c.id,'disabled':c.disabled}


def grant(user,days,source):
    # Preserve pre-B07 perpetual entitlement. New finite grants extend the later of now/expiry.
    if user.membership=='member' and user.membership_expires_at is None:
        user.membership_source=user.membership_source or 'legacy';return
    user.membership_expires_at=max(user.membership_expires_at or m.utcnow(),m.utcnow())+timedelta(days=days)
    user.membership='member';user.membership_source=source


def redeem(db,user_id,code):
    c=db.scalar(select(ActivationCode).where(ActivationCode.digest==hashlib.sha256(code.strip().upper().encode()).hexdigest()))
    if not c:fail(422,'INVALID_CODE','激活码无效')
    b=db.get(ActivationBatch,c.batch_id)
    if c.disabled or b.disabled:fail(409,'CODE_DISABLED','激活码已停用')
    if b.expires_at<=m.utcnow():fail(409,'CODE_EXPIRED','激活码已过兑换截止时间')
    if c.used_by:fail(409,'CODE_USED','激活码已兑换')
    u=db.get(m.User,user_id);grant(u,b.duration_days,'activation')
    c.used_by=user_id;c.used_at=m.utcnow();c.entitlement_expires_at=u.membership_expires_at
    return {'ok':True,'redemption_id':c.id,**membership(u)}


def diary_view(n):
    return {'id':n.id,'note_id':n.id,'user_id':n.user_id,'companion_id':n.companion_id,'entry_date':n.entry_date,
            'timezone':n.timezone,'content':n.content,'emotion':n.emotion,'revision':n.revision,
            'canonical_diary_id':n.canonical_diary_id or n.id,'is_current':n.is_current,'legacy_duplicate':not n.is_current,
            'created_at':iso(n.ts),'updated_at':iso(n.updated_at)}


def entry_day(value,tz):
    try:
        zone=ZoneInfo(tz)
        day=date.fromisoformat(value) if value else datetime.now(zone).date()
    except (ZoneInfoNotFoundError,ValueError,TypeError):fail(422,'INVALID_DATE','日期或时区无效')
    if day>datetime.now(zone).date():fail(422,'FUTURE_DIARY','不能保存未来日期的日记')
    return day.isoformat()


def diary_create(db,req):
    if not req.content.strip():fail(422,'EMPTY_CONTENT','内容不能为空')
    scope=cid(db);day=entry_day(req.entry_date,req.timezone)
    existing=db.scalar(select(m.DiaryNote).where(m.DiaryNote.user_id==uid(db),m.DiaryNote.companion_id==scope,m.DiaryNote.entry_date==day,m.DiaryNote.is_current==True))
    if existing:fail(409,'DIARY_EXISTS','当天已有日记，请更新原记录',canonical_diary_id=existing.id,diary=diary_view(existing))
    n=m.DiaryNote(user_id=uid(db),companion_id=scope,content=req.content,emotion=req.emotion,entry_date=day,timezone=req.timezone)
    db.add(n);db.flush();n.canonical_diary_id=n.id
    memory=store.add_memory(db,uid(db),type='diary',content=req.content,summary='日记记录',emotion=None)
    n.memory_id=memory.id
    return {'ok':True,'note_id':n.id,'created_at':iso(n.ts),'emotion':n.emotion,'diary':diary_view(n)}

class DiaryPatch(BaseModel):
    companion_id:str
    entry_date:str
    timezone:str='Asia/Shanghai'
    content:str=Field(min_length=1,max_length=20000)
    expected_revision:int=Field(ge=1)
    request_id:str|None=None
    client_request_id:str|None=None

@router.patch('/diary/{diary_id}')
def diary_patch(diary_id:str,req:DiaryPatch,db=Depends(get_session)):
    n=db.get(m.DiaryNote,diary_id)
    if not n or n.user_id!=uid(db):fail(404,'NOT_FOUND','日记不存在')
    if not req.content.strip():fail(422,'EMPTY_CONTENT','内容不能为空')
    if not n.is_current:fail(409,'LEGACY_DIARY_READONLY','旧重复记录保留只读',canonical_diary_id=n.canonical_diary_id)
    if n.companion_id!=cid(db) or n.entry_date!=entry_day(req.entry_date,req.timezone):fail(409,'DIARY_SCOPE_MISMATCH','日记日期或陪伴不一致')
    if req.expected_revision!=n.revision:fail(409,'REVISION_CONFLICT','日记已被修改',revision=n.revision,diary=diary_view(n))
    n.content=req.content;n.revision+=1;n.updated_at=m.utcnow()
    memory=db.get(m.MemoryEntry,n.memory_id) if n.memory_id else None
    if memory is not None:memory.content=req.content
    return {'ok':True,'diary':diary_view(n)}


def recover_operation(db,op):
    """Expired local UoW has no committed business rows without its receipt.
    Unknown provider effects stay recoverable through the persisted original order.
    A late owner is fenced at the final transaction before committing business rows.
    """
    from sqlalchemy import text
    from memory.b07_models import Order
    if op.status!='processing' or (op.lease_expires_at and op.lease_expires_at>m.utcnow()):return op
    db.execute(text('BEGIN IMMEDIATE'));db.refresh(op)
    if op.status!='processing' or (op.lease_expires_at and op.lease_expires_at>m.utcnow()):return op
    order=db.scalar(select(Order).where(Order.user_id==op.user_id,Order.request_id==op.request_id))
    external=op.endpoint.startswith('/api/subscriptions/orders')
    op.status='failed';op.http_status=409;op.updated_at=m.utcnow()
    op.error={'code':'ORDER_RESULT_UNCONFIRMED' if external else 'OPERATION_INTERRUPTED',
              'message':'请查询原订单恢复结果' if external else '处理已中断，本次业务数据未提交',
              'request_id':op.request_id,'retryable':not external}
    if order:op.resource_id=order.id
    return op
