"""Queue intents with their originating transaction; no in-process fire-and-forget."""
import hashlib,json
from sqlalchemy import select
from memory.scope import current_db
from memory.b07_models import Job

def enqueue(kind,user_id,payload,dedupe=None):
    db=current_db.get()
    if db is None: raise RuntimeError('Background work requires a managed transaction')
    cid=db.info.get('companion_id')
    key=dedupe or f'{kind}:{user_id}:{cid}:{db.info.get("request_id") or hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()}'
    # Delay insertion until external calls are finished, keeping SQLite write locks short.
    db.info.setdefault('job_intents',[]).append(dict(kind=kind,user_id=user_id,companion_id=cid,payload=payload,dedupe_key=key))

def flush_intents(db):
    for args in db.info.pop('job_intents',[]):
        if not db.scalar(select(Job).where(Job.dedupe_key==args['dedupe_key'])): db.add(Job(**args))
