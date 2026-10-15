"""Configured products, durable original orders and one-time message grants."""
import json
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter,Depends,Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel,Field
from sqlalchemy import select,text,update
from config import get_settings
from memory.db import get_session
from memory import models as m
from memory.b07_models import Plan,Order,MessageReceipt,Job
from api.security import SecureRoute,fail,iso
from api.b07 import uid,grant
from gateway.payments import WeChatPay,configured,PaymentError
router=APIRouter(prefix='/api',route_class=SecureRoute)

def templates():
    try:
        data=json.loads(get_settings().wechat_subscribe_templates)
        if not isinstance(data,list):return []
        for t in data:
            if not all(t.get(k) for k in ('id','title','description','purpose','page','data')) or not isinstance(t['data'],dict):return []
        return data
    except (ValueError,AttributeError):return []

def public_plan(p):
    return {'id':p.id,'name':p.name,'price_label':f'¥{p.amount/100:.2f}','period_label':p.period_label,'benefits':p.benefits,'terms':p.terms}

@router.get('/subscriptions/config')
def config(platform:str='unknown',db=Depends(get_session)):
    ready=configured() and platform in get_settings().payment_allowed_platforms.split(',')
    plans=[public_plan(p) for p in db.scalars(select(Plan).where(Plan.enabled==True)) if platform in p.platforms] if ready else []
    ts=templates();msg=bool(ts and get_settings().wechat_appid and get_settings().wechat_secret)
    return {'ok':True,'membership_available':bool(plans),'messages_available':msg,'plans':plans,
        'templates':[{k:t[k] for k in ('id','title','description')} for t in ts] if msg else [],
        'membership_unavailable_reason':None if plans else '正式套餐、商户或平台许可尚未配置',
        'messages_unavailable_reason':None if msg else '正式消息模板尚未配置'}

class PlanReq(BaseModel):
    id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    name:str=Field(min_length=1,max_length=128)
    amount:int=Field(ge=1,le=10000000)
    duration_days:int=Field(ge=1,le=3650)
    period_label:str=Field(min_length=1,max_length=128)
    benefits:str=Field(min_length=1,max_length=2000)
    terms:str=Field(min_length=1,max_length=4000)
    platforms:list[Literal['ios','android','devtools']]=Field(min_length=1)
    enabled:bool=False
    request_id:str|None=None

@router.post('/admin/subscriptions/plans')
def plan_save(req:PlanReq,db=Depends(get_session)):
    p=db.get(Plan,req.id)
    if p is None:p=Plan(id=req.id);db.add(p)
    for k,v in req.model_dump(exclude={'request_id'}).items():setattr(p,k,v)
    return {'ok':True,'plan':public_plan(p)}

class OrderReq(BaseModel):
    plan_id:str
    client_request_id:str|None=None
    request_id:str|None=None
    platform:Literal['ios','android','devtools']


def order_view(o,payment=None):
    return {'ok':True,'order_id':o.id,'client_request_id':o.request_id,'status':o.status,
            'can_resume':o.status in ('unpaid','processing') and o.expires_at>m.utcnow(),
            'payment':payment,'amount':o.amount,'expires_at':iso(o.expires_at)}

def own_order(db,key,by_request=False):
    q=select(Order).where(Order.user_id==uid(db))
    q=q.where(Order.request_id==key) if by_request else q.where(Order.id==key)
    o=db.scalar(q)
    if not o:fail(404,'ORDER_NOT_FOUND','订单不存在')
    return o

def ensure_pay():
    if not configured():fail(503,'PAYMENT_NOT_CONFIGURED','支付尚未配置')
    return WeChatPay()

