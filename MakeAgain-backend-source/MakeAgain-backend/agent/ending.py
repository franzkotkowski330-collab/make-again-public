"""结束仪式纪念文案:依据用户留下的记忆与陪伴类型,生成一段温柔的纪念正文(供 019/020 结束流程展示)。

设计要点:
- 一次 fast 模型调用,把「对象名 + 关系类型 + 近期记忆摘要」织成一段 80~150 字的收尾文字。
- 不煽情、不空喊加油、不引导伤感、不编造细节;落点在「好好生活不等于忘记」。
- 无记忆数据或 mock 时,按关系类型回落固定温和文案,保证结束流程不中断。
"""
from __future__ import annotations

from gateway.client import LLMClient
from memory import store

_SYSTEM = "你是「重逢」的陪伴者。用户正在结束一段陪伴,需要一段温柔的纪念文字,像一封写给 TA(或它)的收尾。"

_FALLBACK = {
    "pet": "你带我走过的那些路,我都记得。以后你再想起我,不必只想起最后那一天。",
    "relative": "你留下的照片、话语和物品,都记录着这段关系真实存在过。",
    "breakup": "这段感情真实存在过,你认真爱过,也认真难过过。",
}

_DISCLOSURE = "以下文字由 AI 根据你留下的故事整理"


def memorial(db, user_id: str, client: LLMClient) -> dict:
    """生成纪念正文,返回 {memorial, subject_name, relationship_type, disclosure}。"""
    store.get_or_create_user(db, user_id)
    companion = store.get_current_companion(db, user_id)
    state = store.get_user_state(db, user_id)
    relationship_type = companion.relationship_type if companion is not None else None
    if relationship_type is None:
        relationship_type = state.relationship_type if state is not None else None
    rt = (relationship_type or "").strip().lower()
    subject_name = companion.subject_name if companion is not None else None
    if subject_name is None:
        subject_name = state.subject_name if state is not None else None
    subject_name = (subject_name or "").strip()
    fallback = _FALLBACK.get(rt, _FALLBACK["breakup"])

    if client.mock:
        return {"memorial": fallback, "subject_name": subject_name or None,
                "relationship_type": rt or None, "disclosure": _DISCLOSURE}

    mem_txt = "\n".join(
        f"- {m.summary or m.content}" for m in store.list_memories(db, user_id)[:12]
    ) or "(暂无)"
    prompt = (
        f"关系类型:{rt or '未知'};对象名:{subject_name or '未具名'}。\n"
        f"这段陪伴里 TA 说过/经历过的片段(按时间,均为过去):\n{mem_txt}\n\n"
        "请写一段 80~150 字的纪念文字,像一段温柔的收尾,把陪伴的体温留在字里:"
        "不煽情、不空喊加油、不引导伤感、不编造细节,点到一两个真实片段,落点在「好好生活不等于忘记」。只输出这段文字。"
    )
    try:
        result = client.chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
            temperature=0.7, model=client.settings.llm_fast_model,
        )
        text = (result.get("content") or "").strip()
    except Exception:
        text = ""
    return {"memorial": text or fallback, "subject_name": subject_name or None,
            "relationship_type": rt or None, "disclosure": _DISCLOSURE}
