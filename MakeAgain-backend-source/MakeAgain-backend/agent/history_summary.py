"""每日聊天总结:当天退出时写草稿(draft),跨天后惰性固定(final)。

对齐需求「每天固定凌晨总结一段简短文字;今天就退出时先写草稿、固定时间固定下来、无感」:
- 总结由当天 ChatMessage 拼 transcript、走 fast 模型生成一句话(用户私有记录,可引用当天自己的倾诉,
  与「每日启发文案不引用 memory 事件」不同)。
- 退出对话(/chat/session/clear)时后台 schedule 今日 draft;跨天后打开记录页(/days)时
  finalize_due 把「昨日及之前」补成 final。惰性触发,无定时器。
- 单 worker + _inflight 去重 + flush,与 agent/weekly.py 同构。
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from config import get_settings
from gateway.client import LLMClient
from memory import store
from memory.db import SessionLocal

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chat-summary")
_inflight: set[tuple[str, str, str]] = set()
_lock = threading.Lock()

_SUMMARY_SYSTEM = (
    "你是「重逢」的记录员。把用户当天与 AI 的对话概括成一句简短、温柔、中性的记录:"
    "只记录核心内容与情绪状态,不评价、不安慰、不添加对话之外的信息,用陈述句,"
    "不要以「今天」开头(它已经是一条按天的记录)。无法概括时只返回空字符串,不要任何解释。"
)


def today_key(now: datetime | None = None) -> str:
    """本地时区的今日日期键 YYYY-MM-DD。"""
    offset = get_settings().timezone_offset_hours
    return ((now or datetime.now()) + timedelta(hours=offset)).strftime("%Y-%m-%d")


def _day_range(date_key: str) -> tuple[datetime, datetime]:
    """本地日 → naive UTC 区间 [start, end)。"""
    day = datetime.strptime(date_key, "%Y-%m-%d")
    offset = get_settings().timezone_offset_hours
    start_utc = day - timedelta(hours=offset)
    return start_utc, start_utc + timedelta(days=1)


def _transcript(msgs) -> str:
    lines = []
    for m in msgs:
        role = "用户" if m.role == "user" else "你"
        lines.append(f"{role}: {(m.content or '').strip()}")
    return "\n".join(lines)


def _build_prompt(transcript: str, max_chars: int) -> str:
    return f"请用一句不超过 {max_chars} 字的中文概括下面这位用户当天的倾诉:\n\n{transcript}"


def summarize_day(db, user_id: str, date_key: str, status: str = "draft",
                  client: LLMClient | None = None) -> str | None:
    """生成某天的一句话总结并落库;当天无消息则返回 None(不建行)。"""
    store.get_or_create_user(db, user_id)
    start, end = _day_range(date_key)
    msgs = store.list_chat_day_messages(db, user_id, start, end)
    if not msgs:
        return None
    client = client or LLMClient()
    s = get_settings()
    transcript = _transcript(msgs)[: s.chat_daily_summary_transcript_chars]
    result = client.chat(
        [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": _build_prompt(transcript, s.chat_daily_summary_max_chars)},
        ],
        temperature=0.4, max_tokens=128, model=client.settings.llm_fast_model,
    )
    summary = (result.get("content") or "").strip()
    if not summary:
        return None
    summary = summary[: s.chat_daily_summary_max_chars]
    store.upsert_daily_chat_summary(db, user_id, date_key, summary, status)
    return summary


def schedule(user_id: str, date_key: str, status: str) -> None:
    from memory.jobs import enqueue
    enqueue("summary",user_id,{"date_key":date_key,"status":status})


def _do(user_id: str, date_key: str, status: str, key: tuple) -> None:
    db = SessionLocal()
    try:
        client = LLMClient()  # 独立客户端,避免与请求线程共享 httpx 连接
        summarize_day(db, user_id, date_key, status, client=client)
    except Exception:  # noqa: BLE001 后台任务兜底,不把异常抛回请求
        raise
    finally:
        with _lock:
            _inflight.discard(key)
        db.close()


def finalize_due(db, user_id: str, now: datetime | None = None) -> list[str]:
    """把「昨日及之前」尚未固定的天补成 final(后台),返回本次调度中的日期列表。

    已 final 的天跳过;今日不处理(保持 draft,等待退出/下一次总结)。
    """
    today = today_key(now)
    summaries = {s.date_key: s.status for s in store.list_daily_chat_summaries(db, user_id)}
    pending: list[str] = []
    for date_key, _count in store.list_chat_days(db, user_id):
        if date_key >= today:
            continue
        if summaries.get(date_key) == "final":
            continue
        schedule(user_id, date_key, "final")
        pending.append(date_key)
    return pending


def flush() -> None:
    """等待当前已提交的总结生成全部完成(供脚本/测试退出前调用)。"""
    _executor.submit(lambda: None).result()
