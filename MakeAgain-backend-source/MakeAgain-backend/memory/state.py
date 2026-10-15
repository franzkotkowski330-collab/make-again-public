"""状态数据化:把记忆流里的情绪信号,聚合为可解释的"状态"与"记忆点"数据。

纯本地聚合,零 LLM。产出两类:
- 状态指标:baseline / trend / volatility / acute_ratio / calm_ratio / reconcile / risk / stage
- 记忆点数据:active_days / longest_streak / first_reconcile / top_time_tag / top_place_tag / mention_count ...

供报告选卡、回信资格、语气路由复用(报告只把它当内部信号,不直接给用户看冷分数)。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from config import get_settings
from emotion.tone import ACUTE_EMOTIONS
from memory import store

CALM_EMOTIONS = {"平静", "释怀"}
STAGES = ["低谷期", "波动期", "趋稳期", "平静期", "和解期"]


def _local_date(ts) -> str:
    offset = get_settings().timezone_offset_hours
    return (ts + timedelta(hours=offset)).strftime("%Y-%m-%d")


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 50.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_state(db, user_id: str, since=None) -> dict:
    """聚合情绪信号,返回状态指标 + 阶段(0~4 对应 STAGES)。since 为时间下界时只统计该时间之后。"""
    day_scores: dict[str, list[float]] = {}
    day_emotions: dict[str, list[str]] = {}
    for m in store.list_memories(db, user_id, since=since):
        if not (m.emotion and "score" in m.emotion):
            continue
        d = _local_date(m.ts)
        day_scores.setdefault(d, []).append(float(m.emotion["score"]))
        day_emotions.setdefault(d, []).append(m.emotion.get("emotion", "其他"))

    days = sorted(day_scores)
    n_days = len(days)

    last7 = days[-7:]
    scores_7 = [s for d in last7 for s in day_scores[d]]
    baseline = _mean(scores_7)
    volatility = _std(scores_7)

    # 趋势:后半段均值 − 前半段均值
    trend = 0.0
    if n_days >= 2:
        half = max(1, n_days // 2)
        prev_avg = _mean([s for d in days[:half] for s in day_scores[d]])
        recent_avg = _mean([s for d in days[half:] for s in day_scores[d]])
        trend = recent_avg - prev_avg

    last14 = days[-14:]
    emos_14 = [e for d in last14 for e in day_emotions[d]]
    acute_ratio = sum(1 for e in emos_14 if e in ACUTE_EMOTIONS) / len(emos_14) if emos_14 else 0.0
    calm_ratio = sum(1 for e in emos_14 if e in CALM_EMOTIONS) / len(emos_14) if emos_14 else 0.0

    # 风险度:急性占比 + 低基线 + 谷底
    valley = min(scores_7) if scores_7 else 50.0
    risk = _clamp(acute_ratio * 0.6 + (1 - baseline / 100.0) * 0.2 + (0.3 if valley < 30 else 0.0), 0.0, 1.0)

    # 和解度(0~100)
    goal = store.get_or_create_user(db, user_id).goal
    trend_score = _clamp((trend + 10.0) / 20.0, 0.0, 1.0)
    goal_bonus = 1.0 if goal else 0.5
    reconcile = (
        0.35 * calm_ratio + 0.20 * trend_score + 0.20 * (1 - acute_ratio)
        + 0.15 * (baseline / 100.0) + 0.10 * goal_bonus
    ) * 100.0

    stage = _stage(baseline, trend, acute_ratio, calm_ratio, reconcile)
    return {
        "baseline": round(baseline, 1),
        "trend": round(trend, 1),
        "volatility": round(volatility, 1),
        "acute_ratio": round(acute_ratio, 2),
        "calm_ratio": round(calm_ratio, 2),
        "risk": round(risk, 2),
        "reconcile": round(reconcile, 1),
        "stage": stage,
        "stage_label": STAGES[stage],
        "n_days": n_days,
        "n_memories": sum(len(v) for v in day_scores.values()),
    }


def _stage(baseline: float, trend: float, acute_ratio: float, calm_ratio: float, reconcile: float) -> int:
    # 阶段判定(阈值可调,先按经验值)
    if reconcile >= 70 and calm_ratio >= 0.5:
        return 4  # 和解期
    if calm_ratio >= 0.4 and baseline >= 55 and acute_ratio <= 0.3:
        return 3  # 平静期
    if trend > 3 and baseline >= 50:
        return 2  # 趋稳期
    if acute_ratio >= 0.5 or baseline < 45:
        return 0  # 低谷期
    return 1  # 波动期


# ---- 记忆点数据 ----

def active_days(db, user_id: str, since=None) -> int:
    return len({_local_date(m.ts) for m in store.list_memories(db, user_id, since=since)})


def longest_streak(db, user_id: str) -> int:
    """最长连续有倾诉记录的天数。"""
    days = sorted({_local_date(m.ts) for m in store.list_memories(db, user_id)})
    if not days:
        return 0
    dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in days]
    best = cur = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def first_reconcile(db, user_id: str, since=None) -> str | None:
    """第一条体现释怀/放下的记忆的日期(按时间正序),没有则 None。"""
    hits = [k for k in ("释怀", "放下", "祝福", "走出来")]
    mems = sorted(store.list_memories(db, user_id, since=since), key=lambda m: m.ts)
    for m in mems:
        if (m.emotion and m.emotion.get("emotion") == "释怀") or any(k in (m.content or "") for k in hits):
            return _local_date(m.ts)
    return None


def top_tags(db, user_id: str, since=None) -> tuple[str | None, str | None]:
    mems = store.list_memories(db, user_id, since=since)
    time_c = Counter(m.time_tag for m in mems if m.time_tag)
    place_c = Counter(m.place_tag for m in mems if m.place_tag)
    top_time = time_c.most_common(1)[0][0] if time_c else None
    top_place = place_c.most_common(1)[0][0] if place_c else None
    return top_time, top_place


def night_count(db, user_id: str) -> int:
    night = {"凌晨", "深夜", "夜里", "晚上"}
    return sum(1 for m in store.list_memories(db, user_id) if m.time_tag in night)


def mention_count(db, user_id: str) -> int | None:
    """对象「称呼」在倾诉里出现的次数;称呼缺省或为单字代词时返回 None(卡省略)。"""
    obj = store.get_portrait(db, user_id, "object")
    name = (obj or {}).get("称呼") or (obj or {}).get("姓名")
    if not name or len(str(name)) < 2:
        return None
    name = str(name)
    return sum((m.content or "").count(name) + (m.summary or "").count(name)
               for m in store.list_memories(db, user_id))


def saddest_day(db, user_id: str) -> str | None:
    """平均情绪分最低的一天(本地日期),无数据则 None。"""
    day_scores: dict[str, list[float]] = {}
    for m in store.list_memories(db, user_id):
        if m.emotion and "score" in m.emotion:
            day_scores.setdefault(_local_date(m.ts), []).append(float(m.emotion["score"]))
    if not day_scores:
        return None
    return min(day_scores, key=lambda d: _mean(day_scores[d]))
