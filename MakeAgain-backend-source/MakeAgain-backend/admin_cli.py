"""Independent administrator initialization and durable job worker. No web signup escalation."""
import argparse,getpass,sys,importlib,time,signal
from datetime import timedelta
from sqlalchemy import select,text,update
from config import validate_runtime
from memory.db import init_db,_RawSessionLocal
from memory.scope import current_db
from memory import models as m
from memory.b07_models import Audit,Job,MessageReceipt
from auth import hash_password


def init_admin(username):
    password=getpass.getpass('New administrator password: ')
    if len(password)<12:raise ValueError('Use at least 12 characters')
    if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords differ')
    with _RawSessionLocal() as db:
        db.execute(text('BEGIN IMMEDIATE'))
        if db.scalar(select(m.UserAuth).where(m.UserAuth.username==username.lower())):raise ValueError('Account already exists; initialization does not promote existing users')
        u=m.User(role='admin',account_status='active');db.add(u);db.flush()
        digest,salt=hash_password(password)
        db.add(m.UserAuth(user_id=u.id,username=username.lower(),password_hash=digest,password_salt=salt))
        db.add(Audit(actor_id='local-cli',action='initialize_admin',target_id=u.id));db.commit()
        print('Administrator initialized. No token or password printed.')


def run_one():
    db=_RawSessionLocal()
    try:
        db.execute(text('BEGIN IMMEDIATE'))
        job=db.scalar(select(Job).where(Job.status.in_(['pending','retry']),Job.due_at<=m.utcnow()).order_by(Job.due_at,Job.id))
        if not job:return False
        job.status='running';job.attempts+=1;job.updated_at=m.utcnow();db.commit()
        job_id=job.id
        db.info.update(managed=True,borrowed=True,user_id=job.user_id,companion_id=job.companion_id,request_id='job:'+job.id)
        context=current_db.set(db)
        try:
            user=db.get(m.User,job.user_id)
            c=db.get(m.Companion,job.companion_id) if job.companion_id else None
            if not user or user.account_status!='active' or (c and c.archived_at):
                job.status='cancelled';job.error_code='ACCOUNT_OR_COMPANION_INACTIVE'
            elif job.kind=='message':
                from api.subscriptions import templates
                from gateway.messages import send_message,MessageError
                t=next((t for t in templates() if t['id']==job.payload['template_id'] and t['purpose']==job.payload['purpose']),None)
                latest=db.scalar(select(MessageReceipt).where(MessageReceipt.user_id==job.user_id,MessageReceipt.template_id==job.payload['template_id']).order_by(MessageReceipt.created_at.desc()))
                if not t or not latest or latest.choice!='accept':raise MessageError('CONSENT_OR_TEMPLATE_UNAVAILABLE')
                send_message(job.user_id,t);job.status='sent'
            elif job.kind=='memory':
                from memory.async_write import _do_write
                extractor=None
                if job.payload.get('extractor'):
                    allowed={'memory.breakup.extract':'extract_breakup_turn','memory.loved_one.extract':'extract_loved_one_turn','memory.pet.extract':'extract_pet_turn'}
                    if job.payload['extractor'] not in allowed:raise ValueError('Unsupported extractor')
                    extractor=getattr(importlib.import_module(job.payload['extractor']),allowed[job.payload['extractor']])
                _do_write(job.user_id,job.payload['message'],job.payload['type'],extractor);job.status='completed'
            elif job.kind=='portrait':
                from memory.portrait import merge_if_due
                from gateway.client import LLMClient
                merge_if_due(db,job.user_id,LLMClient());job.status='completed'
            elif job.kind=='report':
                from agent.async_report import _do_report
                _do_report(job.user_id,job.payload['session_id'],job.payload['loss_type'],job.payload['history']);job.status='completed'
            elif job.kind=='weekly':
                from agent.weekly import _do
                _do(job.user_id,job.payload['week_key']);job.status='completed'
            elif job.kind=='summary':
                from agent.history_summary import _do
                _do(job.user_id,job.payload['date_key'],job.payload['status'],());job.status='completed'
            else:raise ValueError('Unsupported job')
            from memory.jobs import flush_intents
            flush_intents(db)
            # Recheck under the write lock before persisting work for an archived companion.
            db.flush()
            if c and job.status != 'cancelled':
                db.refresh(c)
                if c.archived_at:raise ValueError('Companion archived during processing')
            job.updated_at=m.utcnow();db.finish()
        except Exception as exc:
            from gateway.messages import MessageError
            db.rollback();job=db.get(Job,job_id)
            current_user=db.get(m.User,job.user_id)
            current_companion=db.get(m.Companion,job.companion_id) if job.companion_id else None
            inactive=not current_user or current_user.account_status!='active' or (current_companion and current_companion.archived_at)
            retryable=not isinstance(exc,MessageError) or exc.retryable
            job.status='cancelled' if inactive and job.kind!='message' else 'unknown' if isinstance(exc,MessageError) and exc.unknown else 'retry' if retryable and job.attempts<3 else 'failed'
            job.error_code='ACCOUNT_OR_COMPANION_INACTIVE' if job.status=='cancelled' else exc.code if isinstance(exc,MessageError) else type(exc).__name__
            job.due_at=m.utcnow()+timedelta(seconds=60*2**job.attempts);job.updated_at=m.utcnow()
            if job.kind=='report' and job.status!='cancelled':
                interview=db.get(m.InterviewSession,job.payload['session_id'])
                if interview:
                    state=dict(interview.state or {});state.update(generating=job.status=='retry',report_failed=job.status=='failed');interview.state=state
            db.finish()
        finally:current_db.reset(context)
        return True
    finally:db.info['borrowed']=False;db.close()


