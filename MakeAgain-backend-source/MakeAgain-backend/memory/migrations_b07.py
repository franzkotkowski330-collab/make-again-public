"""Additive SQLite migration; keep all old rows and original diary text/IDs.

The small tables with old user-only unique constraints are rebuilt transactionally
with the same columns plus companion-aware constraints. Unattributable preexisting
rows remain NULL (never guessed into a later companion).
"""
from datetime import timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from sqlalchemy import text, inspect, select
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm.attributes import flag_modified
from memory import models as m


def migrate(engine, session_factory):
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('B07 migrations currently support SQLite only')
    from memory.db import Base
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c['name'] for c in inspect(conn).get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    default = ''
                    if col.server_default is not None:
                        default = ' DEFAULT ' + str(col.server_default.arg.compile(compile_kwargs={'literal_binds':True})) if hasattr(col.server_default.arg,'compile') else " DEFAULT '" + str(col.server_default.arg) + "'"
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}{default}'))
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:index.create(conn,checkfirst=True)
        # Replace only obsolete uniqueness, without dropping any row.
        for name in ['daily_chat_summaries','nudge_logs','daily_picks','treehole_seen','state_snapshots','weekly_reports']:
            uniques = inspect(conn).get_unique_constraints(name)
            if not any('companion_id' not in u['column_names'] for u in uniques):
                continue
            table = Base.metadata.tables[name]
            temp_name = name + '_b07_new'
            ddl = str(CreateTable(table).compile(engine)).replace('CREATE TABLE '+name, 'CREATE TABLE '+temp_name,1)
            conn.execute(text(ddl))
            cols = ','.join('"'+c.name+'"' for c in table.columns)
            conn.execute(text(f'INSERT INTO {temp_name} ({cols}) SELECT {cols} FROM {name}'))
            conn.execute(text(f'DROP TABLE {name}'))
            conn.execute(text(f'ALTER TABLE {temp_name} RENAME TO {name}'))
            for idx in table.indexes:
                idx.create(conn, checkfirst=True)
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"))
    from memory.db import _migrate_companions
    _migrate_companions()
    with session_factory() as db:
        done = db.execute(text("SELECT 1 FROM schema_migrations WHERE version='B07'" )).scalar()
        if done:
            return
        for u in db.scalars(select(m.User)):
            u.role = u.role or 'user'; u.account_status = u.account_status or 'active'
            if u.membership == 'member' and not u.membership_source:
                u.membership_source = 'legacy'
        for c in db.scalars(select(m.Companion)):
            c.revision = c.revision or 1
            c.chat_session_id = c.chat_session_id or uuid4().hex
            # Metadata backfills must not look like a new user edit.
            flag_modified(c, 'updated_at')
        # Assign by timestamps to existing intervals, not the latest current ID.
        from memory.scope import scoped_models
        for model in scoped_models() + [m.TreeholeLetter]:
            for row in db.scalars(select(model).where(model.companion_id.is_(None))):
                uid = getattr(row,'user_id',None) or getattr(row,'author_user_id',None)
                ts = getattr(row,'ts',None) or getattr(row,'created_at',None) or getattr(row,'updated_at',None) or getattr(row,'seen_at',None) or getattr(row,'last_seen',None)
                cs = list(db.scalars(select(m.Companion).where(m.Companion.user_id==uid).order_by(m.Companion.started_at)))
                candidates = [c for c in cs if ts and c.started_at<=ts and (not c.archived_at or ts<=c.archived_at)]
                if len(candidates)==1:
                    row.companion_id = candidates[0].id
                elif len(cs)==1:
                    row.companion_id = cs[0].id
                if row.companion_id is not None and hasattr(row, 'updated_at'):
                    flag_modified(row, 'updated_at')
        groups = {}
        for n in db.scalars(select(m.DiaryNote).order_by(m.DiaryNote.ts,m.DiaryNote.id)):
            n.entry_date = n.entry_date or n.ts.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
            n.timezone = n.timezone or 'Asia/Shanghai'; n.revision = n.revision or 1
            n.updated_at = n.updated_at or n.ts
            groups.setdefault((n.user_id,n.companion_id,n.entry_date),[]).append(n)
        for rows in groups.values():
            canonical=rows[-1].id
            for row in rows:
                row.canonical_diary_id=canonical; row.is_current=row.id==canonical
        db.commit()
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_diary_current_b07 ON diary_notes(user_id, companion_id, entry_date) WHERE is_current=1"))
        db.execute(text("INSERT INTO schema_migrations(version,applied_at) VALUES ('B07',datetime('now'))"))
        db.commit()
