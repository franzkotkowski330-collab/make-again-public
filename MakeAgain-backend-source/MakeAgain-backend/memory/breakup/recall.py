"""失恋场景的记忆召回。

当前复用通用召回(recency + importance + relevance),保持与其它场景一致的打分骨架。
失恋场景的差异化方向(后续在此扩展):
- 对「分手原因 / 关系复盘」类记忆(记忆 kind 为 memory/emotion_trigger/goal 的 fact)适度加权,
  因为复盘期需要这些素材;
- 对反刍信号强的记忆,克制重复注入(避免「刚聊过又提」,通用召回已含 overlap 克制)。
"""
from __future__ import annotations

from memory.recall import recall as _recall


def recall(db, user_id: str, query: str, top_k: int | None = None,
           recent_text: str | None = None) -> list[dict]:
    return _recall(db, user_id, query, top_k=top_k, recent_text=recent_text)
