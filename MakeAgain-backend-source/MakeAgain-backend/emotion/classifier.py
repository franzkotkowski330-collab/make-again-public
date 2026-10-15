"""情绪分类:LLM 结构化输出,关键词词典兜底。"""
from __future__ import annotations

from gateway.client import LLMClient

EMOTIONS = ["难过", "愤怒", "内疚", "回避", "释怀", "平静", "焦虑", "孤独", "不甘", "想念", "恐惧", "其他"]

_KEYWORDS: dict[str, list[str]] = {
    "想念": ["想", "怀念", "回忆", "忘不掉", "难以释怀", "放不下", "记得", "想起"],
    "难过": ["难过", "伤心", "哭", "眼泪", "痛", "失落", "遗憾", "委屈", "难受"],
    "愤怒": ["生气", "恨", "愤怒", "暴脾气", "诋毁", "不公平", "凭什么", "吵架"],
    "内疚": ["后悔", "内疚", "抱歉", "对不起", "怪自己", "道歉", "亏欠"],
    "孤独": ["孤独", "一个人", "没人", "空落落", "寂寞"],
    "不甘": ["不甘", "不值得", "为什么是我", "放不下", "不舍得"],
    "焦虑": ["焦虑", "失眠", "睡不着", "内耗", "纠结", "担心", "不安", "恐惧幸福", "自卑", "自愧不如", "配不上"],
    "恐惧": ["害怕", "恐惧", "怕", "不敢"],
    "回避": ["算了", "不想", "逃避", "无所谓", "没意义", "没必要"],
    "释怀": ["释怀", "放下", "祝福", "祝你幸福", "走出来", "轻松", "知足", "不后悔"],
    "平静": ["平静", "还好", "没事", "正常"],
}


def classify(text: str, client: LLMClient | None = None) -> dict:
    if client is None or client.mock:
        return _lexicon_classify(text)
    messages = [
        {"role": "system", "content": "你是情绪分析助手,只输出 JSON,不要多余文字。"},
        {"role": "user", "content": (
            f"分析下面这段话的情绪,输出 JSON,字段:\n"
            f"emotion(枚举:{'/'.join(EMOTIONS)}),\n"
            f"valence(-1到1,负=消极/正=积极),\n"
            f"arousal(0到1,情绪强度),\n"
            f"score(0到100,越高=心情越好),\n"
            f"reason(一句话依据)。\n\n{text}"
        )},
    ]
    parsed, _ = client.chat_json(messages, temperature=0.1, model=client.settings.llm_fast_model)
    if parsed and "emotion" in parsed:
        return _normalize(parsed)
    return _lexicon_classify(text)


def _normalize(d: dict) -> dict:
    out = {
        "emotion": d.get("emotion", "其他"),
        "valence": float(d.get("valence", 0.0)),
        "arousal": float(d.get("arousal", 0.5)),
        "score": int(d.get("score", 50)),
        "reason": d.get("reason", ""),
    }
    out["valence"] = max(-1.0, min(1.0, out["valence"]))
    out["arousal"] = max(0.0, min(1.0, out["arousal"]))
    out["score"] = max(0, min(100, out["score"]))
    return out


def _lexicon_classify(text: str) -> dict:
    scores = {e: 0 for e in EMOTIONS}
    for emo, kws in _KEYWORDS.items():
        for kw in kws:
            if kw in text:
                scores[emo] += 1
    emotion = max(scores, key=scores.get) if any(scores.values()) else "其他"

    neg = any(k in text for k in ["难过", "伤心", "哭", "痛", "遗憾", "生气", "内耗", "焦虑", "失眠", "害怕", "孤独", "不甘", "后悔", "难受"])
    pos = any(k in text for k in ["释怀", "放下", "祝福", "轻松", "开心", "幸福", "知足", "高兴", "不后悔"])

    if neg and not pos:
        valence, score = -0.5, 35
    elif pos and not neg:
        valence, score = 0.4, 65
    elif neg and pos:
        valence, score = -0.1, 45
    else:
        valence, score = 0.0, 50

    arousal = 0.6 if any(k in text for k in ["哭", "生气", "暴", "害怕", "慌", "崩溃", "痛", "失眠"]) else 0.4
    return {"emotion": emotion, "valence": valence, "arousal": arousal, "score": score, "reason": "关键词词典兜底"}
