"""Anonymous community moderation and owner-visible reports."""
from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field,model_validator
from typing import Literal
from sqlalchemy import select,or_
from memory import models as m,store
from memory.b07_models import CommunityBlock,CommunityReport,Job
from memory.db import get_session
from api.security import SecureRoute,fail,iso
from api.b07 import uid
router=APIRouter(prefix='/api',route_class=SecureRoute)

def blocked(db,a,b):
    return db.scalar(select(CommunityBlock.id).where(or_((CommunityBlock.user_id==a)&(CommunityBlock.blocked_user_id==b),(CommunityBlock.user_id==b)&(CommunityBlock.blocked_user_id==a)))) is not None

class Moderate(BaseModel):
    status:Literal['published','rejected','closed']
    request_id:str|None=None

@router.patch('/admin/treehole/letters/{letter_id}')
def moderate(letter_id:str,req:Moderate,db=Depends(get_session)):
    letter=db.get(m.TreeholeLetter,letter_id)
    if not letter:fail(404,'NOT_FOUND','信件不存在')
    letter.status=req.status
    return {'id':letter.id,'status':letter.status}

class CommunityTarget(BaseModel):
    letter_id:str|None=None
    reply_id:str|None=None
    request_id:str|None=None
    companion_id:str|None=None  # Context only; community controls do not edit an archive.

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.letter_id)==bool(self.reply_id):raise ValueError("Exactly one letter_id or reply_id is required")
        return self

class ReportReq(CommunityTarget):
    reason:str=Field(min_length=1,max_length=256)
    block_author:bool=False


def resolve_target(db,req):
    """Only published peer letters or a delivered reply to the current user's own letter."""
    if req.reply_id:
        reply=db.get(m.TreeholeReply,req.reply_id)
        letter=db.get(m.TreeholeLetter,reply.letter_id) if reply else None
        if not reply or reply.status!="delivered" or not letter or letter.author_user_id!=uid(db):
            fail(404,'NOT_FOUND','回信不存在')
        target=reply.author_user_id
    else:
        letter=db.get(m.TreeholeLetter,req.letter_id)
        if not letter or letter.status!="published":fail(404,'NOT_FOUND','信件不存在')
        target=letter.author_user_id
    if target==uid(db):fail(409,'SELF_REPORT','不能举报或屏蔽自己的内容')
    return letter,target


def add_block(db,target):
    # A peer's existing block must not suppress this user's independent block choice.
    existing=db.scalar(select(CommunityBlock.id).where(CommunityBlock.user_id==uid(db),CommunityBlock.blocked_user_id==target))
    if not existing:db.add(CommunityBlock(user_id=uid(db),blocked_user_id=target))

@router.post('/treehole/report')
def report(req:ReportReq,db=Depends(get_session)):
    if not req.reason.strip():fail(422,'EMPTY_REASON','请填写举报原因')
    letter,target=resolve_target(db,req)
    r=CommunityReport(user_id=uid(db),letter_id=letter.id,reply_id=req.reply_id,reason=req.reason)
    db.add(r);db.flush()
    if req.block_author:add_block(db,target)
    return {'ok':True,'id':r.id,'status':r.status,'blocked':req.block_author}

@router.post('/treehole/block')
def block(req:CommunityTarget,db=Depends(get_session)):
    _letter,target=resolve_target(db,req);add_block(db,target)
    return {'ok':True,'blocked':True}

@router.get('/admin/treehole/reports')
def reports(limit:int=50,db=Depends(get_session)):
    if not 1<=limit<=100:fail(422,'INVALID_PAGE','分页参数无效')
    return {'reports':[{'id':r.id,'letter_id':r.letter_id,'reply_id':r.reply_id,'reason':r.reason,'status':r.status,'created_at':iso(r.created_at)} for r in db.scalars(select(CommunityReport).order_by(CommunityReport.created_at.desc()).limit(limit))]}

@router.post('/initial-report/{user_id}/retry')
def retry_report(user_id:str,db=Depends(get_session)):
    session=store.get_active_interview(db,user_id)
    if not session or session.status!='completed':fail(409,'INTERVIEW_INCOMPLETE','请先完成访谈')
    if (session.state or {}).get('report_ready'):return {'ok':True,'status':'ready','report_ready':True}
    job=db.scalar(select(Job).where(Job.dedupe_key=='report:'+session.id))
    if job:
        if job.status in ('running','pending','retry'):return {'ok':True,'status':'generating','report_ready':False}
        job.status='pending';job.attempts=0;job.error_code=None;job.due_at=m.utcnow()
    else:
        from memory.jobs import enqueue
        enqueue('report',user_id,{'session_id':session.id,'loss_type':session.loss_type,'history':(session.state or {}).get('history',[])},dedupe='report:'+session.id)
    state=dict(session.state or {});state.update(generating=True,report_failed=False);session.state=state
    return {'ok':True,'status':'generating','report_ready':False}
