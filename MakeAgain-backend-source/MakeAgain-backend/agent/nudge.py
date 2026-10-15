"""软引导提醒 Agent(F2-1):根据用户 memory(情绪节点)+ 本地时间触发温柔的陪伴提醒。

设计要点:
- 拉取式:`get_nudges` 在用户打开 App 时被调用,返回"此刻该触发"的软引导。
- 触发源两类:
  1. 时间触发(late_night):本地 23:00–05:00,提醒"早点休息,别熬夜"。
  2. 情绪节点触发(emotion):用户 memory 里反复出现(frequency≥2)的时间标签(如"晚上"/"周六")
     命中当前时刻,生成"换一种方式度过这个时刻"的软引导。
- 去重:NudgeLog 表,同一 user + rule_key + 本地日期每天最多一次,避免烦人。
- 文案用 fast 模型(deepseek-chat)生成,并注入命中标签下的真实记忆,让文案具体不模板;
  失败时模板兜底;纯本地时间计算零 LLM 延迟。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from config import get_settings
from emotion.tone import SHARED_RULE, pick_tone
from gateway.client import LLMClient
from memory import store

_NIGHT_TAGS = {"凌晨", "深夜", "夜里", "晚上"}

# 无情绪节点/事件触发时的「语录」填充(占位示例,待产品提供正式语录后替换;后续可按 loss_type 分类)
QUOTES = [
    "有些日子不必勉强自己振作,照顾好今天的自己就很好。",
    "难过的时候,允许自己慢慢来。",
    "你不需要一下子好起来,一步一步走就很了不起。",
    "此刻的安静,也是在好好休息。",
    "把日子过成自己的节奏,不必和任何人比较。",
]


def _now_tags(now: datetime) -> set[str]:
    """当前本地时刻命中的时间标签集合(口径与 extract.py 的 time_tag 一致)。"""
    tags: set[str] = set()
    h = now.hour
    wd = now.weekday()  # 0=周一
    if h < 5:
        tags |= {"凌晨", "深夜", "夜里"}
    elif h < 10:
        tags |= {"早上", "上午"}
    elif h < 12:
        tags.add("上午")
    elif h < 14:
        tags.add("中午")
    elif h < 18:
        tags.add("下午")
    elif h < 19:
        tags.add("傍晚")
    else:
        tags |= {"晚上", "夜里", "深夜"}
    if wd >= 5:
        tags.add("周末")
    if wd == 5:
        tags.add("周六")
    if wd == 6:
        tags.add("周日")
    return tags


def _is_late_night(now: datetime) -> bool:
    return now.hour >= 23 or now.hour < 5


def _local_now(now: datetime | None = None) -> datetime:
    s = get_settings()
    base = now or datetime.now().replace(microsecond=0)
    return base + timedelta(hours=s.timezone_offset_hours)


def _memory_evidence(db, user_id: str, time_tag: str) -> str:
    """取该时间标签下最近的记忆片段,作为文案生成依据(让提醒具体、非模板)。"""
    entries = store.list_memories_by_tag(db, user_id, time_tag, limit=2)
    lines = [f"- {e.summary or e.content}" for e in entries if (e.summary or e.content)]
    return "\n".join(lines) if lines else "(暂无具体记忆)"


def _build_candidates(db, user_id: str, now: datetime, breakup: bool = False) -> list[dict]:
    """纯本地计算,产出本轮应触发的候选规则(不含文案)。"""
    tags = _now_tags(now)
    late_night = _is_late_night(now)
    candidates: list[dict] = []

    # 情绪节点触发:按 time_tag 聚合(同一时刻可能命中多个情绪节点),每时刻只出一条候选
    agg: dict[str, dict] = {}
    for node in store.list_emotion_nodes(db, user_id):
        if not node.time_tag or node.time_tag not in tags:
            continue
        a = agg.setdefault(node.time_tag, {"freq": 0, "emotions": []})
        a["freq"] += node.frequency
        a["emotions"].append(node.emotion)

    for time_tag, a in agg.items():
        if a["freq"] < 2:
            continue
        emotion = max(set(a["emotions"]), key=a["emotions"].count)  # 该时刻的主导情绪
        is_night = time_tag in _NIGHT_TAGS
        evidence = _memory_evidence(db, user_id, time_tag)
        candidates.append({
            "rule_key": f"emotion:{time_tag}",
            "type": "emotion",
            "trigger": time_tag,
            "emotion": emotion,
            "frequency": a["freq"],
            "is_night": is_night,
            "evidence": evidence,
            "breakup": breakup,
        })

    # 时间触发:深夜且"没有命中任何夜间情绪节点"时才单独提醒,
    # 避免和"晚上/深夜"节点的软引导重复("夜深了"说两遍)。
    has_night_node = any(c["is_night"] for c in candidates)
    if late_night and not has_night_node:
        candidates.append({
            "rule_key": "late_night",
            "type": "time",
            "trigger": "深夜",
            "emotion": None,
            "frequency": 0,
            "is_night": True,
            "evidence": _memory_evidence(db, user_id, "晚上"),
            "breakup": breakup,
        })

    return candidates


def _fallback_text(c: dict, tone_code: str = "soothe", day_seed: int = 0) -> str:
    """模板兜底:以「一件具体的小事」为主,按 trigger + 日期轮换;分手回看仅在状态较稳(guide)时作为可选动作。"""
    if c["type"] == "time":
        return "夜深了,别让思绪陪你熬太晚。今晚早点休息,好吗?"
    trigger = c["trigger"]
    actions = [
        f"又到{trigger}了。出去走十分钟,透透气,好吗?",
        f"到{trigger}了,给自己泡杯热茶,早点躺下,好吗?",
        f"又到{trigger}了。写两行今天的心情,把它交给纸笔,好吗?",
        f"这个时刻容易想太多,去洗个热水澡,让身体先放松下来,好吗?",
        f"又到{trigger}了。听首喜欢的歌,或给朋友发条消息,别一个人闷着,好吗?",
    ]
    if c.get("breakup") and tone_code == "guide":
        actions = actions + [
            f"又到{trigger}了。有空的话,写下你现在最放不下的那件事,再试着轻轻回看这段关系,好吗?",
        ]
    return actions[(len(trigger) + day_seed) % len(actions)]


NUDGE_SYSTEM = """你是「重逢」的陪伴提醒助手。根据用户当前的时间、TA 反复出现的情绪节点,以及节点下的真实记忆,生成一句温柔、简短、不评判的软引导提醒。

