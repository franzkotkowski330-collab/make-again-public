"""存储层:用户/画像/记忆/情绪节点/访谈会话的读写。"""
from __future__ import annotations

import copy
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import get_settings
from memory.models import (
    ActivationRedemption, AuthSession, ChatMessage, Companion, DailyChatSummary, DailyPick, DiaryNote, EchoEntry,
    EmotionNode, FeedbackEntry, InterviewSession, ItemMemory, MemoryEntry, NudgeLog, OnboardingState, PhotoMemory,
    Portrait, Report, StateSnapshot, TodoItem, TreeholeLetter, TreeholeReply, TreeholeSeen, User, UserAuth, UserState,
    WeeklyReport, utcnow,
)


# 陪伴类型口径:新真源为 breakup/pet/relative;旧 users.loss_type 用 loved_one 表达"亲人",统一映射为 relative。
RELATIONSHIP_TYPES = ("breakup", "pet", "relative")
_LEGACY_LOSS_TO_RELATIONSHIP = {"breakup": "breakup", "loved_one": "relative", "pet": "pet"}


def normalize_relationship_type(value: str | None) -> str | None:
    """把任意口径(新/旧)归一化为 breakup/pet/relative;无法识别返回 None。"""
    if value is None:
        return None
    v = (value or "").strip().lower()
    if v in RELATIONSHIP_TYPES:
        return v
    return _LEGACY_LOSS_TO_RELATIONSHIP.get(v)


# 新陪伴类型(breakup/pet/relative) → 旧 loss_type 词汇(breakup/loved_one/pet),与 _LEGACY_LOSS_TO_RELATIONSHIP 互逆。
RELATIONSHIP_TO_LOSS_TYPE = {"breakup": "breakup", "pet": "pet", "relative": "loved_one"}


def relationship_to_loss_type(relationship_type: str | None) -> str | None:
    """把真源陪伴类型映射回旧 loss_type 词汇;无法识别返回 None。"""
    if relationship_type is None:
        return None
    return RELATIONSHIP_TO_LOSS_TYPE.get((relationship_type or "").strip().lower())


def effective_loss_type(db: Session, user_id: str) -> str | None:
    """用户的有效 loss_type:以当前陪伴对象(companions.relationship_type 真源)映射优先,
    回落遗留 user_states.relationship_type / users.loss_type。

    树洞/每日主题/软引导/语气等消费方统一从这里取,避免读到 None 导致失去类型相关的个性化失效。
    """
    companion = get_current_companion(db, user_id)
    if companion is not None:
        mapped = relationship_to_loss_type(companion.relationship_type)
        if mapped is not None:
            return mapped
    state = db.get(UserState, user_id)
    mapped = relationship_to_loss_type(state.relationship_type if state else None)
    if mapped is not None:
        return mapped
    user = db.get(User, user_id)
    return user.loss_type if user else None


def get_or_create_user(db: Session, user_id: str, loss_type: str | None = None, goal: str | None = None) -> User:
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, loss_type=loss_type, goal=goal)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_user(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def update_user(db: Session, user_id: str, **fields) -> User:
    """部分更新 users 字段(白名单由路由层校验);无则新建。"""
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id)
        db.add(user)
    for k, v in fields.items():
        setattr(user, k, v)
    db.commit()
    db.refresh(user)
    return user


def redeem_activation(db: Session, user_id: str, code: str, valid_codes: list[str]) -> str | None:
    """兑换激活码:code 无效返回 "invalid";已被使用返回 "used";成功置会员并返回 None。"""
    code = (code or "").strip()
    if code not in valid_codes:
        return "invalid"
    if db.get(ActivationRedemption, code) is not None:
        return "used"
    db.add(ActivationRedemption(code=code, used_by=user_id))
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id)
        db.add(user)
    user.membership = "member"
    db.commit()
    return None


# ---- 鉴权:用户名密码 + 会话 token ----

def register_user(db: Session, *, user_id: str, username: str,
                  password_hash: str, password_salt: str) -> UserAuth:
    """注册:建 User + UserAuth(用户名唯一)。username 已由路由层归一化为小写。"""
    db.add(User(id=user_id))
    auth = UserAuth(user_id=user_id, username=username,
                    password_hash=password_hash, password_salt=password_salt)
    db.add(auth)
    db.commit()
    db.refresh(auth)
    return auth


def get_auth_by_username(db: Session, username: str) -> UserAuth | None:
    return db.execute(
        select(UserAuth).where(UserAuth.username == username)
    ).scalar_one_or_none()


def get_auth_by_user(db: Session, user_id: str) -> UserAuth | None:
    return db.get(UserAuth, user_id)


def create_session(db: Session, user_id: str, token: str, expires_at: datetime | None) -> AuthSession:
    sess = AuthSession(token=token, user_id=user_id, expires_at=expires_at)
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def get_session_user(db: Session, token: str) -> str | None:
    """校验会话存在且未过期,返回 user_id;无效返回 None。"""
    sess = db.get(AuthSession, token)
    if sess is None:
        return None
    if sess.expires_at is not None and sess.expires_at < utcnow():
        return None
    return sess.user_id


def delete_session(db: Session, token: str) -> None:
    sess = db.get(AuthSession, token)
    if sess is not None:
        db.delete(sess)
        db.commit()


