"""关系类型 AI 推断:从 003 首问「Ta 是谁」的回答直接三分类(breakup / pet / relative)。

输入:用户对「Ta 是谁」的一句回答;输出 relationship_type + confidence(0~1)。
推断失败或低置信度时由路由层返回 relationship_type=null,前端弹窗兜底(禁止缺省为 breakup)。
"""
from __future__ import annotations

from gateway.client import LLMClient

_SYSTEM = "你是关系类型识别助手,只输出 JSON,不要多余文字。"

_LABELS = {
    "breakup": "分手/恋人(前任、伴侣、感情破裂)",
    "pet": "宠物(猫、狗等陪伴过的小动物)",
    "relative": "亲人(家人、父母、长辈、亲友离世)",
}

_VALID = ("breakup", "pet", "relative")

# 置信度 >= 此值才静默采用;低于则交给前端弹窗兜底。
ADOPT_CONFIDENCE = 0.6


def infer(client: LLMClient, answer: str) -> dict:
    """返回 {"relationship_type": str|None, "confidence": float|None}。

    mock 模式走关键词启发式(仅本地冒烟),生产走 LLM。
    """
    if client.mock:
        rt = _keyword_infer(answer)
        return {"relationship_type": rt, "confidence": (0.8 if rt else None)}

    parsed, _ = client.chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": (
                "用户回答了「Ta 是谁」,判断这段陪伴属于哪一类,只输出 JSON:\n"
                '{"relationship_type":"breakup|pet|relative","confidence":0.0~1.0}\n'
                "分类口径:\n"
                f"- breakup = {_LABELS['breakup']}\n"
                f"- pet = {_LABELS['pet']}\n"
                f"- relative = {_LABELS['relative']}\n"
                "confidence 是把握程度,拿不准就给低分。\n\n"
                f"用户回答:{answer}"
            )},
        ],
        temperature=0.0, model=client.settings.llm_fast_model,
    )
    if not isinstance(parsed, dict):
        return {"relationship_type": None, "confidence": None}
    rt = parsed.get("relationship_type")
    if rt not in _VALID:
        return {"relationship_type": None, "confidence": None}
    try:
        conf = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        conf = None
    return {"relationship_type": rt, "confidence": conf}


def _keyword_infer(answer: str) -> str | None:
    """mock/无 key 时的轻量关键词兜底,仅用于本地冒烟,非生产路径。"""
    a = (answer or "").lower()
    if any(w in a for w in ("猫", "狗", "宠物", "小动物", "兔子", "仓鼠", "鸟")):
        return "pet"
    if any(w in a for w in ("爸", "妈", "母", "父", "亲人", "家人", "爷爷", "奶奶", "外公", "外婆",
                            "哥哥", "姐姐", "弟弟", "妹妹", "女儿", "儿子", "丈夫", "妻子", "老伴")):
        return "relative"
    if any(w in a for w in ("前任", "前男友", "前女友", "分手", "恋人", "男朋友", "女朋友", "伴侣", "ex")):
        return "breakup"
    return None
