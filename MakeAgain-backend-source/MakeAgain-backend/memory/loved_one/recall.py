"""亲友离世场景的记忆召回。

当前复用通用召回(recency + importance + relevance),与失恋场景同构,保持一致的打分骨架。
丧亲场景的差异化方向(后续在此扩展):
- 对「共同记忆 / 逝者细节」类记忆(kind 为 memory/emotion_trigger 的 fact)适度加权,
  因为持续联结(纪念/回想)需要这些素材;
- 克制重复注入(通用召回已含 overlap 克制),尤其避免反复翻出死亡当天等创伤性细节。
"""
from __future__ import annotations

from memory.recall import recall as _recall


def recall(db, user_id: str, query: str, top_k: int | None = None,
           recent_text: str | None = None) -> list[dict]:
    return _recall(db, user_id, query, top_k=top_k, recent_text=recent_text)
