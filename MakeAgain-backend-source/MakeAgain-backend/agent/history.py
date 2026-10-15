"""聊天历史(精确到分钟):历史记录页的读取编排 + 批量删除。

职责:
- 游标分页三模式:上滑加载更早(before_id)、下滑加载更新(after_id)、跳转到某天第一条(date)。
- ts 存 naive UTC,展示按 timezone_offset_hours 转本地时间,精确到分钟。
- 批量删除:只允许删本人消息,越权 id 静默忽略。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from agent import history_summary
from config import get_settings
from memory import store
from memory.models import ChatMessage


def serialize(m: ChatMessage) -> dict:
    """单条消息 → 展示结构。ts 转本地,精确到分钟。"""
    local = m.ts + timedelta(hours=get_settings().timezone_offset_hours)
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "session_id": m.session_id,
        "ts": local.strftime("%Y-%m-%d %H:%M"),   # 精确到分钟
        "date": local.strftime("%Y-%m-%d"),       # 本地日,供前端分组
    }


def _anchor_for_date(db, user_id: str, date_str: str) -> int | None:
    """把本地日 'YYYY-MM-DD' 解析为该天第一条消息的 id。

    该天无消息 → 其后最近一天第一条;之后也无 → None(由调用方回落最新一页)。
    """
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError("date 需为 YYYY-MM-DD") from e
    offset = get_settings().timezone_offset_hours
    day_start_utc = day - timedelta(hours=offset)          # 本地 0 点 → naive UTC
    day_end_utc = day_start_utc + timedelta(days=1)
    anchor = store.first_chat_id_on_date(db, user_id, day_start_utc, day_end_utc)
    if anchor is None:
        anchor = store.first_chat_id_since(db, user_id, day_end_utc)
    return anchor


def page(db, user_id: str, *, before_id: int | None = None,
         after_id: int | None = None, date: str | None = None,
         limit: int | None = None) -> dict:
    """取一页聊天历史(升序,旧→新)。

    返回:
      messages: 升序消息列表
      has_older / has_newer: 两个方向是否还有更多(供前端显示加载状态)
      cursor_oldest_id / cursor_newest_id: 本页首尾 id(下一次 before/after 的游标)
      anchor: 跳转模式时定位到的那条(某天第一条),普通翻页为 None
    """
    s = get_settings()
    size = limit or s.chat_history_page_size
    size = max(1, min(int(size), s.chat_history_max_page_size))

    start_id = None
    if date is not None:
        start_id = _anchor_for_date(db, user_id, date)
        # start_id 为 None:该日及之后都无消息 → 回落最新一页(anchor 不设)

    rows = store.list_chat_page(db, user_id, before_id=before_id,
                                after_id=after_id, start_id=start_id, limit=size)
    if not rows:
        return {"messages": [], "has_older": False, "has_newer": False,
                "cursor_oldest_id": None, "cursor_newest_id": None, "anchor": None}

    oldest = rows[0].id
    newest = rows[-1].id
    anchor = serialize(rows[0]) if (date is not None and start_id is not None) else None

    return {
        "messages": [serialize(m) for m in rows],
        "has_older": store.chat_has_before(db, user_id, oldest),
        "has_newer": store.chat_has_after(db, user_id, newest),
        "cursor_oldest_id": oldest,
        "cursor_newest_id": newest,
        "anchor": anchor,
    }


def delete_many(db, user_id: str, message_ids: list[int]) -> dict:
    """批量删除本人消息,返回实际删除条数。"""
    ids = [int(i) for i in message_ids]
    return {"deleted": store.delete_chat_messages(db, user_id, ids)}


def day_list(db, user_id: str, *, before_date: str | None = None,
             limit: int | None = None, now: datetime | None = None) -> dict:
    """聊天记录一级页:每天的总结列表(打开时惰性固定「昨日及之前」的草稿)。

    返回 days(按本地日倒序)+ has_more,每项 {date, count, summary, status, finalizing}:
    - status: draft(今日进行中)| final(已固定)| None(该天尚无总结)。
    - finalizing: 本次已提交后台固定、尚未落库(前端可稍后重拉)。
    - before_date: 只取更早的日期(游标分页);has_more: 是否还有更早的日期。
    """
    s = get_settings()
    size = limit or s.chat_history_page_size
    size = max(1, min(int(size), s.chat_history_max_page_size))
    if before_date is not None:
        try:
            datetime.strptime(before_date, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError("before_date 需为 YYYY-MM-DD") from e

    pending = set(history_summary.finalize_due(db, user_id, now=now))
    summaries = {s.date_key: s for s in store.list_daily_chat_summaries(db, user_id)}
    rows = store.list_chat_days(db, user_id, before_date=before_date, limit=size + 1)
    has_more = len(rows) > size
    days = []
    for date_key, count in rows[:size]:
        row = summaries.get(date_key)
        days.append({
            "date": date_key,
            "count": count,
            "summary": row.summary if row else None,
            "status": row.status if row else None,
            "finalizing": date_key in pending,
        })
    return {"days": days, "has_more": has_more}


def day_page(db, user_id: str, date_str: str, *, before_id: int | None = None,
             after_id: int | None = None, limit: int | None = None) -> dict:
    """聊天记录二级页:某天的内容分页(当天内上滑加载更早)。

    返回 {date, summary, status, messages, has_older, has_newer,
          cursor_oldest_id, cursor_newest_id};游标仅当天内有效,不越界到相邻天。
    """
    s = get_settings()
    size = limit or s.chat_history_page_size
    size = max(1, min(int(size), s.chat_history_max_page_size))

    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError("date 需为 YYYY-MM-DD") from e
    offset = s.timezone_offset_hours
    start_utc = day - timedelta(hours=offset)
    end_utc = start_utc + timedelta(days=1)

    rows = store.list_chat_day_page(db, user_id, start_utc, end_utc,
                                    before_id=before_id, after_id=after_id, limit=size)
    summary_row = store.get_daily_chat_summary(db, user_id, date_str)
    base = {
        "date": date_str,
        "summary": summary_row.summary if summary_row else None,
        "status": summary_row.status if summary_row else None,
    }
    if not rows:
        return {**base, "messages": [], "has_older": False, "has_newer": False,
                "cursor_oldest_id": None, "cursor_newest_id": None}

    oldest = rows[0].id
    newest = rows[-1].id
    return {
        **base,
        "messages": [serialize(m) for m in rows],
        "has_older": store.chat_day_has_before(db, user_id, start_utc, end_utc, oldest),
        "has_newer": store.chat_day_has_after(db, user_id, start_utc, end_utc, newest),
        "cursor_oldest_id": oldest,
        "cursor_newest_id": newest,
    }
