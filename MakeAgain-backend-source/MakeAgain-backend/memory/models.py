"""ORM 模型:用户、画像、记忆流、情绪节点。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from memory.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    # 存 naive UTC,避免 SQLite 时区坑
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    role: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    account_status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    membership_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    membership_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    loss_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # breakup|loved_one|pet
    goal: Mapped[str | None] = mapped_column(String(256), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 用户昵称(菜单栏问候语展示)
    membership: Mapped[str] = mapped_column(String(16), default="free")        # free|member(激活码兑换,暂不门禁)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CompanionScoped:
    companion_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class Portrait(CompanionScoped, Base):
    __tablename__ = "portraits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # user | object
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|confirmed
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MemoryEntry(CompanionScoped, Base):
    __tablename__ = "memory_stream"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16), default="chat")  # chat|event|report
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    facts: Mapped[list] = mapped_column(JSON, default=list)
    emotion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cbt: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # CBT 抽取:自动思维/认知扭曲/反思就绪/功能恢复信号
    importance: Mapped[float] = mapped_column(Float, default=5.0)
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 数组文本,可移植
    place_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatMessage(CompanionScoped, Base):
    """聊天历史(精确到分钟):每轮用户消息与 AI 回复各存一行,支撑历史记录页。

    id 用自增整型主键(单调递增),作为游标分页(上滑/下滑/跳转)与批量删除的稳定锚点;
    ts 存 naive UTC,展示时按 timezone_offset_hours 转本地时间(精确到分钟)。
    同一轮的用户消息与 AI 回复共享同一 ts,展示顺序由 id 单调性保证(user 先于 assistant)。
    """
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_user_ts", "user_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DailyChatSummary(CompanionScoped, Base):
    """每日聊天总结:当天聊了什么、情绪如何的一句简短记录。

    date_key 为本地日(YYYY-MM-DD);status:
    - draft:当天退出对话时后台生成,会随当天继续聊天而覆盖(进行中)。
    - final:跨天后惰性固定(用户打开记录页时把昨日及之前补成 final),不再变。
    """
    __tablename__ = "daily_chat_summaries"
    __table_args__ = (UniqueConstraint("user_id", "companion_id", "date_key", name="uq_daily_chat_summary"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    date_key: Mapped[str] = mapped_column(String(16))   # YYYY-MM-DD(本地时区)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | final
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EmotionNode(CompanionScoped, Base):
    __tablename__ = "emotion_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(128))
    emotion: Mapped[str] = mapped_column(String(32))
    intensity: Mapped[float] = mapped_column(Float, default=0.5)
    frequency: Mapped[int] = mapped_column(Integer, default=1)
    place: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InterviewSession(CompanionScoped, Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    loss_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|completed
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class NudgeLog(CompanionScoped, Base):
    """软引导触达去重:同一 user + rule_key + date_key 每天只推一次。"""
    __tablename__ = "nudge_logs"
    __table_args__ = (UniqueConstraint("user_id", "companion_id", "rule_key", "date_key", name="uq_nudge_daily"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_key: Mapped[str] = mapped_column(String(128))   # late_night | emotion:<trigger> | anniversary
    date_key: Mapped[str] = mapped_column(String(16))    # YYYY-MM-DD(本地时区)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DailyPick(CompanionScoped, Base):
    """每日主题选取:一天一个选中主题 + AI 生成的启发文案(幂等覆盖)。"""
    __tablename__ = "daily_picks"
    __table_args__ = (UniqueConstraint("user_id", "companion_id", "date_key", name="uq_daily_pick"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    date_key: Mapped[str] = mapped_column(String(16))    # YYYY-MM-DD(本地时区)
    theme_key: Mapped[str] = mapped_column(String(64))
    theme_title: Mapped[str] = mapped_column(String(64))
    opening: Mapped[str] = mapped_column(Text)
    theme_guide: Mapped[str] = mapped_column(Text, default="")  # 当日主题的个性化开场引导(LLM+状态+记忆)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TreeholeLetter(CompanionScoped, Base):
    """树洞信:用户匿名写下痛苦/困惑,沉淀后供相似经历者回信。"""
    __tablename__ = "treehole_letters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    author_user_id: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # 一行经历摘要(用于匹配/展示)
    loss_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 写信时主导情绪
    tags: Mapped[list] = mapped_column(JSON, default=list)                # 经历关键词(如 异地/父母反对/病逝)
    status: Mapped[str] = mapped_column(String(16), default="published")  # published|closed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TreeholeReply(Base):
    """回信:情绪稳定/已和解的用户写给相似经历者,经审核后送达。"""
    __tablename__ = "treehole_replies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    letter_id: Mapped[str] = mapped_column(String(64), index=True)
    author_user_id: Mapped[str] = mapped_column(String(64), index=True)  # 回信者
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending_review")  # pending_review|delivered|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TreeholeSeen(CompanionScoped, Base):
    """树洞一次性弹窗去重:记录用户已看/关闭过的弹窗,避免每次打开都弹。

    kind: write(写信邀请) | reply_invite(回信邀请) | reply_received(收到回信,ref_id=reply_id)
    """
    __tablename__ = "treehole_seen"
    __table_args__ = (UniqueConstraint("user_id", "companion_id", "kind", "ref_id", name="uq_treehole_seen"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # reply_received 填 reply_id
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StateSnapshot(CompanionScoped, Base):
    """状态快照:每次生成报告时落一份,支撑"对比上次"与趋势回溯。"""
    __tablename__ = "state_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "companion_id", "date_key", name="uq_state_snapshot"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    date_key: Mapped[str] = mapped_column(String(16))   # YYYY-MM-DD(本地时区)
    stage: Mapped[int] = mapped_column(Integer, default=0)
    stage_label: Mapped[str] = mapped_column(String(16), default="")
    baseline: Mapped[float] = mapped_column(Float, default=50.0)
    trend: Mapped[float] = mapped_column(Float, default=0.0)
    volatility: Mapped[float] = mapped_column(Float, default=0.0)
    acute_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    calm_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    reconcile: Mapped[float] = mapped_column(Float, default=0.0)
    risk: Mapped[float] = mapped_column(Float, default=0.0)
    n_days: Mapped[int] = mapped_column(Integer, default=0)
    n_memories: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ItemMemory(Base):
    """物品纪念/寄存:用户上传的物品照片(原图 + 抠图)与描述,支撑聊天里的「物品」工具。"""
    __tablename__ = "item_memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    item_name: Mapped[str] = mapped_column(String(128))
    intent: Mapped[str] = mapped_column(String(16))  # keep(纪念) | let_go(寄存)
    description: Mapped[str] = mapped_column(Text)   # 简化后描述
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 视觉识别标签
    original_key: Mapped[str] = mapped_column(String(256))  # 原图 object key(已不保留原图,恒为空串;保留列以免迁移)
    cutout_key: Mapped[str] = mapped_column(String(256))    # 抠图 object key
    client_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(16), default="completed", server_default="completed")
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PhotoMemory(Base):
    """场景照片(拍立得):提到某张具体照片时被邀请上传,整张保留不做抠图。"""
    __tablename__ = "photo_memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(128))      # 标题/场景,如「去年冬天在车站」
    description: Mapped[str] = mapped_column(Text)        # 背后的场景或故事
    photo_key: Mapped[str] = mapped_column(String(256))   # 整张照片的 object key
    client_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(16), default="completed", server_default="completed")
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TodoItem(CompanionScoped, Base):
    """待办清单:AI 建议的 1~3 个可勾选小任务,完成度反哺 functional_signal。"""
    __tablename__ = "todo_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(String(256))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 完成时间,用于「最近做了的 4 件」排序


class OnboardingState(Base):
    """用户引导阶段(轻量状态机):new → interview → report → main。

    新表而非 User 加列,便于 create_all 在存量库上直接建表(免 ALTER 迁移)。
    """
    __tablename__ = "onboarding_states"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    phase: Mapped[str] = mapped_column(String(16), default="new")  # new|interview|report|main
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Report(Base):
    """初始报告落库(看板可随时查看):存完整报告 JSON,展示时裁剪成「用户视图」。"""
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="initial")  # initial|tracking
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WeeklyReport(CompanionScoped, Base):
    """周报:每周一(本周首次打开 App)生成一份,弹窗展示后收录看板反复查看。

    week_key 用 ISO 周(周一为一周起点),同一 user+week_key 仅一份(幂等);
    seen_at 标记用户是否已关闭弹窗(看过),用于"这周只弹一次"。
    """
    __tablename__ = "weekly_reports"
    __table_args__ = (UniqueConstraint("user_id", "companion_id", "week_key", name="uq_weekly_report"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    week_key: Mapped[str] = mapped_column(String(16))   # 形如 2026-W35
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # {cards, state, compared}
    seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserAuth(Base):
    """用户名密码登录凭据。单独建表(而非 User 加列),便于 create_all 在存量库上直接建表(免 ALTER 迁移)。"""
    __tablename__ = "user_auth"

    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 存小写,登录不区分大小写
    password_hash: Mapped[str] = mapped_column(String(256))  # pbkdf2 结果 hex
    password_salt: Mapped[str] = mapped_column(String(64))   # 盐 hex
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthSession(Base):
    """登录会话:token → user_id,默认 30 天有效。"""
    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserState(Base):
    """单用户轻量状态:只存用户级(全局)UI 偏好,不再承载「陪伴对象」。

    陪伴对象(多段、可归档)已迁移到 companions 表,由 current_companion_id 指向当前活跃对象。
    下方 relationship_type / subject_name / ending_* / archived_at 为遗留列(只读兼容),不再写入;
    真源一律读 companions。users.loss_type 同样遗留弃用(只读)。
    """
    __tablename__ = "user_states"

    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), primary_key=True)

    # 当前活跃的陪伴对象(指向 companions.id;空 = 尚无对象或引导期)
    current_companion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ---- 遗留陪伴身份列(已迁移 companions,仅读兼容,勿再写) ----
    relationship_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # DEPRECATED
    relationship_type_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # DEPRECATED
    relationship_type_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # DEPRECATED
    subject_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # DEPRECATED

    # 引导 / 首次关键来信 / 首份报告(用户级,不随对象走)
    first_letter_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|opened
    first_report_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|pinned

    # 各类介绍/开关(用户级)
    home_intro_seen: Mapped[bool] = mapped_column(Boolean, default=False)
    board_intro_seen: Mapped[bool] = mapped_column(Boolean, default=False)
    tts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    tts_intro_seen: Mapped[bool] = mapped_column(Boolean, default=False)
    tts_speed: Mapped[float] = mapped_column(Float, default=1.0)              # 语速(0.85 慢 / 1.0 正常 / 1.2 快)
    tts_vol: Mapped[float] = mapped_column(Float, default=1.0)                # 音量(0.7 小 / 1.0 中 / 1.3 大)
    echo_enabled: Mapped[bool] = mapped_column(Boolean, default=True)         # 回忆回声开关

    # 005 首页状态(用户级)
    dismissed_moods: Mapped[list] = mapped_column(JSON, default=list)   # 被撤下的情绪日
    pending_events: Mapped[list] = mapped_column(JSON, default=list)    # weekly / trial / mail 待办

    # 结束 / 归档(遗留,已迁移 companions) ----
    ending_stage: Mapped[str] = mapped_column(String(16), default="active")  # DEPRECATED
    ending_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # DEPRECATED
    ending_ritual: Mapped[str | None] = mapped_column(String(16), nullable=True)  # DEPRECATED
    ending_committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # DEPRECATED
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # DEPRECATED

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Companion(Base):
    """陪伴对象(一个「忘不掉的对象」= 一行):单用户可有多段,一个当前活跃。

    relationship_type 是陪伴类型真源(breakup/pet/relative);archived_at 置位 = 进入只读档案态。
    取代原 user_states 上单行的陪伴身份字段;user_states.current_companion_id 指向当前活跃对象。
    """
    __tablename__ = "companions"
    __table_args__ = (Index("ix_companions_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)

    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    chat_session_id: Mapped[str] = mapped_column(String(64), default=_uuid)

    # 陪伴类型(关系类型)
    relationship_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # breakup|pet|relative
    relationship_type_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # inferred|manual
    relationship_type_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    subject_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 对象名(结束仪式展示用)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # 结束 / 归档
    ending_stage: Mapped[str] = mapped_column(String(16), default="active")  # active|farewell|complete
    ending_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ending_ritual: Mapped[str | None] = mapped_column(String(16), nullable=True)  # dissolved|buried|skipped
    ending_committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 置位=只读档案态

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FeedbackEntry(Base):
    """用户意见反馈(帮助与反馈页提交,轻量落库,供运营查看)。user_id 可空(未登录也可反馈)。"""
    __tablename__ = "feedback_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|processing|resolved
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)      # 运营回复/处理说明
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DiaryNote(Base):
    """日记便利贴(「写下今天」):用户在时间看板写的一句话,轻量情绪记录。"""
    __tablename__ = "diary_notes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", server_default="Asia/Shanghai")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    canonical_diary_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    content: Mapped[str] = mapped_column(Text)
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 用情绪词典轻量归类,零 LLM
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EchoEntry(CompanionScoped, Base):
    """回声壁:随机捕获用户富有情感的句子,随机延时后作为"回忆回声"推回用户(类似网易云音乐/视频 App 的回忆推送)。

    提取:后台记忆回写时,按情感强度 + 采样概率随机挑中用户的一句倾诉,存下原句(零 LLM,纯采样)。
    推送:拉取式 /echo/popup 在 reveal_at 到期后返回最早一条;前端展示后调 /echo/popup/seen 标记已看。
    status: pending(待回显) | delivered(已回显) | dismissed(用户划掉)
    """
    __tablename__ = "echo_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)                            # 被回显的原句(截取的一句)
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 提取时主导情绪
    source: Mapped[str] = mapped_column(String(16), default="chat")       # chat
    status: Mapped[str] = mapped_column(String(16), default="pending")    # pending|delivered|dismissed
    reveal_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # 到期回显时间(随机延迟后)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ActivationRedemption(Base):
    """激活码兑换记录:一码一次,兑换成功后置 users.membership=member。"""
    __tablename__ = "activation_redemptions"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    used_by: Mapped[str] = mapped_column(String(64), index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
