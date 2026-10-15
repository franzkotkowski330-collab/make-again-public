"""写入流程:从一轮倾诉抽取事实 + 情绪 + 显著性,供记忆流落库。"""
from __future__ import annotations

from emotion.classifier import classify
from gateway.client import LLMClient

_TIME_TAGS = ["凌晨", "深夜", "夜里", "晚上", "傍晚", "下午", "中午", "上午", "早上", "周末", "周六", "周日"]
_PLACE_TAGS = ["学校", "教室", "家里", "她家", "教堂", "寺庙", "医院", "公司", "公园", "海边", "咖啡店", "拜佛"]


def _first_tag(text: str, tags: list[str]) -> str | None:
    for t in tags:
        if t in text:
            return t
    return None


def extract_tags(text: str) -> tuple[str | None, str | None]:
    """轻量提取地点/时间标签(供访谈等场景复用,不依赖 LLM)。"""
    return _first_tag(text, _PLACE_TAGS), _first_tag(text, _TIME_TAGS)


def extract_turn(client: LLMClient, user_text: str, *, object_hint: str | None = None) -> dict:
    """返回 {facts, emotion, summary, importance, place_tag, time_tag}。

    object_hint: 可选的「思念对象提示」,注入到抽取 prompt 里,约束 summary/facts 对
    「TA」的称呼(如丧亲场景传「逝者是亲人,别写前任/前女友」),避免 LLM 默认套失恋用词。
    """
    emotion = classify(user_text, client)
    place_tag = _first_tag(user_text, _PLACE_TAGS)
    time_tag = _first_tag(user_text, _TIME_TAGS)

    if client.mock:
        return {
            "facts": [],
            "emotion": emotion,
            "summary": user_text[:120],
            "importance": _importance_from_emotion(emotion),
            "place_tag": place_tag,
            "time_tag": time_tag,
        }

    hint = f"[场景提示] {object_hint}\n\n" if object_hint else ""
    messages = [
        {"role": "system", "content": "你是记忆抽取助手,只输出 JSON,不要多余文字。"},
        {"role": "user", "content": (
            "从下面的倾诉中抽取结构化信息,输出 JSON,字段:\n"
            "{\"facts\":[{\"kind\":\"user|object|memory|emotion_trigger|goal|time\",\"fact\":\"...\",\"confidence\":0.8}],\n"
            " \"summary\":\"一句话概括\",\n"
            " \"importance\":0到10(对疗愈进程的重要程度),\n"
            f" \"place_tag\":\"地点,只能从这些词里选:{'/'.join(_PLACE_TAGS)},没有则为 null\",\n"
            f" \"time_tag\":\"时间,只能从这些词里选:{'/'.join(_TIME_TAGS)},没有则为 null\"}}\n\n"
            "facts 的 kind 含义:user=用户自身信息,object=思念对象信息,memory=共同记忆细节,"
            "emotion_trigger=情绪触发点,goal=疗愈目标,time=事件发生的真实时间(如「去年生日」「三年前」「纪念日」,"
            "指事件发生的时间,不是聊天时刻;没有则省略)。\n\n"
            + hint
            + f"文本:\n{user_text}"
        )},
    ]
    parsed, _ = client.chat_json(messages, temperature=0.2, model=client.settings.llm_fast_model)
    if not parsed:
        return {
            "facts": [], "emotion": emotion, "summary": user_text[:120],
            "importance": _importance_from_emotion(emotion),
            "place_tag": place_tag, "time_tag": time_tag,
        }

    facts = parsed.get("facts")
    if not isinstance(facts, list):
        facts = []
    importance = float(parsed.get("importance", _importance_from_emotion(emotion)))

    # 硬校验:标签必须落在标准词表内,否则回退到词典提取结果(标准词表)或 None,
    # 避免 LLM 造出"今天"这类非标准标签污染情绪节点。
    llm_place = parsed.get("place_tag")
    llm_time = parsed.get("time_tag")
    return {
        "facts": facts,
        "emotion": emotion,
        "summary": parsed.get("summary") or user_text[:120],
        "importance": max(0.0, min(10.0, importance)),
        "place_tag": llm_place if llm_place in _PLACE_TAGS else place_tag,
        "time_tag": llm_time if llm_time in _TIME_TAGS else time_tag,
    }


def _importance_from_emotion(emotion: dict) -> float:
    # 越消极、强度越高,越值得记住
    s = abs(emotion.get("valence", 0)) * 5 + emotion.get("arousal", 0.5) * 5
    return max(3.0, min(10.0, s))
