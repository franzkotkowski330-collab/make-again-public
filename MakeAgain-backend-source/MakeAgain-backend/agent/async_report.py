"""后台报告生成:访谈完成时不阻塞,把「生成报告 + 落库」移到后台线程。

设计要点:
- 访谈完成(answer 走到 complete)只同步做「标记完成 + 推进阶段」,立即返回。
- 报告生成(1 次 LLM 调用,可能几十秒)放到单 worker 后台线程,不阻塞用户。
- 生成完成后落库(画像 / 记忆流 / reports 表),并回写访谈会话的 report_ready 标记,
  供前端轮询 /interview/{session_id} 得知「报告就绪」。
- 单 worker 串行写库,避免 SQLite 并发写锁竞争;使用独立 DB 会话与 LLM 客户端。
"""
from __future__ import annotations

import copy
import logging
from concurrent.futures import ThreadPoolExecutor

from gateway.client import LLMClient
from memory.db import SessionLocal

logger = logging.getLogger(__name__)

# 单 worker:串行生成报告,避免并发写 SQLite 争锁
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-gen")


def schedule_report(user_id: str, session_id: str, loss_type: str | None, history: list[dict]) -> None:
    """提交一次后台报告生成,立即返回。失败不影响已完成的访谈。"""
    from memory.jobs import enqueue
    enqueue("report",user_id,{"session_id":session_id,"loss_type":loss_type,"history":history},dedupe="report:"+session_id)


def _do_report(user_id: str, session_id: str, loss_type: str | None, history: list[dict]) -> None:
    from agent import interview
    from memory import store

    db = SessionLocal()
    try:
        client = LLMClient()  # 独立客户端,避免与请求线程共享 httpx 连接
        companion = store.get_current_companion(db, user_id)
        subject_name = (companion.subject_name or "").strip() if companion else ""
        parsed = interview._build_report_parsed(client, loss_type, history,
                                                subject_name=subject_name or None)
        interview._persist_report(db, user_id, loss_type, parsed)
        session = store.get_interview(db, session_id)
        if session is not None:
            state = copy.deepcopy(session.state or {})
            state["report"] = parsed
            state["report_ready"] = True
            state["generating"] = False
            store.update_interview(db, session, state=state)
    except Exception:  # noqa: BLE001 后台任务必须兜底,不能把异常抛回请求
        raise
    finally:
        db.close()


def flush_reports() -> None:
    """等待当前已提交的报告生成全部完成(供脚本/测试在退出前调用)。"""
    _executor.submit(lambda: None).result()
