"""回声壁 Agent:随机捕获用户富有情感的句子,随机延时后作为"回忆回声"推回用户。

设计要点(仅后端,类网易云音乐/视频 App 的回忆推送):
- 捕获:后台记忆回写(async_write._do_write)顺带调用 maybe_capture_echo,零 LLM、纯采样——
  情感达标(富有情感) + 随机命中(echo_capture_rate)才存下一句,并随机一个未来的 reveal_at。
- 推送:拉取式。get_echo_popup 在打开 App / 轮询时被调用,返回 reveal_at 已到期的最早一条;
  前端展示后调 mark_seen(delivered),用户划掉调 mark_seen(dismissed)。未标记前会一直待回显。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from config import get_settings
from memory import store
from memory.models import EchoEntry, utcnow

# 「富有情感」的主导情绪:平静/回避/其他(中性·逃避·未知)不适合作为"回忆回声"。
_RICH_EMOTIONS = {"想念", "难过", "愤怒", "内疚", "孤独", "不甘", "释怀", "焦虑", "恐惧"}

# 关键词兜底:无情绪 dict 时,或从长句中挑最富情感的一句时使用。
_RICH_KWS = (
    "想", "想念", "怀念", "忘不掉", "放不下", "舍不得", "记得", "想起", "回忆",
    "难过", "伤心", "哭", "眼泪", "遗憾", "委屈", "难受", "痛",
    "后悔", "内疚", "对不起", "怪我",
    "孤独", "一个人", "空落落",
    "害怕", "恐惧", "不甘", "凭什么",
    "释怀", "放下", "祝福", "轻松", "走出来",
)

_SENT_END = "。！？!?；;\n"


def _emotionally_rich(emotion: dict | None, text: str = "") -> bool:
    """判断这句倾诉是否「富有情感」(值得回显)。"""
    if emotion:
        emo = emotion.get("emotion")
        if emo in _RICH_EMOTIONS:
            return True
        try:
            if abs(float(emotion.get("valence", 0.0))) >= 0.4:
                return True
            if float(emotion.get("arousal", 0.0)) >= 0.5:
                return True
        except (TypeError, ValueError):
            pass
        return False
    # 无情绪 dict 时,用关键词兜底
    return any(k in text for k in _RICH_KWS)


def _split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SENT_END:
            s = buf.strip()
            if s:
                parts.append(s)
            buf = ""
    tail = buf.strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_sentence(text: str) -> str:
    """从整段消息里截出一句作为回声;短消息原样返回,长消息挑最富情感的一句。"""
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) <= get_settings().echo_max_chars:
        return t
    sentences = _split_sentences(t)
    if sentences:
        best = max(sentences, key=lambda s: (sum(1 for k in _RICH_KWS if k in s), len(s)))
        return best[: get_settings().echo_max_chars]
    return t[: get_settings().echo_max_chars]


def maybe_capture_echo(db, user_id: str, message: str, emotion: dict | None = None) -> EchoEntry | None:
    """后台回写时尝试捕获一条回声;不满足条件返回 None(不阻塞、不抛异常)。"""
    s = get_settings()
    text = (message or "").strip()
    if len(text) < s.echo_min_chars:
        return None
    if not _emotionally_rich(emotion, text):
        return None
    # 随机采样:富有情感的句子只取一部分,避免每句都回显造成打扰
    if random.random() >= s.echo_capture_rate:
        return None
    # 待回显队列上限:超出则放弃新捕获
    if store.count_pending_echos(db, user_id) >= s.echo_max_pending:
        return None
    sentence = _extract_sentence(text)
    if not sentence:
        return None
    delay_hours = random.uniform(s.echo_reveal_min_hours, s.echo_reveal_max_hours)
    reveal_at = utcnow() + timedelta(hours=delay_hours)
    emo = (emotion or {}).get("emotion")
    return store.add_echo(db, user_id, sentence, emo, reveal_at)


def _local(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    local = dt + timedelta(hours=get_settings().timezone_offset_hours)
    return local.strftime("%Y-%m-%d %H:%M")


def _serialize(e: EchoEntry) -> dict:
    return {
        "id": e.id,
        "content": e.content,
        "emotion": e.emotion,
        "source": e.source,
        "status": e.status,
        "reveal_at": _local(e.reveal_at),
        "created_at": _local(e.created_at),
    }


def get_echo_popup(db, user_id: str, now: datetime | None = None) -> dict:
    """拉取式推送:返回 reveal_at 已到期的最早一条待回显回声(不标记,前端展示后调 seen)。

    按用户偏好:echo_enabled=False 直接关掉;节流间隔取全局 echo_push_interval_hours。
    """
    store.get_or_create_user(db, user_id)
    state = store.get_user_state(db, user_id)
    if state is not None and state.echo_enabled is False:
        return {"echo": None}
    interval = get_settings().echo_push_interval_hours
    now = now or utcnow()
    due = store.list_due_echos(db, user_id, now=now)
    if not due:
        return {"echo": None}
    last = store.last_echo_surfaced_at(db, user_id)
    if last is not None and (now - last) < timedelta(hours=interval):
        return {"echo": None}
    return {"echo": _serialize(due[0])}


def mark_seen(db, user_id: str, echo_id: str, dismiss: bool = False) -> dict:
    """标记回声已看(或划掉),之后不再回显。"""
    status = "dismissed" if dismiss else "delivered"
    return {"ok": store.mark_echo_status(db, echo_id, status)}