def recover_jobs():
    # Run only after stopping old worker processes. Unknown sends remain blocked for human review.
    with _RawSessionLocal() as db:
        for j in db.scalars(select(Job).where(Job.status=='running',Job.updated_at<m.utcnow()-timedelta(minutes=15))):
            j.status='unknown' if j.kind=='message' else 'retry';j.error_code='WORKER_INTERRUPTED'
        db.commit()
        from api.b07 import recover_operation
        from memory.b07_models import Operation
        for op in db.scalars(select(Operation).where(Operation.status=="processing")):
            recover_operation(db,op);db.commit()


def reconcile_orders(limit=20):
    """Query existing out_trade_no; close expired unpaid orders only after provider confirmation."""
    from gateway.payments import configured,WeChatPay,PaymentError
    from memory.b07_models import Order
    from api.subscriptions import refresh_order
    if not configured():return 0
    with _RawSessionLocal() as listing:
        ids=list(listing.scalars(select(Order.id).where(Order.status.in_(['processing','unpaid'])).order_by(Order.created_at).limit(limit)))
    count=0
    for oid in ids:
        db=_RawSessionLocal();db.info.update(managed=True,borrowed=True)
        token=current_db.set(db)
        try:
            o=db.get(Order,oid);refresh_order(db,o);db.finish()
            if o.status=='unpaid' and o.expires_at<=m.utcnow():
                WeChatPay().close(o)
                db.execute(text('BEGIN IMMEDIATE'));db.refresh(o)
                if not o.paid_at:o.status='expired'
                db.finish()
            count+=1
        except Exception:
            db.rollback()  # Unknown stays processing/unpaid; next sweep queries same ID.
        finally:
            current_db.reset(token);db.info['borrowed']=False;db.close()
    return count


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('init-admin');p.add_argument('--username',required=True)
    sub.add_parser('migrate')
    sub.add_parser('reconcile-orders')
    p=sub.add_parser('worker');p.add_argument('--max-jobs',type=int,default=100);p.add_argument('--forever',action='store_true');p.add_argument('--poll-seconds',type=float,default=3)
    sub.add_parser('recover-stopped-worker')
    args=parser.parse_args();validate_runtime();init_db()
    if args.command=='init-admin':init_admin(args.username)
    elif args.command=='worker':
        stopped=False
        def stop(*_):
            nonlocal stopped
            stopped=True
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        count=0;last_reconcile=0
        while not stopped:
            if time.monotonic()-last_reconcile>60:
                reconcile_orders();last_reconcile=time.monotonic()
            worked=run_one()
            if worked:count+=1
            if not args.forever and (not worked or count>=args.max_jobs):break
            if not worked:time.sleep(max(0.2,min(args.poll_seconds,30)))
        print(f'Processed {count} jobs; stopped cleanly')
    elif args.command=='reconcile-orders':print(f'Reconciled {reconcile_orders()} original orders')
    elif args.command=='recover-stopped-worker':recover_jobs()
    else:print('Migration complete')

if __name__=='__main__':main()
