"""结构化事实(facts)的通用读写助手,供召回 / 上下文构建复用。

facts 是 extract.py 抽取的稳定事实列表,每条形如:
    {"kind": "user|object|memory|emotion_trigger|goal|time", "fact": "...", "confidence": 0.8}

这里只提供零依赖的拼接与过滤,避免把 LLM / 情绪依赖带进 recall 这类纯本地模块。
"""
from __future__ import annotations

# 与 extract.py / interview.py 的 facts kind 口径保持一致
FACT_KINDS = ("user", "object", "memory", "emotion_trigger", "goal", "time")


def _fact_str(f) -> str:
    if not isinstance(f, dict):
        return ""
    return str(f.get("fact") or "").strip()


def facts_text(facts) -> str:
    """把所有 fact 文本拼成一段(供召回做关键词匹配)。"""
    return " ".join(_fact_str(f) for f in (facts or []) if _fact_str(f))


def facts_for_context(facts, min_conf: float = 0.6, kinds=None, max_n: int = 2) -> list[str]:
    """过滤出高置信、指定 kind 的 fact 文本(去重、保序、截断)。

    confidence 缺失时视为通过(不因字段缺失丢信息);`kinds` 为 None 时不过滤 kind。
    """
    allowed = set(kinds) if kinds else None
    out: list[str] = []
    seen: set[str] = set()
    for f in (facts or []):
        if not isinstance(f, dict):
            continue
        if allowed is not None and f.get("kind") not in allowed:
            continue
        conf = f.get("confidence")
        if conf is not None:
            try:
                if float(conf) < min_conf:
                    continue
            except (TypeError, ValueError):
                pass
        text = _fact_str(f)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= max_n:
            break
    return out
