"""Persistent recent turns: page exits and process restarts keep the same session."""
from sqlalchemy import select
from memory.db import SessionLocal
from memory.models import ChatMessage
from config import get_settings

def append_turn(user_id,role,content,session_id=None):
    # save_chat_turn persists both messages exactly once in the request transaction.
    pass

def get_turns(user_id,session_id=None):
    db=SessionLocal()
    try:
        rows=db.scalars(select(ChatMessage).where(ChatMessage.user_id==user_id,ChatMessage.session_id==(session_id or '')).order_by(ChatMessage.id.desc()).limit(get_settings().session_context_max_turns*2)).all()
        return [{'role':r.role,'content':r.content} for r in reversed(rows)]
    finally:
        if not db.info.get('managed'):db.close()

def clear_turns(user_id,session_id=None):
    pass
