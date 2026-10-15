"""情绪日历(F2-3):按本地日聚合记忆流里的情绪打分,产出每日主情绪 + 均分。

纯聚合、零 LLM。数据来源是 MemoryEntry.emotion(由 extract_turn 在后台用
classify 打出的 {emotion, valence, arousal, score}),score 越高 = 心情越好。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from config import get_settings
from memory.store import list_memories


def _local_date(ts: datetime, offset: int) -> str:
    return (ts + timedelta(hours=offset)).strftime("%Y-%m-%d")


def get_calendar(db, user_id: str, month: str | None = None) -> dict:
    """返回某月(默认当月)逐日情绪:主情绪 + 平均 score/valence/arousal + 条数。

    整月每一天都会返回,某天没聊时该天 emotion/score/valence/arousal 为 None、count=0(空)。
    """
    offset = get_settings().timezone_offset_hours
    local_now = datetime.now() + timedelta(hours=offset)
    if not month:
        month = local_now.strftime("%Y-%m")
    year = int(month[:4])
    mon = int(month[5:7])
    first = datetime(year, mon, 1)
    nxt = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
    days_in_month = (nxt - first).days

    by_day: dict[str, dict] = defaultdict(lambda: {
        "scores": [], "valences": [], "arousals": [], "emotions": [],
    })
    for e in list_memories(db, user_id):
        emo = e.emotion or {}
        if "score" not in emo:
            continue
        date = _local_date(e.ts, offset)
        g = by_day[date]
        g["scores"].append(float(emo.get("score", 50)))
        g["valences"].append(float(emo.get("valence", 0.0)))
        g["arousals"].append(float(emo.get("arousal", 0.5)))
        g["emotions"].append(emo.get("emotion", "其他"))

    days = []
    for d in range(1, days_in_month + 1):
        date = f"{year:04d}-{mon:02d}-{d:02d}"
        g = by_day.get(date)
        if g:
            dominant = Counter(g["emotions"]).most_common(1)[0][0]
            n = len(g["scores"])
            days.append({
                "date": date,
                "emotion": dominant,
                "score": round(sum(g["scores"]) / n, 1),
                "valence": round(sum(g["valences"]) / n, 2),
                "arousal": round(sum(g["arousals"]) / n, 2),
                "count": n,
            })
        else:
            days.append({"date": date, "emotion": None, "score": None,
                         "valence": None, "arousal": None, "count": 0})

    return {"month": month, "days": days}
