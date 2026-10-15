"""Request transaction boundary and companion filters shared by API and workers.

Legacy store.commit calls flush inside a managed request; only the boundary can
commit. This is intentional: business rows and operation.result commit together.
"""
from contextvars import ContextVar
from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

current_db = ContextVar('b07_db', default=None)

class ManagedSession(Session):
    def commit(self):
        if self.info.get('managed'):
            self.flush()
        else:
            super().commit()

    def finish(self):
        super().commit()

    def close(self):
        if not self.info.get('borrowed'):
            super().close()


def scoped_models():
    from memory import models as m
    return [m.Portrait,m.MemoryEntry,m.ChatMessage,m.DailyChatSummary,m.EmotionNode,
            m.InterviewSession,m.NudgeLog,m.DailyPick,m.TreeholeSeen,m.StateSnapshot,
            m.ItemMemory,m.PhotoMemory,m.TodoItem,m.Report,m.WeeklyReport,m.DiaryNote,m.EchoEntry]

@event.listens_for(ManagedSession, 'do_orm_execute')
def filter_scope(state):
    cid = state.session.info.get('companion_id')
    if cid and not state.execution_options.get('unscoped'):
        for model in scoped_models():
            state.statement = state.statement.options(with_loader_criteria(
                model, model.companion_id == cid, include_aliases=True))

@event.listens_for(ManagedSession, 'before_flush')
def stamp_scope(db, *_):
    cid = db.info.get('companion_id')
    if not cid:
        return
    from memory.models import TreeholeLetter
    types = tuple(scoped_models()) + (TreeholeLetter,)
    for row in db.new:
        if isinstance(row, types) and row.companion_id is None:
            row.companion_id = cid