def get_portrait(db: Session, user_id: str, kind: str) -> dict:
    row = db.execute(
        select(Portrait)
        .where(Portrait.user_id == user_id, Portrait.kind == kind)
        .order_by(Portrait.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.content if row else {}


def upsert_portrait(db: Session, user_id: str, kind: str, content: dict, status: str = "draft") -> Portrait:
    row = db.execute(
        select(Portrait)
        .where(Portrait.user_id == user_id, Portrait.kind == kind)
        .order_by(Portrait.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        row = Portrait(user_id=user_id, kind=kind, content=content, status=status, version=1)
        db.add(row)
    else:
        row.content = content
        row.status = status
        row.version += 1
    db.commit()
    db.refresh(row)
    return row


def add_memory(db: Session, user_id: str, *, type: str = "chat", content: str,
               summary: str | None = None, facts: list | None = None,
               emotion: dict | None = None, importance: float = 5.0,
               place_tag: str | None = None, time_tag: str | None = None,
               cbt: dict | None = None) -> MemoryEntry:
    entry = MemoryEntry(
        user_id=user_id, type=type, content=content, summary=summary,
        facts=facts or [], emotion=emotion, importance=importance,
        place_tag=place_tag, time_tag=time_tag, cbt=cbt,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_memories(db: Session, user_id: str, since=None) -> list[MemoryEntry]:
    q = select(MemoryEntry).where(MemoryEntry.user_id == user_id)
    if since is not None:
        q = q.where(MemoryEntry.ts >= since)
    return db.execute(q.order_by(MemoryEntry.ts.desc())).scalars().all()


def list_memories_by_tag(db: Session, user_id: str, time_tag: str, limit: int = 3) -> list[MemoryEntry]:
    return db.execute(
        select(MemoryEntry)
        .where(MemoryEntry.user_id == user_id, MemoryEntry.time_tag == time_tag)
        .order_by(MemoryEntry.ts.desc())
        .limit(limit)
    ).scalars().all()


def clear_emotion_nodes(db: Session, user_id: str) -> None:
    for node in db.execute(select(EmotionNode).where(EmotionNode.user_id == user_id)).scalars().all():
        db.delete(node)
    db.commit()


def list_emotion_nodes(db: Session, user_id: str) -> list[EmotionNode]:
    return db.execute(
        select(EmotionNode).where(EmotionNode.user_id == user_id).order_by(EmotionNode.frequency.desc())
    ).scalars().all()


# ---- 软引导去重 ----

def nudge_seen(db: Session, user_id: str, rule_key: str, date_key: str) -> bool:
    row = db.execute(
        select(NudgeLog).where(
            NudgeLog.user_id == user_id, NudgeLog.rule_key == rule_key, NudgeLog.date_key == date_key
        )
    ).scalar_one_or_none()
    return row is not None


def mark_nudge(db: Session, user_id: str, rule_key: str, date_key: str) -> None:
    db.add(NudgeLog(user_id=user_id, rule_key=rule_key, date_key=date_key))
    db.commit()


# ---- 每日主题 ----

def upsert_daily_pick(db: Session, user_id: str, date_key: str,
                      theme_key: str, theme_title: str, opening: str) -> DailyPick:
    row = db.execute(
        select(DailyPick).where(DailyPick.user_id == user_id, DailyPick.date_key == date_key)
    ).scalar_one_or_none()
    if row is None:
        row = DailyPick(user_id=user_id, date_key=date_key, theme_key=theme_key,
                        theme_title=theme_title, opening=opening)
        db.add(row)
    else:
        row.theme_key = theme_key
        row.theme_title = theme_title
        row.opening = opening
    db.commit()
    db.refresh(row)
    return row


def get_daily_pick(db: Session, user_id: str, date_key: str) -> DailyPick | None:
    return db.execute(
        select(DailyPick).where(DailyPick.user_id == user_id, DailyPick.date_key == date_key)
    ).scalar_one_or_none()


def upsert_theme_guide(db: Session, user_id: str, date_key: str,
                       theme_key: str, theme_title: str, guide: str) -> DailyPick:
    """写/覆盖当日主题的个性化开场引导(独立于 opening,互不覆盖)。"""
    row = db.execute(
        select(DailyPick).where(DailyPick.user_id == user_id, DailyPick.date_key == date_key)
    ).scalar_one_or_none()
    if row is None:
        row = DailyPick(user_id=user_id, date_key=date_key, theme_key=theme_key,
                        theme_title=theme_title, opening="", theme_guide=guide)
        db.add(row)
    else:
        row.theme_key = theme_key
        row.theme_title = theme_title
        row.theme_guide = guide
    db.commit()
    db.refresh(row)
    return row


def get_theme_guide(db: Session, user_id: str, date_key: str) -> str:
    row = db.execute(
        select(DailyPick).where(DailyPick.user_id == user_id, DailyPick.date_key == date_key)
    ).scalar_one_or_none()
    return (row.theme_guide or "") if row is not None else ""


def list_daily_picks(db: Session, user_id: str) -> list[DailyPick]:
    return db.execute(
        select(DailyPick).where(DailyPick.user_id == user_id).order_by(DailyPick.ts.desc())
    ).scalars().all()


def count_nudges(db: Session, user_id: str) -> int:
    rows = db.execute(select(NudgeLog).where(NudgeLog.user_id == user_id)).scalars().all()
    return len(rows)


def count_replies_delivered(db: Session, user_id: str) -> int:
    rows = db.execute(
        select(TreeholeReply).where(
            TreeholeReply.author_user_id == user_id, TreeholeReply.status == "delivered"
        )
    ).scalars().all()
    return len(rows)


# ---- 访谈会话 ----

def create_interview(db: Session, user_id: str, loss_type: str | None, state: dict) -> InterviewSession:
    session = InterviewSession(user_id=user_id, loss_type=loss_type, state=state)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_interview(db: Session, session_id: str) -> InterviewSession | None:
    return db.get(InterviewSession, session_id)


def update_interview(db: Session, session: InterviewSession,
                     state: dict | None = None, status: str | None = None) -> InterviewSession:
    # 深拷贝:避免"原地改同一 dict 再赋回"导致 SQLAlchemy JSON 列不触发变更检测
    if state is not None:
        session.state = copy.deepcopy(state)
    if status is not None:
        session.status = status
    db.commit()
    db.refresh(session)
    return session


# ---- 树洞信箱 ----

def add_letter(db: Session, user_id: str, *, content: str, summary: str | None,
               loss_type: str | None, emotion: str | None, tags: list | None) -> TreeholeLetter:
    letter = TreeholeLetter(
        author_user_id=user_id, content=content, summary=summary,
        loss_type=loss_type, emotion=emotion, tags=tags or [],
    )
    db.add(letter)
    db.commit()
    db.refresh(letter)
    return letter


def list_published_letters(db: Session) -> list[TreeholeLetter]:
    from api.community import blocked
    rows=db.scalars(select(TreeholeLetter).where(TreeholeLetter.status=="published")).all()
    user_id=db.info.get("user_id")
    return [r for r in rows if not user_id or not blocked(db,user_id,r.author_user_id)]


def list_all_letters(db: Session) -> list[TreeholeLetter]:
    """全部来信(运营后台用),按时间倒序。"""
    return db.execute(
        select(TreeholeLetter).order_by(TreeholeLetter.created_at.desc())
    ).scalars().all()


def list_letters_by_author(db: Session, user_id: str, since=None) -> list[TreeholeLetter]:
    q = select(TreeholeLetter).where(TreeholeLetter.author_user_id == user_id)
    if db.info.get("companion_id"):
        q = q.where(TreeholeLetter.companion_id == db.info["companion_id"])
    if since is not None:
        q = q.where(TreeholeLetter.created_at >= since)
    return db.execute(q.order_by(TreeholeLetter.created_at.desc())).scalars().all()


def get_letter(db: Session, letter_id: str) -> TreeholeLetter | None:
    return db.get(TreeholeLetter, letter_id)


def add_reply(db: Session, letter_id: str, author_user_id: str, content: str) -> TreeholeReply:
    reply = TreeholeReply(letter_id=letter_id, author_user_id=author_user_id, content=content)
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return reply


def get_reply(db: Session, reply_id: str) -> TreeholeReply | None:
    return db.get(TreeholeReply, reply_id)


def list_pending_replies(db: Session) -> list[TreeholeReply]:
    return db.execute(
        select(TreeholeReply).where(TreeholeReply.status == "pending_review")
        .order_by(TreeholeReply.created_at.asc())
    ).scalars().all()


def update_reply_status(db: Session, reply_id: str, status: str) -> TreeholeReply:
    reply = db.get(TreeholeReply, reply_id)
    if reply is None:
        raise ValueError("reply not found")
    reply.status = status
    reply.reviewed_at = utcnow()
    db.commit()
    db.refresh(reply)
    return reply


def list_replies_by_letter(db: Session, letter_id: str) -> list[TreeholeReply]:
    """某封信收到的、已送达的回信(按送达时间倒序)。"""
    return db.execute(
        select(TreeholeReply)
        .where(TreeholeReply.letter_id == letter_id, TreeholeReply.status == "delivered")
        .order_by(TreeholeReply.reviewed_at.desc())
    ).scalars().all()


def count_active_replies(db: Session, letter_id: str) -> int:
    """来信「已有回信」数:待审核 + 已送达(被驳回的不算),供运营判断是否还需回信、避免重复。"""
    return len(db.execute(
        select(TreeholeReply)
        .where(TreeholeReply.letter_id == letter_id,
               TreeholeReply.status.in_(["pending_review", "delivered"]))
    ).scalars().all())


def list_delivered_replies_to_author(db: Session, user_id: str) -> list[TreeholeReply]:
    q=select(TreeholeReply).join(TreeholeLetter,TreeholeReply.letter_id==TreeholeLetter.id).where(TreeholeLetter.author_user_id==user_id,TreeholeReply.status=="delivered")
    if db.info.get("companion_id"):q=q.where(TreeholeLetter.companion_id==db.info["companion_id"])
    return db.scalars(q.order_by(TreeholeReply.reviewed_at.desc())).all()


def list_replies_by_author(db: Session, user_id: str) -> list[TreeholeReply]:
    """该用户写给他人的所有回信(含审核状态,按时间倒序)。"""
    return db.execute(
        select(TreeholeReply)
        .where(TreeholeReply.author_user_id == user_id)
        .order_by(TreeholeReply.created_at.desc())
    ).scalars().all()


def treehole_seen(db: Session, user_id: str, kind: str, ref_id: str | None = None) -> bool:
    return db.execute(
        select(TreeholeSeen).where(
            TreeholeSeen.user_id == user_id,
            TreeholeSeen.kind == kind,
            TreeholeSeen.ref_id == ref_id,  # None → IS NULL
        )
    ).scalar_one_or_none() is not None


def mark_treehole_seen(db: Session, user_id: str, kind: str, ref_id: str | None = None) -> TreeholeSeen:
    existing = db.execute(
        select(TreeholeSeen).where(
            TreeholeSeen.user_id == user_id,
            TreeholeSeen.kind == kind,
            TreeholeSeen.ref_id == ref_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = TreeholeSeen(user_id=user_id, kind=kind, ref_id=ref_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---- 状态快照 ----

def upsert_state_snapshot(db: Session, user_id: str, date_key: str, state: dict) -> StateSnapshot:
    row = db.execute(
        select(StateSnapshot).where(StateSnapshot.user_id == user_id, StateSnapshot.date_key == date_key)
    ).scalar_one_or_none()
    if row is None:
        row = StateSnapshot(user_id=user_id, date_key=date_key)
        db.add(row)
    for k in ("stage", "stage_label", "baseline", "trend", "volatility",
              "acute_ratio", "calm_ratio", "reconcile", "risk", "n_days", "n_memories"):
        setattr(row, k, state.get(k))
    db.commit()
    db.refresh(row)
    return row


def get_previous_snapshot(db: Session, user_id: str, date_key: str) -> StateSnapshot | None:
    """严格早于 date_key 的最近一份快照(用于"对比上次")。"""
    return db.execute(
        select(StateSnapshot).where(StateSnapshot.user_id == user_id, StateSnapshot.date_key < date_key)
        .order_by(StateSnapshot.date_key.desc())
        .limit(1)
    ).scalar_one_or_none()


# ---- 物品纪念/寄存 ----

def add_item_memory(db: Session, user_id: str, *, item_name: str, intent: str, description: str,
                    label: str | None, original_key: str, cutout_key: str,
                    companion_id: str | None = None) -> ItemMemory:
    row = ItemMemory(
        user_id=user_id, companion_id=_resolve_companion_id(db, user_id, companion_id),
        item_name=item_name, intent=intent, description=description,
        label=label, original_key=original_key, cutout_key=cutout_key,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_item_memories(db: Session, user_id: str, companion_id: str | None = None) -> list[ItemMemory]:
    cid = _resolve_companion_id(db, user_id, companion_id)
    if cid is None:
        return []
    return db.execute(
        select(ItemMemory).where(ItemMemory.user_id == user_id, ItemMemory.companion_id == cid).order_by(ItemMemory.ts.desc())
    ).scalars().all()


def get_item_memory(db: Session, item_id: str) -> ItemMemory | None:
    return db.get(ItemMemory, item_id)


# ---- 场景照片(拍立得) ----

def add_photo_memory(db: Session, user_id: str, *, title: str, description: str,
                     photo_key: str, companion_id: str | None = None) -> PhotoMemory:
    row = PhotoMemory(user_id=user_id, companion_id=_resolve_companion_id(db, user_id, companion_id),
                      title=title, description=description, photo_key=photo_key)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_photo_memories(db: Session, user_id: str, companion_id: str | None = None) -> list[PhotoMemory]:
    cid = _resolve_companion_id(db, user_id, companion_id)
    if cid is None:
        return []
    return db.execute(
        select(PhotoMemory).where(PhotoMemory.user_id == user_id, PhotoMemory.companion_id == cid).order_by(PhotoMemory.ts.desc())
    ).scalars().all()


def has_photo(db: Session, user_id: str, title: str) -> bool:
    """该照片是否已上传过(标题归一化去重),用于「已放过就不再问」。"""
    key = _norm_item_name(title)
    if not key:
        return False
    for row in list_photo_memories(db, user_id):
        k = _norm_item_name(row.title)
        if not k:
            continue
        if key == k or key in k or k in key:
            return True
    return False


# ---- 待办清单 ----

def add_todo(db: Session, user_id: str, content: str, done: bool = False) -> TodoItem:
    row = TodoItem(user_id=user_id, content=content, done=done)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_todos(db: Session, user_id: str) -> list[TodoItem]:
    return db.execute(
        select(TodoItem).where(TodoItem.user_id == user_id).order_by(TodoItem.ts.desc())
    ).scalars().all()


def mark_todo_done(db: Session, todo_id: str) -> TodoItem | None:
    """勾选完成 → 置 done=True,并写一条 type="todo" 记忆反哺功能恢复信号。

    完成度通过 cbt={"functional_signal": 1.0}、emotion=None 入记忆流,
    让现有 gather_signals 的 _avg("functional_signal") 自动吸收,不改 state.py。
    """
    row = db.get(TodoItem, todo_id)
    if row is None:
        return None
    if row.done: return row
    row.done = True
    row.done_at = utcnow()
    db.add(MemoryEntry(
        user_id=row.user_id, type="todo", content=row.content,
        summary=f"完成待办「{row.content}」", facts=[], emotion=None,
        importance=5.0, cbt={"functional_signal": 1.0},
    ))
    db.commit()
    db.refresh(row)
    return row


# ---- 用户引导阶段 ----

def get_onboarding(db: Session, user_id: str) -> OnboardingState | None:
    return db.get(OnboardingState, user_id)


def upsert_onboarding(db: Session, user_id: str, phase: str) -> OnboardingState:
    row = db.get(OnboardingState, user_id)
    if row is None:
        row = OnboardingState(user_id=user_id, phase=phase)
        db.add(row)
    else:
        row.phase = phase
    db.commit()
    db.refresh(row)
    return row


# ---- 初始报告 ----

def save_report(db: Session, user_id: str, kind: str, content: dict,
                companion_id: str | None = None) -> Report:
    row = Report(user_id=user_id, companion_id=_resolve_companion_id(db, user_id, companion_id),
                 kind=kind, content=content)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_latest_report(db: Session, user_id: str, kind: str = "initial",
                      companion_id: str | None = None) -> dict | None:
    cid = _resolve_companion_id(db, user_id, companion_id)
    if cid is None:
        return None
    row = db.execute(
        select(Report)
        .where(Report.user_id == user_id, Report.kind == kind, Report.companion_id == cid)
        .order_by(Report.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.content if row else None


def get_latest_report_row(db: Session, user_id: str, kind: str = "initial",
                          companion_id: str | None = None) -> Report | None:
    """返回最新报告的整行(含 created_at),供看板展示报告生成日。"""
    cid = _resolve_companion_id(db, user_id, companion_id)
    if cid is None:
        return None
    return db.execute(
        select(Report)
        .where(Report.user_id == user_id, Report.kind == kind, Report.companion_id == cid)
        .order_by(Report.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


# ---- 周报 ----

def get_weekly_report(db: Session, user_id: str, week_key: str) -> WeeklyReport | None:
    return db.execute(
        select(WeeklyReport).where(
            WeeklyReport.user_id == user_id, WeeklyReport.week_key == week_key
        )
    ).scalar_one_or_none()


def save_weekly_report(db: Session, user_id: str, week_key: str, content: dict) -> WeeklyReport:
    """幂等写入本周周报(重复生成时覆盖内容,不动 seen_at)。"""
    row = get_weekly_report(db, user_id, week_key)
    if row is None:
        row = WeeklyReport(user_id=user_id, week_key=week_key, content=content)
        db.add(row)
    else:
        row.content = content
    db.commit()
    db.refresh(row)
    return row


def list_weekly_reports(db: Session, user_id: str) -> list[WeeklyReport]:
    return db.execute(
        select(WeeklyReport).where(WeeklyReport.user_id == user_id)
        .order_by(WeeklyReport.week_key.desc())
    ).scalars().all()


def mark_weekly_report_seen(db: Session, user_id: str, week_key: str) -> WeeklyReport | None:
    row = get_weekly_report(db, user_id, week_key)
    if row is None:
        return None
    row.seen_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


# ---- 访谈会话(恢复) ----

def get_active_interview(db: Session, user_id: str) -> InterviewSession | None:
    """最近一条仍在进行的访谈会话(供中断恢复 / 前端轮询)。"""
    return db.execute(
        select(InterviewSession)
        .where(InterviewSession.user_id == user_id)
        .order_by(InterviewSession.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


# 常见「前缀 + 物品」里的前缀:量词 / 指示代词 / 所有格(他送我的/她给我的…)。
# 按长到短排列,配合多轮剥离,把「他送我的那条围巾」「那条围巾」「围巾」归一到核心词。
_ITEM_NAME_PREFIXES = (
    "他给我买的", "她给我买的", "ta给我买的",
    "他送给我的", "她送给我的", "ta送给我的",
    "他送我的", "她送我的", "ta送我的",
    "他送给", "她送给", "ta送给",
    "他送的", "她送的", "ta送的",
    "他给我的", "她给我的", "ta给我的",
    "他买的", "她买的", "ta买的",
    "他给的", "她给的", "ta给的",
    "他留下的", "她留下的", "ta留下的",
    "他留给我", "她留给我", "ta留给我",
    "送给我的", "送我的", "给我的", "留给我的", "买给我的",
    "他的", "她的", "它的", "ta的",
    "这个", "那个", "一个", "一件", "这条", "那条", "那一条", "这只", "那件", "这件",
    "我的", "我那个",
)


def _norm_item_name(name: str) -> str:
    """归一化物品名:去空白/量词与所有格前缀 + 小写,便于去重匹配。

    多轮剥离,支持「他送我的那条围巾」这类复合前缀,最终落到核心词,
    让「他送的笔袋」「他送我的笔袋」「笔袋」互相命中,避免重复邀请。
    """
    s = (name or "").strip().lower()
    changed = True
    while changed:
        changed = False
        for pre in _ITEM_NAME_PREFIXES:
            if s.startswith(pre):
                s = s[len(pre):].lstrip()
                changed = True
                break
    return "".join(s.split())


def has_item_story(db: Session, user_id: str, item_name: str) -> bool:
    """该物品是否已讲过(已上传过 / 记忆流里提过),用于「已讲过就不再问」。"""
    key = _norm_item_name(item_name)
    if not key:
        return False
    for row in list_item_memories(db, user_id):
        k = _norm_item_name(row.item_name)
        if not k:
            continue
        if key == k or key in k or k in key:
            return True
    for m in list_memories(db, user_id):
        text = f"{m.summary or ''} {m.content or ''}".lower()
        if len(key) >= 2 and key in text:
            return True
    return False


# ---- 聊天历史(精确到分钟) ----

def save_chat_turn(db: Session, user_id: str, session_id: str | None,
                   user_text: str, assistant_text: str) -> None:
    """落库一轮对话(用户消息 + AI 回复两条),一次 commit,保证历史立即可见。

    两条共享同一 ts(同一分钟),展示顺序由自增 id 单调性保证(user 在前)。
    """
    now = utcnow()
    sid = session_id or ""
    db.add(ChatMessage(user_id=user_id, session_id=sid, role="user", content=user_text, ts=now))
    db.add(ChatMessage(user_id=user_id, session_id=sid, role="assistant", content=assistant_text, ts=now))
    db.commit()


def count_chat_sessions(db: Session, user_id: str) -> int:
    """用户参与过的聊天会话数(每次进入聊天 = 一个 session_id,有消息即算),用于树洞写信门槛。"""
    return db.execute(
        select(func.count(func.distinct(ChatMessage.session_id)))
        .where(ChatMessage.user_id == user_id, ChatMessage.session_id != "")
    ).scalar_one() or 0


def count_completed_chat_turns(db: Session, user_id: str) -> int:
    """Count real persisted user/assistant pairs, independent of session ID churn."""
    from sqlalchemy.orm import aliased
    response=aliased(ChatMessage)
    pair=select(response.id).where(response.user_id==ChatMessage.user_id,
        response.companion_id==ChatMessage.companion_id,response.session_id==ChatMessage.session_id,
        response.ts==ChatMessage.ts,response.role=="assistant",response.id>ChatMessage.id).exists()
    return db.scalar(select(func.count(ChatMessage.id)).where(ChatMessage.user_id==user_id,
        ChatMessage.companion_id==db.info.get("companion_id"),ChatMessage.role=="user",pair)) or 0


def list_chat_page(db: Session, user_id: str, *, before_id: int | None = None,
                   after_id: int | None = None, start_id: int | None = None,
                   limit: int = 30) -> list[ChatMessage]:
    """返回升序(旧→新)的一页。before_id/after_id/start_id 三选一,都不传=最新一页。

    - before_id: 取 id < before_id 的消息(上滑加载更早),按 id 降序取最近 limit 条后转升序。
    - after_id:  取 id > after_id 的消息(下滑加载更新),升序。
    - start_id:  取 id >= start_id 的消息(跳转到某天第一条后向下翻),升序。
    """
    if before_id is not None:
        rows = db.execute(
            select(ChatMessage).where(ChatMessage.user_id == user_id, ChatMessage.id < before_id)
            .order_by(ChatMessage.id.desc()).limit(limit)
        ).scalars().all()
        return list(reversed(rows))
    if after_id is not None:
        return db.execute(
            select(ChatMessage).where(ChatMessage.user_id == user_id, ChatMessage.id > after_id)
            .order_by(ChatMessage.id.asc()).limit(limit)
        ).scalars().all()
    if start_id is not None:
        return db.execute(
            select(ChatMessage).where(ChatMessage.user_id == user_id, ChatMessage.id >= start_id)
            .order_by(ChatMessage.id.asc()).limit(limit)
        ).scalars().all()
    rows = db.execute(
        select(ChatMessage).where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.id.desc()).limit(limit)
    ).scalars().all()
    return list(reversed(rows))


def first_chat_id_on_date(db: Session, user_id: str, day_start_utc, day_end_utc) -> int | None:
    """某本地日 [start,end) 内第一条消息的 id;无则 None。"""
    return db.execute(
        select(ChatMessage.id).where(
            ChatMessage.user_id == user_id,
            ChatMessage.ts >= day_start_utc, ChatMessage.ts < day_end_utc,
        ).order_by(ChatMessage.id.asc()).limit(1)
    ).scalar_one_or_none()


def first_chat_id_since(db: Session, user_id: str, ts_ge) -> int | None:
    """ts >= ts_ge 的第一条消息 id(用于「该天无消息时落到其后最近一天」)。"""
    return db.execute(
        select(ChatMessage.id).where(ChatMessage.user_id == user_id, ChatMessage.ts >= ts_ge)
        .order_by(ChatMessage.id.asc()).limit(1)
    ).scalar_one_or_none()


def chat_has_before(db: Session, user_id: str, message_id: int) -> bool:
    """是否还有比 message_id 更早的消息(上滑加载是否可用)。"""
    return db.execute(
        select(ChatMessage.id).where(ChatMessage.user_id == user_id, ChatMessage.id < message_id)
        .limit(1)
    ).scalar_one_or_none() is not None


def chat_has_after(db: Session, user_id: str, message_id: int) -> bool:
    """是否还有比 message_id 更新的消息(下滑加载是否可用)。"""
    return db.execute(
        select(ChatMessage.id).where(ChatMessage.user_id == user_id, ChatMessage.id > message_id)
        .limit(1)
    ).scalar_one_or_none() is not None


def delete_chat_messages(db: Session, user_id: str, message_ids: list[int]) -> int:
    """批量删除该用户自己的聊天消息(越权 id 自然被 user_id 过滤),返回实际删除条数。"""
    rows = db.execute(
        select(ChatMessage).where(ChatMessage.user_id == user_id, ChatMessage.id.in_(message_ids))
    ).scalars().all()
    for r in rows:
        db.delete(r)
    db.commit()
    return len(rows)


# ---- 每日聊天总结 ----

def upsert_daily_chat_summary(db: Session, user_id: str, date_key: str,
                              summary: str, status: str) -> DailyChatSummary:
    """按 (user_id, date_key) 覆盖写总结(草稿会被当天更完整的总结覆盖;final 亦幂等覆盖)。"""
    row = db.execute(
        select(DailyChatSummary).where(
            DailyChatSummary.user_id == user_id, DailyChatSummary.date_key == date_key
        )
    ).scalar_one_or_none()
    if row is None:
        row = DailyChatSummary(user_id=user_id, date_key=date_key, summary=summary, status=status)
        db.add(row)
    else:
        row.summary = summary
        row.status = status
    db.commit()
    db.refresh(row)
    return row


def get_daily_chat_summary(db: Session, user_id: str, date_key: str) -> DailyChatSummary | None:
    return db.execute(
        select(DailyChatSummary).where(
            DailyChatSummary.user_id == user_id, DailyChatSummary.date_key == date_key
        )
    ).scalar_one_or_none()


def list_daily_chat_summaries(db: Session, user_id: str) -> list[DailyChatSummary]:
    return db.execute(
        select(DailyChatSummary).where(DailyChatSummary.user_id == user_id)
        .order_by(DailyChatSummary.date_key.desc())
    ).scalars().all()


def _chat_day_expr():
    """本地日表达式:date(ts, '+offset hours'),把 naive UTC 归到本地日。"""
    offset = get_settings().timezone_offset_hours
    mod = f"+{offset} hours" if offset >= 0 else f"{offset} hours"
    return func.date(ChatMessage.ts, mod)


def list_chat_days(db: Session, user_id: str, *, before_date: str | None = None,
                   limit: int | None = None) -> list[tuple[str, int]]:
    """按本地日分组聊天历史,返回 [(date_key, count)],日期倒序。

    before_date(YYYY-MM-DD)只取更早的日期(一级页游标分页);limit 截断条数。
    """
    day = _chat_day_expr()
    q = (
        select(day.label("d"), func.count().label("c"))
        .where(ChatMessage.user_id == user_id)
        .group_by(day)
    )
    if before_date is not None:
        q = q.having(day < before_date)
    q = q.order_by(day.desc())
    if limit is not None:
        q = q.limit(limit)
    rows = db.execute(q).all()
    return [(r[0], r[1]) for r in rows]


def list_chat_day_messages(db: Session, user_id: str, start_utc, end_utc) -> list[ChatMessage]:
    """某本地日 [start_utc, end_utc) 内的全部消息(升序,供总结拼 transcript)。"""
    return db.execute(
        select(ChatMessage).where(
            ChatMessage.user_id == user_id,
            ChatMessage.ts >= start_utc, ChatMessage.ts < end_utc,
        ).order_by(ChatMessage.id.asc())
    ).scalars().all()


def list_chat_day_page(db: Session, user_id: str, start_utc, end_utc,
                       before_id: int | None = None, after_id: int | None = None,
                       limit: int = 30) -> list[ChatMessage]:
    """当天内升序一页:before_id=上滑更早、after_id=下滑更新、都不传=当天最新一页。"""
    if before_id is not None:
        rows = db.execute(
            select(ChatMessage).where(
                ChatMessage.user_id == user_id,
                ChatMessage.ts >= start_utc, ChatMessage.ts < end_utc,
                ChatMessage.id < before_id,
            ).order_by(ChatMessage.id.desc()).limit(limit)
        ).scalars().all()
        return list(reversed(rows))
    if after_id is not None:
        return db.execute(
            select(ChatMessage).where(
                ChatMessage.user_id == user_id,
                ChatMessage.ts >= start_utc, ChatMessage.ts < end_utc,
                ChatMessage.id > after_id,
            ).order_by(ChatMessage.id.asc()).limit(limit)
        ).scalars().all()
    rows = db.execute(
        select(ChatMessage).where(
            ChatMessage.user_id == user_id,
            ChatMessage.ts >= start_utc, ChatMessage.ts < end_utc,
        ).order_by(ChatMessage.id.desc()).limit(limit)
    ).scalars().all()
    return list(reversed(rows))


def chat_day_has_before(db: Session, user_id: str, start_utc, end_utc, message_id: int) -> bool:
    """当天内是否还有比 message_id 更早的消息(上滑加载是否可用)。"""
    return db.execute(
        select(ChatMessage.id).where(
            ChatMessage.user_id == user_id,
            ChatMessage.ts >= start_utc, ChatMessage.ts < end_utc,
            ChatMessage.id < message_id,
        ).limit(1)
    ).scalar_one_or_none() is not None


def chat_day_has_after(db: Session, user_id: str, start_utc, end_utc, message_id: int) -> bool:
    """当天内是否还有比 message_id 更新的消息(下滑加载是否可用)。"""
    return db.execute(
        select(ChatMessage.id).where(
            ChatMessage.user_id == user_id,
            ChatMessage.ts >= start_utc, ChatMessage.ts < end_utc,
            ChatMessage.id > message_id,
        ).limit(1)
    ).scalar_one_or_none() is not None


# ---- 单用户轻量状态(user_states) ----

def get_user_state(db: Session, user_id: str) -> UserState | None:
    return db.get(UserState, user_id)


def get_or_create_user_state(db: Session, user_id: str) -> UserState:
    """取单用户状态行;不存在则新建(单行,幂等)。"""
    row = db.get(UserState, user_id)
    if row is None:
        row = UserState(user_id=user_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_user_state(db: Session, user_id: str, **fields) -> UserState:
    """部分更新 user_states 字段(白名单由路由层校验);无则新建。"""
    row = db.get(UserState, user_id)
    if row is None:
        row = UserState(user_id=user_id)
        db.add(row)
    for k, v in fields.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


# ---- 陪伴对象(companions):单用户多对象 ----

def get_companion(db: Session, companion_id: str) -> Companion | None:
    return db.get(Companion, companion_id)


def get_current_companion(db: Session, user_id: str) -> Companion | None:
    """取当前活跃陪伴对象:经 user_states.current_companion_id 解析;无指针则返回 None。"""
    if db.info.get("companion_id"):
        scoped = db.get(Companion,db.info["companion_id"])
        if scoped and scoped.user_id == user_id: return scoped
    state = db.get(UserState, user_id)
    if state is None or not state.current_companion_id:
        return None
    return db.get(Companion, state.current_companion_id)


def _resolve_companion_id(db: Session, user_id: str, companion_id: str | None = None) -> str | None:
    """解析内容归属的陪伴 id:显式传入则校验归属,否则取当前陪伴;无则 None。"""
    if companion_id:
        c = db.get(Companion, companion_id)
        return c.id if c is not None and c.user_id == user_id else None
    c = get_current_companion(db, user_id)
    return c.id if c is not None else None


def create_companion(db: Session, user_id: str, relationship_type: str | None = None,
                     subject_name: str | None = None, *, make_current: bool = True) -> Companion:
    """新建陪伴对象;默认置为当前活跃对象(对应前端「开始新的陪伴」)。"""
    state = get_or_create_user_state(db, user_id) if make_current else None
    companion = Companion(user_id=user_id, relationship_type=relationship_type, subject_name=subject_name)
    db.add(companion)
    db.flush()
    if state is not None:
        state.current_companion_id = companion.id
    db.commit()
    db.refresh(companion)
    return companion


def get_or_create_current_companion(db: Session, user_id: str) -> Companion:
    """取当前陪伴对象;不存在则新建空对象并置为当前。"""
    companion = get_current_companion(db, user_id)
    if companion is not None:
        return companion
    return create_companion(db, user_id)


def list_companions(db: Session, user_id: str) -> list[Companion]:
    """该用户全部陪伴对象(按开始时间升序)。"""
    return db.execute(
        select(Companion).where(Companion.user_id == user_id).order_by(Companion.started_at.asc())
    ).scalars().all()


def update_companion(db: Session, companion_id: str, **fields) -> Companion | None:
    """部分更新陪伴对象字段(白名单由路由层校验);不存在返回 None。"""
    companion = db.get(Companion, companion_id)
    if companion is None:
        return None
    for k, v in fields.items():
        setattr(companion, k, v)
    companion.revision += 1
    db.commit()
    db.refresh(companion)
    return companion


def archive_companion(db: Session, companion_id: str, ritual: str) -> Companion | None:
    """结束归档(幂等):置 ending_stage=complete + archived_at;已归档则原样返回。"""
    companion = db.get(Companion, companion_id)
    if companion is None:
        return None
    if companion.archived_at is None:
        companion.revision += 1
        companion.ending_stage = "complete"
        companion.ending_ritual = ritual
        companion.ending_committed_at = utcnow()
        companion.archived_at = utcnow()
        db.commit()
        db.refresh(companion)
    return companion


# ---- 意见反馈 ----

def add_feedback(db: Session, content: str, user_id: str | None = None,
                 contact: str | None = None) -> FeedbackEntry:
    row = FeedbackEntry(content=content, user_id=user_id, contact=contact)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_feedback(db: Session) -> list[FeedbackEntry]:
    """运营后台:全部意见反馈,按提交时间倒序。"""
    return db.execute(
        select(FeedbackEntry).order_by(FeedbackEntry.created_at.desc())
    ).scalars().all()


def update_feedback(db: Session, feedback_id: str, reply: str | None = None,
                    status: str | None = None) -> FeedbackEntry:
    """运营后台:记录处理回复/更新处理状态。"""
    row = db.get(FeedbackEntry, feedback_id)
    if row is None:
        raise ValueError("feedback not found")
    if reply is not None:
        row.reply = reply
    if status is not None:
        row.status = status
    db.commit()
    db.refresh(row)
    return row


# ---- 日记便利贴(「写下今天」) ----

def add_diary_note(db: Session, user_id: str, content: str, emotion: str | None = None,
                   companion_id: str | None = None) -> DiaryNote:
    row = DiaryNote(user_id=user_id, companion_id=_resolve_companion_id(db, user_id, companion_id),
                    content=content, emotion=emotion)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_diary_notes(db: Session, user_id: str, companion_id: str | None = None) -> list[DiaryNote]:
    cid = _resolve_companion_id(db, user_id, companion_id)
    if cid is None:
        return []
    return db.execute(
        select(DiaryNote).where(DiaryNote.user_id == user_id, DiaryNote.companion_id == cid).order_by(DiaryNote.ts.desc())
    ).scalars().all()


# ---- 回声壁 ----

def add_echo(db: Session, user_id: str, content: str, emotion: str | None,
             reveal_at: datetime, source: str = "chat") -> EchoEntry:
    row = EchoEntry(user_id=user_id, content=content, emotion=emotion, source=source,
                    reveal_at=reveal_at)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_due_echos(db: Session, user_id: str, now: datetime | None = None) -> list[EchoEntry]:
    """待回显:status=pending 且 reveal_at 已到期,按到期时间从早到晚。"""
    now = now or utcnow()
    return db.execute(
        select(EchoEntry).where(
            EchoEntry.user_id == user_id,
            EchoEntry.status == "pending",
            EchoEntry.reveal_at <= now,
        ).order_by(EchoEntry.reveal_at.asc())
    ).scalars().all()


def count_pending_echos(db: Session, user_id: str) -> int:
    rows = db.execute(
        select(EchoEntry).where(EchoEntry.user_id == user_id, EchoEntry.status == "pending")
    ).scalars().all()
    return len(rows)


def mark_echo_status(db: Session, echo_id: str, status: str) -> bool:
    """置回显状态(delivered / dismissed);不存在的 id 返回 False。

    delivered_at 记录「这条回声被展示/处理」的时间(已看或划掉都算),供推送节流使用。
    """
    row = db.get(EchoEntry, echo_id)
    if row is None:
        return False
    row.status = status
    if status in ("delivered", "dismissed"):
        row.delivered_at = utcnow()
    db.commit()
    return True


def last_echo_surfaced_at(db: Session, user_id: str) -> datetime | None:
    """最近一次回声被展示/处理(已看或划掉)的时间,用于推送节流:echo_push_interval_hours 内不再推下一条。"""
    return db.execute(
        select(EchoEntry.delivered_at).where(
            EchoEntry.user_id == user_id,
            EchoEntry.status.in_(["delivered", "dismissed"]),
            EchoEntry.delivered_at.isnot(None),
        ).order_by(EchoEntry.delivered_at.desc()).limit(1)
    ).scalar_one_or_none()
