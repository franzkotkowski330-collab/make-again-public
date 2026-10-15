"""反思流程:从记忆流聚合情绪趋势与情绪节点,生成"用户看不到的变化"洞察。"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select

from memory.models import EmotionNode, utcnow
from memory.store import clear_emotion_nodes, list_memories


def reflect(db, user_id: str) -> dict:
    entries = list_memories(db, user_id)
    if not entries:
        return {"trend": [], "insights": [], "nodes": [], "count": 0}

    by_hour: dict[int, list[float]] = defaultdict(list)
    by_day: dict[str, list[float]] = defaultdict(list)
    for e in entries:
        if e.emotion and "score" in e.emotion:
            by_hour[e.ts.hour].append(float(e.emotion["score"]))
            by_day[e.ts.strftime("%Y-%m-%d")].append(float(e.emotion["score"]))

    # 最难过的时间段(平均情绪分最低)
    hour_avg = {h: sum(v) / len(v) for h, v in by_hour.items()}
    lowest_hour = min(hour_avg, key=hour_avg.get) if hour_avg else None
    hour_label = _hour_label(lowest_hour) if lowest_hour is not None else None

    # 情绪趋势(按天)
    trend = [
        {"day": d, "avg_score": round(sum(v) / len(v), 1), "count": len(v)}
        for d, v in sorted(by_day.items())
    ]

    place_counter = Counter(e.place_tag for e in entries if e.place_tag)
    time_counter = Counter(e.time_tag for e in entries if e.time_tag)

    insights: list[str] = []
    if hour_label and len(trend) >= 2:
        insights.append(f"你最低落的情绪,通常出现在{hour_label}。")
    top_place = place_counter.most_common(1)
    if top_place:
        insights.append(f"你最近 {top_place[0][1]} 次提到了同一个地方:{top_place[0][0]}。")
    top_time = time_counter.most_common(1)
    if top_time:
        insights.append(f"你反复提到的时刻是「{top_time[0][0]}」,共 {top_time[0][1]} 次。")

    nodes = _rebuild_nodes(db, user_id, entries)
    return {"trend": trend, "insights": insights, "nodes": nodes, "count": len(entries)}


def _hour_label(hour: int) -> str:
    if 5 <= hour < 12:
        return "上午"
    if 12 <= hour < 18:
        return "下午"
    if 18 <= hour < 24:
        return "晚上"
    return "凌晨"


def _rebuild_nodes(db, user_id: str, entries) -> list[dict]:
    # 幂等:清空后按 time_tag/place_tag 重建,同 trigger 累加 frequency
    clear_emotion_nodes(db, user_id)
    keyed: dict[tuple, dict] = {}
    for e in entries:
        trigger = e.time_tag or e.place_tag
        if not trigger:
            continue
        emo = (e.emotion or {}).get("emotion", "想念")
        key = (trigger, emo)
        if key in keyed:
            keyed[key]["frequency"] += 1
        else:
            keyed[key] = {
                "trigger": trigger, "emotion": emo, "frequency": 1,
                "place": e.place_tag, "time_tag": e.time_tag,
            }

    nodes: list[dict] = []
    for (trigger, emo), info in keyed.items():
        db.add(EmotionNode(
            user_id=user_id, trigger=trigger, emotion=emo,
            intensity=0.5, frequency=info["frequency"],
            place=info["place"], time_tag=info["time_tag"],
        ))
        nodes.append({"trigger": trigger, "emotion": emo, "frequency": info["frequency"]})
    db.commit()
    return nodes


def upsert_emotion_node(db, entry) -> None:
    """增量更新单个情绪节点(替代每轮全量重建),口径与 _rebuild_nodes 的 (trigger, emotion) 一致。

    输入一条已落库的 MemoryEntry(带 time_tag/place_tag/emotion);无 trigger 时直接返回。
    """
    trigger = getattr(entry, "time_tag", None) or getattr(entry, "place_tag", None)
    if not trigger:
        return
    emo = (getattr(entry, "emotion", None) or {}).get("emotion") or "想念"
    row = db.execute(
        select(EmotionNode).where(
            EmotionNode.user_id == entry.user_id,
            EmotionNode.trigger == trigger,
            EmotionNode.emotion == emo,
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(EmotionNode(
            user_id=entry.user_id, trigger=trigger, emotion=emo,
            intensity=0.5, frequency=1,
            place=getattr(entry, "place_tag", None), time_tag=getattr(entry, "time_tag", None),
        ))
    else:
        row.frequency += 1
        row.last_seen = utcnow()
    db.commit()
