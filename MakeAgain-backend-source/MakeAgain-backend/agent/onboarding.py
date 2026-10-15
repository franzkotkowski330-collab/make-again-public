"""用户引导阶段(轻量状态机):new → interview → report → main。

阶段由 onboarding_states 表持久化(字段 + 状态机)。语义:
- new:首次进入,尚未开始问卷。
- interview:问卷进行中(可中断恢复)。
- report:问卷已完成、报告已生成(报告可能仍在后台生成,前端轮询就绪)。
- main:已进入主界面;初始报告仍可在看板随时查看。

只有明确的用户动作才推进阶段:开始问卷、完成问卷、进入主界面。
"""
from __future__ import annotations

from memory import store

PHASES = ("new", "interview", "report", "main")

_TRANSITIONS = {
    "new": {"interview"},
    "interview": {"report"},
    "report": {"main"},
    "main": set(),
}


def get_phase(db, user_id: str) -> str:
    """当前阶段;无记录视为 new(首次进入)。"""
    row = store.get_onboarding(db, user_id)
    return row.phase if row else "new"


def set_phase(db, user_id: str, phase: str) -> str:
    """直接写入阶段(内部使用,不做转移校验)。"""
    if phase not in PHASES:
        raise ValueError(f"非法阶段: {phase}")
    store.upsert_onboarding(db, user_id, phase)
    return phase


def transition(db, user_id: str, to_phase: str) -> str:
    """按状态机推进;仅允许合法转移,幂等重复设置当前阶段。"""
    cur = get_phase(db, user_id)
    if to_phase == cur:
        return cur
    if to_phase not in _TRANSITIONS.get(cur, set()):
        raise ValueError(f"不允许从「{cur}」跳到「{to_phase}」")
    return set_phase(db, user_id, to_phase)


def initial_report_view(report: dict, created_at: str | None = None) -> dict:
    """初始报告的「用户视图」:标题 / 关键词 / 正文 / 金句 + (分手时)关系分析 + 生成时间。

    不暴露 user_portrait / object_portrait / goal / heal_plan(这些是内部画像与计划,
    供后续陪伴 / 跟踪报告使用,不直接呈现给用户)。
    """
    report = report or {}
    view = {
        "title": report.get("title", ""),
        "keywords": report.get("keywords") or [],
        "summary": report.get("summary", ""),
        "quote": report.get("quote", ""),
    }
    if created_at:
        view["created_at"] = created_at
    ra = report.get("relationship_analysis")
    if ra:
        view["relationship_analysis"] = ra
    return view
