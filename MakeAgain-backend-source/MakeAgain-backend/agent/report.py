"""定期跟踪报告:事件触发(数据充足 + 使用一定期限),滑动卡片流,因人而异。

选卡逻辑(用户确认):
1. 计算状态(stage 0~4)。
2. 每张卡先看数据是否可用(数据不足 → 省略),再看阶段门槛(当前阶段 >= 卡的最低阶段才展示)。
3. 状态总括卡永远第一;其余按优先级 + 分线多样性,最多 report_max_cards 张。
   这样"不同用户不一样":低谷期看不到"释怀/回信"这类往前走的卡,避免像在催他好起来。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from config import get_settings
from gateway.client import LLMClient
from memory import store, state as state_mod

_SUMMARY_SYSTEM = "你是「重逢」的陪伴者。根据用户近期的记忆与情绪状态,温和地总结并分析 TA 这段时间的历程,像一位熟悉 TA 的朋友。"


def _fmt_md(s: str) -> str:
    d = datetime.strptime(s, "%Y-%m-%d")
    return f"{d.month}月{d.day}日"


def _dominant_emotion_halves(db, user_id: str, since=None) -> tuple[str | None, str | None]:
    mems = sorted(
        [m for m in store.list_memories(db, user_id, since=since) if m.emotion and m.emotion.get("emotion")],
        key=lambda m: m.ts,
    )
    if len(mems) < 4:
        return None, None
    half = len(mems) // 2
    a = Counter(m.emotion["emotion"] for m in mems[:half]).most_common(1)[0][0]
    b = Counter(m.emotion["emotion"] for m in mems[half:]).most_common(1)[0][0]
    return a, b


# ---- 卡片文案(返回 None 表示该用户无此数据,卡省略) ----

def _card_total_turns(db, uid, st, since=None):
    n, days = st["n_memories"], st["n_days"]
    return f"这 {days} 天,你一共向我倾诉了 {n} 次。" if n >= 1 else None


def _card_mention(db, uid, st):
    n = state_mod.mention_count(db, uid)
    if not n:
        return None
    name = (store.get_portrait(db, uid, "object") or {}).get("称呼") or "TA"
    return f"你一共提到「{name}」{n} 次。"


def _card_attachment(db, uid, st):
    p = store.get_portrait(db, uid, "user") or {}
    att = p.get("依恋类型")
    pattern = p.get("关系中的模式")
    if not att or att in ("暂未提及", ""):
        return None
    base = f"在这段关系里,你更偏「{att}」的依恋方式"
    if pattern and pattern not in ("暂未提及", ""):
        base += f",{pattern}"
    return base + "。"


def _card_cause(db, uid, st):
    cause = (store.get_portrait(db, uid, "user") or {}).get("关系症结")
    if not cause or cause in ("暂未提及", ""):
        return None
    return f"回头看这段关系,主要是 {cause}。"


def _card_top_time(db, uid, st, since=None):
    t, _ = state_mod.top_tags(db, uid, since)
    return f"你这段时间的倾诉,大多发生在【{t}】。" if t else None


def _card_top_place(db, uid, st):
    _, p = state_mod.top_tags(db, uid)
    return f"你最常在【{p}】想起 TA。" if p else None


def _card_saddest_day(db, uid, st):
    d = state_mod.saddest_day(db, uid)
    return f"{_fmt_md(d)},是你最难过的一天。" if d else None


def _card_emotion_shift(db, uid, st, since=None):
    a, b = _dominant_emotion_halves(db, uid, since)
    return f"从「{a}」慢慢变成了「{b}」。" if a and b and a != b else None


def _card_first_reconcile(db, uid, st, since=None):
    d = state_mod.first_reconcile(db, uid, since)
    return f"{_fmt_md(d)},你第一次说出了「释怀」。" if d else None


def _card_active_days(db, uid, st, since=None):
    d = state_mod.active_days(db, uid, since)
    return f"这段时间,我们一起走过了 {d} 天。" if d >= 1 else None


def _card_streak(db, uid, st):
    s = state_mod.longest_streak(db, uid)
    return f"你最长连续 {s} 天都来找我。" if s >= 2 else None


def _card_late_nights(db, uid, st):
    n = state_mod.night_count(db, uid)
    return f"你有 {n} 个夜晚,还在想 TA。" if n >= 1 else None


def _card_nudges(db, uid, st):
    n = store.count_nudges(db, uid)
    return f"我在你最难熬的 {n} 个时刻轻轻陪过你。" if n >= 1 else None


def _card_treehole(db, uid, st, since=None):
    n = len(store.list_letters_by_author(db, uid, since))
    return "你写过一封树洞信,把想说的话交给了树洞。" if n >= 1 else None


def _card_replies(db, uid, st):
    n = store.count_replies_delivered(db, uid)
    return f"你给 {n} 个相似经历的人回过信。" if n >= 1 else None


def _card_theme(db, uid, st):
    picks = store.list_daily_picks(db, uid)
    if not picks:
        return None
    top = Counter(p.theme_title for p in picks).most_common(1)[0][0]
    return f"你最常选的疗愈主题是「{top}」。"


def _card_object_trait(db, uid, st):
    obj = store.get_portrait(db, uid, "object") or {}
    trait, name = obj.get("性格"), obj.get("称呼") or "TA"
    return f"在你眼里,{name} 是个{trait}的人。" if trait else None


# 卡目录:(key, 分线, 最低阶段, 文案函数);顺序即优先级。
# 去掉与初始报告重复的「依恋/关系症结」(初始报告已有完整关系复盘),保留这段时间的动态变化
# (情绪转变、第一次释怀)与精选陪伴记录(想起的时间、陪伴天数、倾诉次数、树洞信)。
_CARDS = [
    ("first_reconcile", "情绪", 3, _card_first_reconcile),
    ("emotion_shift", "情绪", 2, _card_emotion_shift),
    ("top_time", "情绪", 0, _card_top_time),
    ("active_days", "时间", 0, _card_active_days),
    ("total_turns", "情绪", 0, _card_total_turns),
    ("treehole", "行为", 0, _card_treehole),
]


def _card_summary(db, uid, st, client: LLMClient, since=None) -> str:
    fallback = f"这段时间,你正从「{st['stage_label']}」里慢慢走出来。"
    if client.mock:
        return fallback
    mem_txt = "\n".join(
        f"- {m.summary or m.content}" for m in store.list_memories(db, uid, since=since)[:12]
    ) or "(暂无)"
    trend_word = "上升" if st["trend"] > 0 else "下降" if st["trend"] < 0 else "平稳"
    prompt = (
        f"用户近期状态:阶段=「{st['stage_label']}」,情绪均分={st['baseline']},趋势={trend_word},"
        f"平静/释怀占比={st['calm_ratio']},急性情绪占比={st['acute_ratio']}。\n"
        f"这段时间 TA 说过的话(按时间顺序,均为过去):\n{mem_txt}\n\n"
        "请用 2~3 句话(总共不超过 150 字),像一位熟悉 TA 的朋友,帮 TA 温和地总结并分析这段时间的历程:"
        "TA 经历了哪些情绪起伏、有什么真实的转变或反复、正在往哪个方向走。"
        "要具体、有信息,点到 TA 说过的真实片段(用「记得你曾说过…」的语气),但不要复述细节、不要编造、不要引导伤感、不评判、不空喊加油。只输出这段话。"
    )
    result = client.chat(
        [{"role": "system", "content": _SUMMARY_SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0.7, model=client.settings.llm_fast_model,
    )
    return (result.get("content") or "").strip() or fallback


def _since_from_prev(prev) -> datetime | None:
    """由上一篇快照推「这段时间」的时间下界(UTC);无上一篇则 None(从有记录起)。"""
    if prev is None:
        return None
    offset = get_settings().timezone_offset_hours
    return datetime.strptime(prev.date_key, "%Y-%m-%d") + timedelta(days=1) - timedelta(hours=offset)


def report_eligibility(db, user_id: str, now: datetime | None = None) -> dict:
    s = get_settings()
    now = now or datetime.now()
    offset = get_settings().timezone_offset_hours
    date_key = (now + timedelta(hours=offset)).strftime("%Y-%m-%d")
    since = _since_from_prev(store.get_previous_snapshot(db, user_id, date_key))
    days = state_mod.active_days(db, user_id, since=since)
    n_mem = len(store.list_memories(db, user_id, since=since))
    eligible = days >= s.report_min_days and n_mem >= s.report_min_memories
    if days < s.report_min_days:
        reason = f"这段时间倾诉还不多(有 {days} 天,需 {s.report_min_days} 天)"
    elif n_mem < s.report_min_memories:
        reason = f"这段时间倾诉还比较少(有 {n_mem} 条,需 {s.report_min_memories} 条)"
    else:
        reason = "可以生成一份跟踪报告了"
    return {"eligible": eligible, "active_days": days, "memories": n_mem,
            "min_days": s.report_min_days, "min_memories": s.report_min_memories, "reason": reason}


def build_report(db, user_id: str, client: LLMClient, now: datetime | None = None) -> dict:
    store.get_or_create_user(db, user_id)
    s = get_settings()
    now = now or datetime.now()

    offset = get_settings().timezone_offset_hours
    date_key = (now + timedelta(hours=offset)).strftime("%Y-%m-%d")
    prev = store.get_previous_snapshot(db, user_id, date_key)
    since = _since_from_prev(prev)

    # 这段时间数据不足 → 不生成(也不落快照,避免把这段窗口算过去)
    days = state_mod.active_days(db, user_id, since=since)
    n_mem = len(store.list_memories(db, user_id, since=since))
    if days < s.report_min_days or n_mem < s.report_min_memories:
        return {"eligible": False, "reason": "这段时间的倾诉还不多,先不生成报告",
                "cards": [], "state": None, "compared": None}

    st = state_mod.compute_state(db, user_id, since=since)

    # 状态总括第一,其余按优先级 + 分线多样性
    cards = [{"key": "summary", "line": "状态", "text": _card_summary(db, user_id, st, client, since=since)}]
    line_count = {"状态": 1}
    max_cards = get_settings().report_max_cards
    for key, line, min_stage, make in _CARDS:
        if len(cards) >= max_cards:
            break
        if st["stage"] < min_stage:
            continue
        if line_count.get(line, 0) >= 3:  # 每线最多 3 张,兼顾多样与"记忆点"
            continue
        text = make(db, user_id, st, since=since)
        if not text:
            continue
        cards.append({"key": key, "line": line, "text": text})
        line_count[line] = line_count.get(line, 0) + 1

    compared = None
    if prev is not None:
        compared = {
            "prev_stage": prev.stage_label,
            "curr_stage": st["stage_label"],
            "stage_up": st["stage"] > prev.stage,
            "baseline_delta": round(st["baseline"] - prev.baseline, 1),
        }
    store.upsert_state_snapshot(db, user_id, date_key, st)
    return {"eligible": True, "reason": "可以生成", "state": st, "cards": cards, "compared": compared}