def apply_paid(db,o,data):
    s=get_settings()
    if data.get('out_trade_no')!=o.id or data.get('mchid')!=s.wechat_pay_mchid or data.get('appid')!=s.wechat_appid or data.get('payer',{}).get('openid')!=o.user_id:
        fail(400,'PAYMENT_OWNER_MISMATCH','支付身份不一致')
    a=data.get('amount',{})
    if a.get('total')!=o.amount or a.get('currency')!='CNY':fail(400,'PAYMENT_AMOUNT_MISMATCH','支付金额不一致')
    if data.get('trade_state')!='SUCCESS' or not data.get('transaction_id'):fail(400,'PAYMENT_STATE_INVALID','支付状态无效')
    if o.paid_at is not None or o.transaction_id is not None:
        if o.transaction_id!=data['transaction_id']:fail(409,'PAYMENT_TRANSACTION_MISMATCH','支付流水不一致')
        o.status='paid'
        return
    if db.scalar(select(Order).where(Order.transaction_id==data['transaction_id'],Order.id!=o.id)):fail(409,'PAYMENT_TRANSACTION_REUSED','支付流水已使用')
    grant(db.get(m.User,o.user_id),o.duration_days,'payment')
    o.status='paid';o.transaction_id=data['transaction_id'];o.paid_at=m.utcnow()

@router.post('/subscriptions/orders')
def create_order(req:OrderReq,db=Depends(get_session)):
    pay=ensure_pay();p=db.get(Plan,req.plan_id)
    if not p or not p.enabled or req.platform not in p.platforms or req.platform not in get_settings().payment_allowed_platforms.split(','):
        fail(409,'PLAN_UNAVAILABLE','当前平台不能购买此套餐')
    if db.get(m.UserAuth,uid(db)):fail(409,'WECHAT_LOGIN_REQUIRED','请通过微信登录购买')
    rid=db.info['request_id']
    o=db.scalar(select(Order).where(Order.user_id==uid(db),Order.request_id==rid))
    if not o:
        o=Order(user_id=uid(db),request_id=rid,plan_id=p.id,snapshot={**public_plan(p),'platform':req.platform},amount=p.amount,duration_days=p.duration_days,expires_at=m.utcnow()+timedelta(minutes=30))
        db.add(o);db.flush();db.finish()  # original out_trade_no survives an unknown external outcome
    try:
        prepay_id=pay.prepay(o)
        db.execute(text('BEGIN IMMEDIATE'));db.refresh(o)
        if o.status=='paid':return order_view(o)
        o.prepay_id=prepay_id;o.status='unpaid'
        return order_view(o,pay.payment_params(o.prepay_id))
    except PaymentError: return order_view(o)


def refresh_order(db,o):
    if o.status=='paid':return o
    pay=ensure_pay()
    try:data=pay.query(o)
    except PaymentError:return o  # unknown remains recoverable; never invent cancellation
    db.execute(text('BEGIN IMMEDIATE'))
    db.refresh(o)
    if o.status=='paid' or o.paid_at is not None:return o
    if data.get('trade_state')=='SUCCESS':apply_paid(db,o,data)
    elif data.get('trade_state') in ('CLOSED','REVOKED'):o.status='closed'
    elif data.get('trade_state')=='NOTPAY':o.status='unpaid'
    return o

@router.get('/subscriptions/orders/by-request/{request_id}')
def by_request(request_id:str,db=Depends(get_session)):
    return order_view(refresh_order(db,own_order(db,request_id,True)))

@router.post('/subscriptions/orders/{order_id}/resume')
def resume(order_id:str,db=Depends(get_session)):
    o=refresh_order(db,own_order(db,order_id));db.finish()
    if o.status not in ('unpaid','processing') or o.expires_at<=m.utcnow():return order_view(o)
    pay=ensure_pay()
    try:
        prepay_id=o.prepay_id or pay.prepay(o)
        db.execute(text('BEGIN IMMEDIATE'));db.refresh(o)
        if o.status=='paid' or o.paid_at is not None:return order_view(o)
        if o.status not in ('unpaid','processing'):return order_view(o)
        o.prepay_id=prepay_id;o.status='unpaid';return order_view(o,pay.payment_params(o.prepay_id))
    except PaymentError:return order_view(o)

@router.post('/subscriptions/orders/{order_id}/cancel')
def cancel(order_id:str,db=Depends(get_session)):
    o=refresh_order(db,own_order(db,order_id));db.finish()
    if o.status in ('paid','closed','cancelled','expired'):return order_view(o)
    try:ensure_pay().close(o)
    except PaymentError:return order_view(o)
    db.execute(text('BEGIN IMMEDIATE'));db.refresh(o)
    if o.status!='paid':o.status='expired' if o.expires_at<=m.utcnow() else 'cancelled'
    return order_view(o)

