"""Legacy metadata migration must preserve the dates users already saw."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest


@pytest.mark.parametrize('existing_companion', [False, True])
def test_legacy_backfill_preserves_activity_dates(tmp_path, existing_companion):
    root = Path(__file__).resolve().parents[1]
    path = tmp_path / 'legacy.sqlite3'
    stamp = '2024-01-02 03:04:05.000000'
    with sqlite3.connect(path) as db:
        db.executescript((root / 'tests/fixtures/legacy-schema.sql').read_text())
        def insert(table, values):
            for _, name, kind, required, _, _ in db.execute('PRAGMA table_info(' + table + ')'):
                if required and name not in values:
                    values[name] = '{}' if kind == 'JSON' else 0 if kind in ('BOOLEAN', 'FLOAT', 'INTEGER') else ''
            db.execute('INSERT INTO ' + table + '(' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', tuple(values.values()))
        insert('users', dict(id='legacy', membership='free', created_at=stamp))
        if existing_companion:
            insert('companions', dict(id='original', user_id='legacy', relationship_type='relative',
                                     ending_stage='active', started_at=stamp, created_at=stamp, updated_at=stamp))
        insert('user_states', dict(user_id='legacy', current_companion_id='original' if existing_companion else None,
                                   relationship_type='relative', created_at=stamp, updated_at=stamp))
        insert('portraits', dict(id='portrait', user_id='legacy', kind='user', content='{"original": "kept"}', updated_at=stamp))
        insert('interview_sessions', dict(id='interview', user_id='legacy', status='active',
                                         state='{"original": "kept"}', created_at=stamp, updated_at=stamp))
        db.commit()
    env = dict(os.environ, DATABASE_URL='sqlite:///' + str(path), PYTHONDONTWRITEBYTECODE='1')
    for _ in range(2):
        run = subprocess.run([sys.executable, '-c', 'from memory.db import init_db; init_db()'],
                             cwd=root, env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        with sqlite3.connect(path) as db:
            for table in ('user_states', 'portraits', 'interview_sessions'):
                assert db.execute('SELECT updated_at FROM ' + table).fetchone()[0] == stamp
            cid = db.execute('SELECT current_companion_id FROM user_states').fetchone()[0]
            assert cid
            assert db.execute('SELECT companion_id FROM portraits').fetchone()[0] == cid
            assert db.execute('SELECT companion_id FROM interview_sessions').fetchone()[0] == cid
            assert db.execute('SELECT content FROM portraits').fetchone()[0] == '{"original": "kept"}'
            assert db.execute('SELECT state FROM interview_sessions').fetchone()[0] == '{"original": "kept"}'
            if existing_companion:
                assert cid == 'original'
                assert db.execute('SELECT updated_at FROM companions').fetchone()[0] == stamp