原则:
1. 不是命令、不是说教、不空喊"加油/振作";语气像一位关心 TA 的朋友,轻轻给一个具体的建议就好,不频繁催促。
2. 文案以「轻轻建议一件具体的小事」为主:给 TA 一个此刻就能做的、很小的动作(如"出去走十分钟""泡杯热茶早点躺下""写两行今天的心情"),用建议的口吻(句尾"好吗/要不要"),不要空泛地安慰、也不要抽象地评价"你已经走出来了"。每次换一个具体动作、换一种说法,不要天天重复同一个建议,避免让 TA 感到被反复催促、被厌烦。
3. 尽量贴合 TA 的真实记忆来写,但不要生硬复述记忆细节,点到即可。
4. 一句话即可,不超过 50 字;不要带称呼,不要问太多问题。
5. 熬夜提醒要温柔,不要说"快去睡"这种命令;同一个时刻只提醒一次,不要重复。
6. 今天是哪一天、现在是什么时刻,一律以给出的"当前时间"为准。用户记忆里提到的星期(如"周六")是过去的习惯,不是今天——千万不要把记忆里的"周六"当成今天写进文案。

[[BREAKUP_HINT]]

[[TONE]]

[[RULE]]

只输出 JSON,结构如下:
{"nudges": ["文案1", "文案2", "..."]}
nudges 是一个字符串数组,长度必须与给你的候选提醒数量一致,且顺序一一对应。"""


_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _local_day_str(local: datetime) -> str:
    return f"{local.strftime('%m月%d日')} {_WEEKDAYS[local.weekday()]} {local.strftime('%H:%M')}"


def _generate_texts(client: LLMClient, candidates: list[dict], now: datetime, tone_prompt: str,
                    breakup: bool = False, tone_code: str = "soothe") -> dict[str, str]:
    """一次 LLM 调用为所有候选生成文案(按位置对应,避免依赖模型回传 rule_key);失败时逐条模板兜底。"""
    day_seed = now.day  # 按天轮换兜底文案,避免同一个人天天看到同一句
    if client.mock:
        return {c["rule_key"]: _fallback_text(c, tone_code, day_seed) for c in candidates}

    breakup_hint = (
        "6. 用户是分手场景:仅当 TA 状态较稳时才极轻地带一句\"试着客观看看这段关系\"的方向,"
        "不要每次都说、不要催促;急性情绪时不要提。"
        if breakup else ""
    )
    system = (NUDGE_SYSTEM.replace("[[BREAKUP_HINT]]", breakup_hint)
              .replace("[[TONE]]", tone_prompt).replace("[[RULE]]", SHARED_RULE))
    brief = "\n".join(
        f"{i + 1}. 时刻={c['trigger']} | 情绪={c['emotion'] or '无'} | 出现次数={c['frequency']}\n"
        f"   相关记忆:\n{c['evidence']}"
        for i, c in enumerate(candidates)
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"当前时间: {_local_day_str(now)}\n候选提醒({len(candidates)} 条):\n{brief}"},
    ]
    parsed, _ = client.chat_json(messages, temperature=0.6, model=client.settings.llm_fast_model)

    texts: list[str] = []
    if parsed and isinstance(parsed.get("nudges"), list):
        texts = [str(t).strip() for t in parsed["nudges"] if isinstance(t, str) and t.strip()]

    result: dict[str, str] = {}
    for i, c in enumerate(candidates):
        result[c["rule_key"]] = texts[i] if i < len(texts) else _fallback_text(c, tone_code, day_seed)
    return result


def _quote_fallback(db, user_id: str, local: datetime, date_key: str) -> dict:
    """无情绪节点/事件触发时用「语录」填充,保证打开 App 仍有陪伴;每天最多一条,按天轮换。"""
    now_str = local.strftime("%Y-%m-%d %H:%M")
    if not QUOTES:
        return {"now": now_str, "nudges": []}
    if store.nudge_seen(db, user_id, "quote", date_key):
        return {"now": now_str, "nudges": []}
    quote = QUOTES[local.toordinal() % len(QUOTES)]
    store.mark_nudge(db, user_id, "quote", date_key)
    return {"now": now_str, "nudges": [{
        "rule_key": "quote", "type": "quote", "trigger": None, "emotion": None, "text": quote,
    }]}


def get_nudges(db, user_id: str, client: LLMClient, now: datetime | None = None) -> dict:
    """拉取式软引导:计算候选 → 过滤已触达 → 生成文案 → 标记已触达 → 返回。"""
    store.get_or_create_user(db, user_id)
    local = _local_now(now)
    date_key = local.strftime("%Y-%m-%d")
    is_breakup = store.effective_loss_type(db, user_id) == "breakup"
    candidates = _build_candidates(db, user_id, local, breakup=is_breakup)

    # 过滤今天已触达过的
    due = [c for c in candidates if not store.nudge_seen(db, user_id, c["rule_key"], date_key)]
    if not due:
        return _quote_fallback(db, user_id, local, date_key)

    tone = pick_tone(db, user_id)
    tone_code = tone["tone"]
    day_seed = local.day
    texts = _generate_texts(client, due, local, tone["prompt"], breakup=is_breakup,
                            tone_code=tone_code)
    nudges = []
    for c in due:
        nudges.append({
            "rule_key": c["rule_key"],
            "type": c["type"],
            "trigger": c["trigger"],
            "emotion": c["emotion"],
            "text": texts.get(c["rule_key"]) or _fallback_text(c, tone_code, day_seed),
        })
        store.mark_nudge(db, user_id, c["rule_key"], date_key)

    return {"now": local.strftime("%Y-%m-%d %H:%M"), "nudges": nudges}
