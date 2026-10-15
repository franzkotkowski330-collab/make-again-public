"""数据库引擎与会话。默认 SQLite 零依赖;生产切 Postgres+pgvector。

并发要点(2 核 4G 小服务器,多用户并发):
- 开 WAL + busy_timeout + synchronous=NORMAL:并发读写时避免 "database is locked"。
- 文件型 SQLite 用显式小连接池(读多写少,WAL 下 5 个连接够用),避免每请求开新连接。
- 单进程多线程(uvicorn 单 worker);进程内内存态(session_context)不跨进程,勿开多 worker。
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import QueuePool

from config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_url = _settings.database_url
_connect_args: dict = {}

_is_file_sqlite = False
if _url.startswith("sqlite"):
    db_path = _url.replace("sqlite:///", "", 1)
    if db_path not in (":memory:", ""):
        _is_file_sqlite = True
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    # check_same_thread=False:SQLAlchemy 连接池跨线程复用同一连接;
    # timeout=等待写锁的秒数(配合 WAL,把瞬时锁竞争消化掉而不是直接报 locked)。
    _connect_args = {"check_same_thread": False, "timeout": 5.0}

_engine_kwargs: dict = {"connect_args": _connect_args}
if _is_file_sqlite:
    # 文件型 SQLite 默认即可用 QueuePool,这里显式收敛连接数,避免并发请求各开一堆连接。
    _engine_kwargs.update(poolclass=QueuePool, pool_size=5, max_overflow=10, pool_timeout=30)

engine = create_engine(_url, **_engine_kwargs)
from memory.scope import ManagedSession, current_db
_RawSessionLocal = sessionmaker(bind=engine, class_=ManagedSession, autoflush=False, expire_on_commit=False)

def SessionLocal():
    return current_db.get() or _RawSessionLocal()

if _url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        # WAL:读写互不阻塞(一写多读),多用户同时读写时大幅降低锁竞争;
        # synchronous=NORMAL 在 WAL 下仍是持久性安全的,且少一次 fsync。
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


# 新增列迁移清单:(表名, 列名, SQLite 列定义)。create_all 只建新表、不改旧表列,
# 后续新增字段需在这里幂等补列(仅 SQLite;Postgres 走正式迁移工具)。
_SQLITE_ADD_COLUMNS = {
    ("memory_stream", "cbt"): "JSON",
    ("user_states", "current_companion_id"): "VARCHAR(64)",
    ("daily_picks", "theme_guide"): "TEXT",
    ("todo_items", "done_at"): "DATETIME",
    ("users", "nickname"): "VARCHAR(64)",
    ("users", "membership"): "VARCHAR(16)",
    ("user_states", "tts_speed"): "FLOAT",
    ("user_states", "tts_vol"): "FLOAT",
    ("user_states", "echo_enabled"): "BOOLEAN",
    ("photo_memories", "companion_id"): "VARCHAR(64)",
    ("item_memories", "companion_id"): "VARCHAR(64)",
    ("diary_notes", "companion_id"): "VARCHAR(64)",
    ("reports", "companion_id"): "VARCHAR(64)",
    ("feedback_entries", "status"): "VARCHAR(16)",
    ("feedback_entries", "reply"): "TEXT",
}


def _migrate_sqlite() -> None:
    """为已存在的 SQLite 表补新增列(幂等,重复执行安全)。"""
    if not _url.startswith("sqlite"):
        return
    from sqlalchemy import text
    with engine.begin() as conn:
        for (table, column), coltype in _SQLITE_ADD_COLUMNS.items():
            cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if column not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))


def _migrate_companions() -> None:
    """幂等:为存量 user_states 行生成对应的 companions 行(陪伴身份迁移),回写 current_companion_id。

    仅迁移「有陪伴数据」的老行(relationship_type/subject_name/archived_at/ending 任一非默认),
    保证已归档老用户分流到 018 不变;全新空 user_states 行留给路由层按需 create_companion。
    """
    from sqlalchemy import select
    from memory import models

    _companion_fields = (
        "relationship_type", "relationship_type_source", "relationship_type_confidence",
        "subject_name", "ending_stage", "ending_started_at", "ending_ritual",
        "ending_committed_at", "archived_at",
    )
    db = SessionLocal()
    try:
        for state in db.execute(select(models.UserState)).scalars().all():
            if state.current_companion_id:
                continue  # 已迁移
            if not any(getattr(state, f) not in (None, "", "active") for f in _companion_fields):
                continue  # 无陪伴数据的全新用户,留给按需创建
            companion = models.Companion(
                user_id=state.user_id,
                relationship_type=state.relationship_type,
                relationship_type_source=state.relationship_type_source,
                relationship_type_confidence=state.relationship_type_confidence,
                subject_name=state.subject_name,
                started_at=state.created_at or models.utcnow(),
                ending_stage=state.ending_stage or "active",
                ending_started_at=state.ending_started_at,
                ending_ritual=state.ending_ritual,
                ending_committed_at=state.ending_committed_at,
                archived_at=state.archived_at,
            )
            db.add(companion)
            db.flush()
            state.current_companion_id = companion.id
            # Preserve the original activity time when assigning a legacy companion.
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(state, 'updated_at')
        db.commit()
    finally:
        if not db.info.get("managed"):
            db.close()


def _migrate_companion_content() -> None:
    """幂等:把存量照片/物品/日记/初始报告回填 companion_id(当前陪伴,无则最早一段)。"""
    from sqlalchemy import select
    from memory import models

    _content_tables = (models.PhotoMemory, models.ItemMemory, models.DiaryNote, models.Report)
    db = SessionLocal()
    try:
        for table in _content_tables:
            for row in db.execute(select(table).where(table.companion_id.is_(None))).scalars().all():
                companion = None
                state = db.get(models.UserState, row.user_id)
                if state and state.current_companion_id:
                    candidate = db.get(models.Companion, state.current_companion_id)
                    if candidate is not None and candidate.user_id == row.user_id:
                        companion = candidate
                if companion is None:
                    companion = db.execute(
                        select(models.Companion).where(models.Companion.user_id == row.user_id)
                        .order_by(models.Companion.started_at.asc())
                    ).scalars().first()
                row.companion_id = companion.id if companion else None
        db.commit()
    finally:
        if not db.info.get("managed"):
            db.close()


def init_db() -> None:
    from memory import models, b07_models  # noqa: F401
    Base.metadata.create_all(engine)
    _migrate_sqlite()
    # Add model columns before legacy companion reads.
    from memory.migrations_b07 import migrate
    migrate(engine, _RawSessionLocal)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        if not db.info.get("managed"):
            db.close()
