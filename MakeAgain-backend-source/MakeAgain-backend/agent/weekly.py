"""周报:每周一(本周首次打开 App)生成一份跟踪周报,弹窗展示后收录看板反复查看。

对齐需求「定期报告=周报形式,每周一更新,非新用户本周第一次点开时弹窗,关闭后收录看板」:
- week_key 用 ISO 周(周一为一周起点),如 "2026-W35";同一 user+week_key 仅一份(幂等)。
- 生成放后台单 worker(同 async_report),避免报告 LLM 摘要阻塞打开 App。
- 非新用户(引导阶段已到 main)才触发;数据不足(沿用 report_eligibility 门槛)不生成、不弹。
- seen_at 标记用户是否已关闭弹窗(看过),用于"这周只弹一次"。
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from agent import onboarding, report as report_mod
from config import get_settings
from gateway.client import LLMClient
from memory import store
from memory.db import SessionLocal

logger = logging.getLogger(__name__)

# 单 worker 串行生成,避免并发写 SQLite 争锁(同 async_report)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-report")
_inflight: set[tuple[str, str]] = set()
_lock = threading.Lock()


def week_key(now: datetime | None = None) -> str:
    """ISO 周 key(周一为一周起点),按本地时区换算。"""
    offset = get_settings().timezone_offset_hours
    local = (now or datetime.now()).replace(microsecond=0) + timedelta(hours=offset)
    iso = local.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def due(db, user_id: str, now: datetime | None = None) -> dict:
    """打开 App 时调用:判断是否要弹本周周报;需要生成时提交后台任务。

    返回 {due, week_key, generating, report, reason}:
    - due=True 且 report 非空 → 直接弹窗展示完整报告。
    - due=True 且 generating=True → 后台生成中,前端轮询 /weekly-report/{user_id}/{week_key}。
    - due=False → 不弹(引导未完成 / 数据不足 / 已看过)。
    """
    store.get_or_create_user(db, user_id)
    wk = week_key(now)

    # 已结束陪伴(已归档)不再生成/弹周报:周报只跟踪「进行中的陪伴」
    companion = store.get_current_companion(db, user_id)
    if companion is not None and companion.archived_at is not None:
        return {"due": False, "week_key": wk, "generating": False, "report": None,
                "reason": "已结束陪伴，不再生成周报"}

    # 非新用户才弹:必须已完成初始引导、进入主界面
    if onboarding.get_phase(db, user_id) != "main":
        return {"due": False, "week_key": wk, "generating": False, "report": None,
                "reason": "引导阶段未完成"}

    existing = store.get_weekly_report(db, user_id, wk)
    if existing is not None:
        if existing.seen_at is None:
            return {"due": True, "week_key": wk, "generating": False,
                    "report": existing.content}
        return {"due": False, "week_key": wk, "generating": False, "report": None,
                "reason": "已查看"}

    # 本周尚未生成:数据够才生成(避免空报告);沿用定期报告的数据充足门槛
    elig = report_mod.report_eligibility(db, user_id, now=now)
    if not elig["eligible"]:
        return {"due": False, "week_key": wk, "generating": False, "report": None,
                "reason": elig["reason"]}

    schedule(user_id, wk)
    return {"due": True, "week_key": wk, "generating": True, "report": None}


def get(db, user_id: str, week_key_: str) -> dict | None:
    """单份周报(含完整报告内容);无则 None。"""
    row = store.get_weekly_report(db, user_id, week_key_)
    if row is None:
        return None
    return {"id": row.id, "companion_id": row.companion_id, "week_key": row.week_key, "seen": row.seen_at is not None,
            "created_at": row.created_at.isoformat()+"Z",
            "report": row.content}


def list_reports(db, user_id: str) -> list[dict]:
    """历史周报列表(看板),按周倒序。"""
    return [
        {"id": r.id, "companion_id": r.companion_id, "week_key": r.week_key, "seen": r.seen_at is not None,
         "created_at": r.created_at.isoformat()+"Z",
         "report": r.content}
        for r in store.list_weekly_reports(db, user_id)
    ]


def mark_seen(db, user_id: str, week_key_: str) -> dict:
    """关闭弹窗时标记已看,之后本周不再弹。"""
    row = store.mark_weekly_report_seen(db, user_id, week_key_)
    return {"ok": row is not None}


def schedule(user_id: str, wk: str) -> None:
    from memory.jobs import enqueue
    enqueue("weekly",user_id,{"week_key":wk})


def _do(user_id: str, wk: str) -> None:
    db = SessionLocal()
    try:
        client = LLMClient()  # 独立客户端,避免与请求线程共享连接
        result = report_mod.build_report(db, user_id, client)
        if result.get("eligible"):
            store.save_weekly_report(db, user_id, wk, {
                "cards": result["cards"],
                "state": result["state"],
                "compared": result["compared"],
            })
    except Exception:  # noqa: BLE001 后台任务兜底,不把异常抛回请求
        raise
    finally:
        with _lock:
            _inflight.discard((user_id, wk))
        db.close()


def flush() -> None:
    """等待当前已提交的周报生成全部完成(供脚本/测试退出前调用)。"""
    _executor.submit(lambda: None).result()
