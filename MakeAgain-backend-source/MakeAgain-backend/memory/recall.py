"""召回流程:recency + importance + relevance 混合打分。"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from config import get_settings
from memory.facts import facts_text
from memory.store import list_memories

_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text or ""))


def _relevance(query_tokens: set[str], entry_tokens: set[str]) -> float:
    """余弦式关键词重叠,无重叠为 0。"""
    if not query_tokens or not entry_tokens:
        return 0.0
    inter = query_tokens & entry_tokens
    if not inter:
        return 0.0
    return len(inter) / math.sqrt(len(query_tokens) * len(entry_tokens))


def _overlap_fraction(entry_tokens: set[str], recent_tokens: set[str]) -> float:
    """记忆自身词汇里已有多少比例出现在最近对话中(用于克制重复引用)。

    不对称:分母是记忆自身的词数。若记忆的大半关键词都已在最近对话里出现,
    说明这件事正在被聊,无需再作为「相关记忆」注入——否则刚说完又提,显得机械。
    """
    if not entry_tokens:
        return 0.0
    return len(entry_tokens & recent_tokens) / len(entry_tokens)


def recall(db, user_id: str, query: str, top_k: int | None = None, recent_text: str | None = None) -> list[dict]:
    s = get_settings()
    top_k = top_k or s.recall_top_k
    entries = list_memories(db, user_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    q_tokens = _tokens(query)
    recent_tokens = _tokens(recent_text) if recent_text else set()

    scored: list[dict] = []
    for e in entries:
        entry_tokens = _tokens(f"{facts_text(e.facts)} {e.summary or ''} {e.content}")
        # 克制:已出现在最近几轮对话里的记忆不再注入(它已在会话历史里,模型本就看得见)
        if recent_tokens and _overlap_fraction(entry_tokens, recent_tokens) >= s.recall_recent_overlap_threshold:
            continue
        days = max(0.0, (now - e.ts).total_seconds() / 86400.0)
        recency = math.exp(-days / 30.0)  # 30 天半衰期
        importance = (e.importance or 5.0) / 10.0
        relevance = _relevance(q_tokens, entry_tokens)
        score = s.recall_alpha * recency + s.recall_beta * importance + s.recall_gamma * relevance
        scored.append({"entry": e, "score": score, "recency": recency, "relevance": relevance})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
