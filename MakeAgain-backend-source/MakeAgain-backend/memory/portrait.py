"""画像增量合并:聊天积累的新事实定期合并进画像,让画像随对话演进。

触发策略(零新表、零迁移):
- 以最新 user 画像的 updated_at 为水位线,统计其后的记忆条数;
  达到 portrait_merge_every 才考虑合并。
- 只收集 confidence >= portrait_merge_min_confidence 且 kind∈{user,object} 的 fact;
  无可用 fact 或 mock 客户端时跳过(不触发 LLM、不破坏离线确定性)。
- 用一次 fast 模型调用把累积 fact 与当前画像合并:仅填「暂未提及」、补稳定事实,
  不覆盖已确认的值、不编造。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from config import get_settings
from memory import store
from memory.models import Portrait

_MERGE_SYSTEM = (
    "你是「重逢」的记忆整理助手。根据现有画像与聊天中累积的稳定事实,输出合并后的画像 JSON。"
)

# 视为「未提及」的占位,可被新事实填充
_PLACEHOLDERS = {"暂未提及", "", None}


def _portrait_text(d: dict) -> str:
    if not d:
        return "(空)"
    return "\n".join(f"- {k}: {v}" for k, v in d.items() if v)


def _user_updated_at(db, user_id: str) -> datetime | None:
    row = db.execute(
        select(Portrait)
        .where(Portrait.user_id == user_id, Portrait.kind == "user")
        .order_by(Portrait.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.updated_at if row else None


def _collect_facts(db, user_id: str, since: datetime, limit: int = 40) -> list[str]:
    """取 since 之后、带高置信 user/object 事实的记忆,返回去重后的 fact 文本列表。"""
    s = get_settings()
    min_conf = s.portrait_merge_min_confidence
    seen: set[str] = set()
    out: list[str] = []
    for m in store.list_memories(db, user_id, since=since):
        for f in (m.facts or []):
            if not isinstance(f, dict):
                continue
            if f.get("kind") not in ("user", "object"):
                continue
            conf = f.get("confidence")
            if conf is not None:
                try:
                    if float(conf) < min_conf:
                        continue
                except (TypeError, ValueError):
                    pass
            text = str(f.get("fact") or "").strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
                if len(out) >= limit:
                    return out
    return out


def _merge_prompt(current: dict, facts: list[str]) -> str:
    return (
        f"现有画像:\n{_portrait_text(current)}\n\n"
        "新累积的稳定事实(均为用户 / 思念对象相关信息):\n"
        + "\n".join(f"- {f}" for f in facts)
        + "\n\n请输出合并后的画像 JSON:保留现有画像里已确认的信息,把「暂未提及」的字段用新事实填上,"
          "补入新的稳定事实;不要删除或改写已确认的值,不要编造。只输出 JSON 对象本身。"
    )


def merge_if_due(db, user_id: str, client) -> bool:
    """达到阈值则把新事实合并进 user 画像;返回是否执行了合并。"""
    s = get_settings()
    if getattr(client, "mock", False):
        return False
    updated_at = _user_updated_at(db, user_id)
    if updated_at is None:
        return False  # 尚无画像(未做访谈),不自动建画像
    recent = store.list_memories(db, user_id, since=updated_at)
    if len(recent) < s.portrait_merge_every:
        return False
    facts = _collect_facts(db, user_id, since=updated_at)
    if not facts:
        return False

    current = store.get_portrait(db, user_id, "user")
    parsed, _ = client.chat_json(
        [{"role": "system", "content": _MERGE_SYSTEM},
         {"role": "user", "content": _merge_prompt(current, facts)}],
        temperature=0.2, model=client.settings.llm_fast_model,
    )
    if not parsed or not isinstance(parsed, dict):
        return False

    merged = dict(current or {})
    for k, v in parsed.items():
        if not v:
            continue
        # 已有非占位值且 LLM 想改写 → 不覆盖,保护已确认信息
        if k in merged and merged[k] not in _PLACEHOLDERS and merged[k] != v:
            continue
        merged[k] = v
    store.upsert_portrait(db, user_id, "user", merged, status="draft")
    return True
