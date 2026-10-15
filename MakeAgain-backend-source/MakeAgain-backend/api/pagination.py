"""Signed keyset pagination scoped to account, companion and collection.

The immutable creation timestamp plus ID breaks timestamp ties. The first page's
server-time ceiling excludes later inserts. Mutable todo buckets additionally
carry a revision fingerprint and require refresh after queue/history changes.
"""
import base64
import hashlib
import hmac
import json
from datetime import datetime
from sqlalchemy import and_, or_, select
from config import get_settings
from memory.models import utcnow
from api.security import fail


def _key():
    s=get_settings()
    secret=s.media_signing_key or s.wechat_secret
    if not secret:fail(503,'CURSOR_NOT_CONFIGURED','分页签名未配置')
    return hmac.new(secret.encode(),b'b07-pagination-v1',hashlib.sha256).digest()


def _encode(payload):
    raw=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
    data=base64.urlsafe_b64encode(raw).decode().rstrip('=')
    signature=hmac.new(_key(),data.encode(),hashlib.sha256).hexdigest()
    return data+'.'+signature


def _decode(token):
    try:
        if len(token)>4096:raise ValueError()
        data,signature=token.split('.')
        expected=hmac.new(_key(),data.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        payload=json.loads(base64.urlsafe_b64decode(data+'='*(-len(data)%4)))
        if not isinstance(payload,dict) or payload.get('v')!=1:raise ValueError()
        return payload
    except (ValueError,TypeError,UnicodeDecodeError,KeyError):
        fail(422,'INVALID_CURSOR','分页标识无效，请重新加载')


def page(db,model,*,collection,limit=None,cursor=None,stamp=None,ascending=False,filters=(),revision=None):
    """Return database-limited rows and metadata. Caller handles legacy no-param mode."""
    scope={'u':db.info['user_id'],'c':db.info.get('companion_id'),'n':collection}
    stamp=stamp if stamp is not None else model.ts
    anchor=None
    if cursor:
        token=_decode(cursor)
        if any(token.get(k)!=v for k,v in scope.items()):fail(409,'CURSOR_SCOPE_MISMATCH','分页标识不属于当前账号或陪伴列表')
        if token.get('r')!=revision:fail(409,'CURSOR_STALE','列表已变化，请从第一页刷新')
        try:
            ceiling=datetime.fromisoformat(token['s'])
            anchor=(datetime.fromisoformat(token['t']),str(token['i']))
            if ceiling.tzinfo is not None or anchor[0].tzinfo is not None:raise ValueError()
        except (KeyError,TypeError,ValueError):fail(422,'INVALID_CURSOR','分页标识无效')
        if (utcnow()-ceiling).total_seconds()>3600:fail(409,'CURSOR_EXPIRED','分页标识已过期，请重新加载')
    else:
        ceiling=utcnow()
    size=limit if limit is not None else 20
    if not 1<=size<=100:fail(422,'INVALID_PAGE_SIZE','每页数量需为1到100')
    owner=getattr(model,'user_id',getattr(model,'author_user_id',None))
    conditions=[owner==scope['u'],model.companion_id==scope['c'],stamp<=ceiling,*filters]
    if anchor:
        if ascending:
            conditions.append(or_(stamp>anchor[0],and_(stamp==anchor[0],model.id>anchor[1])))
        else:
            conditions.append(or_(stamp<anchor[0],and_(stamp==anchor[0],model.id<anchor[1])))
    order=(stamp.asc(),model.id.asc()) if ascending else (stamp.desc(),model.id.desc())
    rows=db.scalars(select(model).where(*conditions).order_by(*order).limit(size+1)).all()
    more=len(rows)>size;rows=rows[:size]
    next_cursor=None
    if more:
        # SQL expression (history coalesce) uses a documented row-side accessor.
        last=rows[-1]
        last_time=(last.done_at or last.ts) if collection=='todo:history' else getattr(last,stamp.key)
        next_cursor=_encode({'v':1,**scope,'s':ceiling.isoformat(),'t':last_time.isoformat(),'i':str(last.id),'r':revision})
    return rows,{'next_cursor':next_cursor,'has_more':more,'limit':size,'companion_id':scope['c']}
