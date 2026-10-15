import pytest
from sqlalchemy import select
from conftest import account
from memory.db import _RawSessionLocal
from memory import models as m
from memory.b07_models import Job
import admin_cli


@pytest.mark.parametrize('extractor', ['memory.breakup.extract', 'memory.loved_one.extract', 'memory.pet.extract'])
def test_each_companionship_extractor_persists_scoped_memory(client, extractor):
    uid, _, state = account(client)
    with _RawSessionLocal() as db:
        job = Job(kind='memory', user_id=uid, companion_id=state['current_companion_id'], payload={'message':'今天在家整理了以前的照片，心情很平静。', 'type':'chat', 'extractor':extractor}, dedupe_key='active-memory')
        db.add(job); db.commit(); jid = job.id
    assert admin_cli.run_one()
    with _RawSessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == 'completed' and job.attempts == 1
        entries = list(db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.user_id == uid)))
        assert len(entries) == 1 and entries[0].companion_id == state['current_companion_id']
        assert entries[0].summary and isinstance(entries[0].cbt, dict)
        assert db.scalar(select(Job).where(Job.user_id == uid, Job.kind == 'portrait')).status == 'pending'


def test_archived_companion_job_stays_cancelled_instead_of_retrying(client, monkeypatch):
    uid, _, state = account(client)
    with _RawSessionLocal() as db:
        c = db.get(m.Companion, state['current_companion_id']); c.archived_at = m.utcnow()
        job = Job(kind='memory', user_id=uid, companion_id=c.id, payload={'message':'合成验证', 'type':'chat'}, dedupe_key='inactive-before-run')
        db.add(job); db.commit(); jid = job.id
    monkeypatch.setattr('memory.async_write._do_write', lambda *args: (_ for _ in ()).throw(AssertionError('Inactive work must not invoke a provider')))
    assert admin_cli.run_one()
    with _RawSessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == 'cancelled' and job.attempts == 1
        assert job.error_code == 'ACCOUNT_OR_COMPANION_INACTIVE'
    assert not admin_cli.run_one()


def test_archive_during_processing_cancels_without_retry_or_memory_write(client, monkeypatch):
    uid, _, state = account(client)
    with _RawSessionLocal() as db:
        job = Job(kind='memory', user_id=uid, companion_id=state['current_companion_id'], payload={'message':'合成验证', 'type':'chat'}, dedupe_key='archive-during-run')
        db.add(job); db.commit(); jid = job.id
    def archive_then_fail(*args):
        with _RawSessionLocal() as other:
            other.get(m.Companion, state['current_companion_id']).archived_at = m.utcnow()
            other.commit()
        raise ValueError('Companion archived during processing')
    monkeypatch.setattr('memory.async_write._do_write', archive_then_fail)
    assert admin_cli.run_one()
    with _RawSessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == 'cancelled' and job.attempts == 1
        assert job.error_code == 'ACCOUNT_OR_COMPANION_INACTIVE'
        assert not list(db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.user_id == uid)))