@router.post('/payments/wechat/notify')
async def notify(request:Request,db=Depends(get_session)):
    try:data=ensure_pay().notification(request.headers,await request.body())
    except PaymentError:fail(401,'INVALID_PAYMENT_SIGNATURE','支付通知校验失败')
    await run_in_threadpool(db.execute,text('BEGIN IMMEDIATE'))
    o=db.get(Order,data.get('out_trade_no'))
    if not o:fail(404,'ORDER_NOT_FOUND','订单不存在')
    apply_paid(db,o,data)
    return {'code':'SUCCESS','message':'成功'}

class ReceiptReq(BaseModel):
    client_request_id:str|None=None
    request_id:str|None=None
    template_id:str
    choice:Literal['accept','reject','ban','filter']

@router.post('/subscriptions/messages/receipts')
def receipt(req:ReceiptReq,db=Depends(get_session)):
    if req.template_id not in {t['id'] for t in templates()}:fail(409,'TEMPLATE_UNAVAILABLE','提醒模板未配置或已停用')
    if req.choice!='accept':
        db.execute(update(MessageReceipt).where(MessageReceipt.user_id==uid(db),MessageReceipt.template_id==req.template_id).values(remaining=0))
        for job in db.scalars(select(Job).where(Job.user_id==uid(db),Job.kind=='message',Job.status.in_(['pending','retry']))):
            if job.payload.get('template_id')==req.template_id:job.status='cancelled';job.error_code='CONSENT_WITHDRAWN'
    row=MessageReceipt(user_id=uid(db),request_id=db.info['request_id'],template_id=req.template_id,choice=req.choice,remaining=1 if req.choice=='accept' else 0)
    db.add(row);db.flush()
    return {'ok':True,'client_request_id':row.request_id,'id':row.id,'choice':row.choice,'remaining':row.remaining}

@router.get('/subscriptions/messages/status')
def message_status(db=Depends(get_session)):
    rows=db.scalars(select(MessageReceipt).where(MessageReceipt.user_id==uid(db)).order_by(MessageReceipt.created_at.desc())).all()
    return {'templates':[{'template_id':t['id'],'remaining':sum(r.remaining for r in rows if r.template_id==t['id']),
                         'choice':next((r.choice for r in rows if r.template_id==t['id']),None)} for t in templates()],
        'permanent':False}

class MessageJobReq(BaseModel):
    user_id:str
    template_id:str
    purpose:str
    request_id:str
    delay_seconds:int=Field(default=0,ge=0,le=2592000)

@router.post('/admin/subscriptions/messages/jobs')
def message_job(req:MessageJobReq,db=Depends(get_session)):
    t=next((t for t in templates() if t['id']==req.template_id),None)
    if not t or t['purpose']!=req.purpose:fail(409,'TEMPLATE_PURPOSE_MISMATCH','用途与模板不一致')
    r=db.scalar(select(MessageReceipt).where(MessageReceipt.user_id==req.user_id,MessageReceipt.template_id==req.template_id,MessageReceipt.remaining>0).order_by(MessageReceipt.created_at))
    if not r:fail(409,'NO_MESSAGE_CREDIT','无可用提醒授权')
    # Reserve exactly one user-authorized opportunity. Template data is approved config, never private text.
    r.remaining-=1
    j=Job(kind='message',user_id=req.user_id,dedupe_key='message:'+req.request_id,
          payload={'template_id':req.template_id,'purpose':req.purpose,'receipt_id':r.id},due_at=m.utcnow()+timedelta(seconds=req.delay_seconds))
    db.add(j);db.flush();return {'ok':True,'id':j.id,'status':j.status}

@router.get('/admin/jobs')
def jobs(limit:int=50,db=Depends(get_session)):
    if not 1<=limit<=100:fail(422,'INVALID_PAGE','分页参数无效')
    return {'jobs':[{'id':j.id,'kind':j.kind,'user_id':j.user_id,'status':j.status,'attempts':j.attempts,'error_code':j.error_code,'due_at':iso(j.due_at)} for j in db.scalars(select(Job).order_by(Job.updated_at.desc()).limit(limit))]}
