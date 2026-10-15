"""聊天记录每日总结的离线白盒验证(确定性,全 mock)。

覆盖本批次改动:
1. summarize_day:当天有消息才生成一句话总结并落库;空天不建行;重复生成幂等覆盖。
2. finalize_due:只把「昨日及之前」补成 final,今日保持 draft;已 final 的天不再调度。
3. day_list:按本地日倒序合并 count + summary + status + finalizing。
4. day_page:当天内游标分页(默认最新页、before_id 上滑更早),不越界到相邻天。

运行(在 backend 目录下):
    python scripts/chat_summary_sim.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from agent import history as history_mod, history_summary  # noqa: E402
from config import Settings  # noqa: E402
from memory import store  # noqa: E402
from memory.db import Base  # noqa: E402
from memory.models import ChatMessage  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
engine = create_engine(f"sqlite:///{_tmp.name.replace(chr(92), '/')}",
                       connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# 后台 worker 走临时库 + 假模型(确定性)
history_summary.SessionLocal = S

UID = "sum-uid"
TZ = timedelta(hours=8)
SUMMARY = "今天聊了分手后对前任的想念,夜里情绪有些低落,也在慢慢学着放下。"


class FakeClient:
    def __init__(self):
        self.settings = Settings(mock_llm=True)

    def chat(self, messages, temperature=0.7, max_tokens=None, model=None, tools=None):
        return {"content": SUMMARY, "usage": {}, "tool_calls": []}


history_summary.LLMClient = lambda: FakeClient()

db = S()
_results: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    _results.append((name, bool(cond)))
    print(f"[{'OK' if cond else 'FAIL'}] {name}")


def local(y, mo, d, h, mi=0):
    """本地时间 → naive UTC。"""
    return datetime(y, mo, d, h, mi) - TZ


def add_msg(role: str, content: str, ts: datetime) -> None:
    db.add(ChatMessage(user_id=UID, session_id="", role=role, content=content, ts=ts))


# 昨日 2026-08-28 两轮(4 条),今日 2026-08-29 一轮(2 条)
add_msg("user", "晚上又想起他了", local(2026, 8, 28, 10, 0))
add_msg("assistant", "我在,慢慢说", local(2026, 8, 28, 10, 0))
add_msg("user", "睡前最难熬", local(2026, 8, 28, 22, 0))
add_msg("assistant", "陪你待一会儿", local(2026, 8, 28, 22, 0))
add_msg("user", "今天好一些了", local(2026, 8, 29, 9, 0))
add_msg("assistant", "那就好", local(2026, 8, 29, 9, 0))
db.commit()

NOW = local(2026, 8, 29, 12, 0)  # 今日 2026-08-29 本地中午

# =====================================================================
print("== 1. list_chat_days 本地日分组 ==")
# =====================================================================
days = store.list_chat_days(db, UID)
check("按本地日分组为 2 天(倒序)", [d for d, _ in days] == ["2026-08-29", "2026-08-28"])
check("count 正确(今日 2 / 昨日 4)", [c for _, c in days] == [2, 4])

# =====================================================================
print("\n== 2. summarize_day ==")
# =====================================================================
got = history_summary.summarize_day(db, UID, "2026-08-28", "draft", client=FakeClient())
check("有消息的天生成总结", got == SUMMARY)
check("草稿落库 status=draft",
      store.get_daily_chat_summary(db, UID, "2026-08-28").status == "draft")
history_summary.summarize_day(db, UID, "2026-08-28", "draft", client=FakeClient())
check("重复生成幂等(仍 1 行)", len(store.list_daily_chat_summaries(db, UID)) == 1)
check("空天不建行", history_summary.summarize_day(db, UID, "2026-08-20", "draft",
                                                 client=FakeClient()) is None)

history_summary.summarize_day(db, UID, "2026-08-29", "draft", client=FakeClient())

# =====================================================================
print("\n== 3. finalize_due(惰性固定) ==")
# =====================================================================
pending = history_summary.finalize_due(db, UID, now=NOW)
check("只调度昨日(不含今日)", pending == ["2026-08-28"])
history_summary.flush()
check("昨日翻成 final", store.get_daily_chat_summary(db, UID, "2026-08-28").status == "final")
check("今日保持 draft", store.get_daily_chat_summary(db, UID, "2026-08-29").status == "draft")
check("已 final 的天不再调度", history_summary.finalize_due(db, UID, now=NOW) == [])

# =====================================================================
print("\n== 4. day_list 一级页 ==")
# =====================================================================
lst = history_mod.day_list(db, UID, now=NOW)["days"]
check("返回 2 天", len(lst) == 2)
today, yday = lst[0], lst[1]
check("今日置顶:date/status/summary/count",
      today["date"] == "2026-08-29" and today["status"] == "draft"
      and today["summary"] == SUMMARY and today["count"] == 2)
check("昨日:status=final/count=4",
      yday["date"] == "2026-08-28" and yday["status"] == "final" and yday["count"] == 4)

# =====================================================================
print("\n== 5. day_page 二级页(当天内游标) ==")
# =====================================================================
p1 = history_mod.day_page(db, UID, "2026-08-28", limit=2)
check("昨日默认最新页(2 条,倒序取末尾)", len(p1["messages"]) == 2
      and p1["messages"][0]["content"] == "睡前最难熬")
check("最新页 has_older=True / has_newer=False",
      p1["has_older"] is True and p1["has_newer"] is False)

p2 = history_mod.day_page(db, UID, "2026-08-28", before_id=p1["cursor_oldest_id"], limit=2)
check("before_id 上滑取更早(剩 2 条)", len(p2["messages"]) == 2
      and p2["messages"][0]["content"] == "晚上又想起他了")
check("上滑到底 has_older=False", p2["has_older"] is False)

t1 = history_mod.day_page(db, UID, "2026-08-29", limit=1)
check("今日默认最新页(1 条,是最新 assistant)", len(t1["messages"]) == 1
      and t1["messages"][0]["role"] == "assistant")
t2 = history_mod.day_page(db, UID, "2026-08-29", before_id=t1["cursor_oldest_id"], limit=5)
check("今日上滑不越界到昨日(仅今日 1 条 user)",
      len(t2["messages"]) == 1 and t2["messages"][0]["role"] == "user"
      and t2["has_older"] is False)

try:
    history_mod.day_page(db, UID, "bad-date")
    check("非法日期抛 ValueError", False)
except ValueError:
    check("非法日期抛 ValueError", True)

# =====================================================================
print("\n" + "=" * 60)
failed = [n for n, ok in _results if not ok]
print(f"共 {len(_results)} 项,失败 {len(failed)} 项")
for n in failed:
    print(f"  FAIL: {n}")
print("=" * 60)

db.close()
engine.dispose()
try:
    os.remove(_tmp.name)
except OSError:
    pass

sys.exit(1 if failed else 0)
