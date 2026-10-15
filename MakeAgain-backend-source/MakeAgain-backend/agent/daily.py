"""每日主题 + 启发文案(F2-2 日常陪伴的开场):个性化推荐主题,附一句简短的总开场文案。

设计要点:
- 主题选择零 LLM:从疗愈主题库按 loss_type + 语气路由(tone)规则打分,返回 top N,并按日期轻微轮换避免天天一样。
- 启发文案零 LLM、纯模板:取今日推荐主题的前两个,拼成一句简短文案(把两个主题都带进去),给用户两个可选方向,不强制选一个。
- 依据心情体现在「选哪两个主题」:get_themes 已按语气/主导情绪路由打分,文案本身只是短句拼接。
- 文案落 DailyPick(同一 user+date_key 幂等覆盖),供陪伴 Agent 注入当天上下文。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from config import get_settings
from emotion.tone import GUIDE, SHARED_RULE, SOOTHE, pick_tone
from gateway.client import LLMClient
from memory import store

# 疗愈主题库(固定池;desc/hint 都往"往前走"收,而不是反复咀嚼失去)
THEMES = [
    {"key": "memory", "title": "最近总想起的那些", "desc": "想起TA的时候，有难过，也常有还温热的部分。有没有哪一次，是带着一点点暖意想起来的？",
     "hint": "引导用户从回忆里提炼温暖与力量,把落点放在'这段经历带给你的好',而不是停留在遗憾与缺失"},
    {"key": "farewell", "title": "没说完的话", "desc": "有些话当时没来得及说，就一直搁在心里了。今天想挑一句，轻轻说出来，好吗？",
     "hint": "帮助用户说出未说的话并温柔收束:说出口 → 慢慢放下,而非反复咀嚼离别场景"},
    {"key": "selfcare", "title": "对自己好一点", "desc": "最近你一直先照顾别人。今天换过来，为自己做一件温柔的小事，好吗？",
     "hint": "把注意力引向照顾自己:睡好、吃好、做一件让自己舒服的小事"},
    {"key": "newlife", "title": "接下来的日子", "desc": "如果带着这些记忆继续往前走，你希望接下来的日子，慢慢长成什么样子？",
     "hint": "引导用户想象并描述'带着记忆继续生活'的具体样子,给希望与方向"},
    {"key": "letter", "title": "想对TA说的话", "desc": "如果写一段话给TA，你想从哪里说起？写完它，我们再轻轻回到自己。",
     "hint": "邀请用户写一段想对TA说的话,并在结尾轻轻带向'写完后,回到自己'"},
    {"key": "gratitude", "title": "记得那些好", "desc": "这段关系给过你的好，有没有哪一份，是你一直记得、舍不得丢掉的？",
     "hint": "引导用户回忆这段关系带来的美好与成长,而不是失去本身"},
    {"key": "anniversary", "title": "特别的日子", "desc": "今天是个特别的日子。想不想用一个小仪式，好好纪念那个时刻？",
     "hint": "引导用户用一个温暖的小仪式去纪念,把悲伤转化为有温度的怀念"},
    # 分手专属:旁观者归因 + 自我觉察(仅 loss_type=breakup 进入推荐池)
    {"key": "insight", "title": "看懂这段关系", "desc": "如果你站在旁观者的角度回看这段关系，你会怎么讲它的来龙去脉？",
     "loss": ["breakup"],
     "hint": "引导用户客观复盘关系为什么走到这一步:既看到对方的部分,也温柔点出自己可改变的部分,用去指责化的语言,落点在'看懂与成长'而非反复指责"},
    {"key": "growth", "title": "我在关系里的样子", "desc": "在这段关系里，你看到的自己是什么样子的？有没有哪种相处方式，你想重新认识一下？",
     "loss": ["breakup"],
     "hint": "引导用户觉察自己在关系里的依恋倾向与相处模式(如回避、焦虑、迁就),把落点放在'更好地认识自己、下一段关系更从容'"},
]

# 按语气路由的推荐加权:安抚型 → 关怀与表达;引导型 → 继续生活与感恩
_BOOST = {
    SOOTHE: {"selfcare": 2.0, "letter": 2.0, "gratitude": 2.0, "newlife": 1.0},
    GUIDE: {"newlife": 2.0, "gratitude": 2.0, "farewell": 2.0, "selfcare": 0.5,
            "insight": 1.5, "growth": 1.5},
}

# 主题 key → 完整条目(取 hint 作为个性化引导的「主题方向」依据)
_THEME_BY_KEY = {t["key"]: t for t in THEMES}


# 个性化开场引导:把「主题方向 + 最近状态 + 真实记忆」织成一句具体不模板的邀请
THEME_GUIDE_SYSTEM = """你是「重逢」的陪伴助手。今天给用户推荐了一个疗愈主题,请基于 TA 最近的情绪状态和真实记忆,写一句温柔、具体、不评判的开场引导,邀请 TA 就这个主题轻轻开口。

原则:
1. 一句话即可,不超过 60 字;不要带称呼,不要命令、说教、空喊"加油/振作"。
2. 必须贴合给出的「主题方向」来写,把落点放在往前走、看见当下能做的事或已经变好的地方,而不是反复咀嚼失去或放大伤感。
3. 自然地带上 TA 真实记忆里的一两个细节(点到即可,不要生硬复述,更不要编造 TA 没说过的事),让 TA 感到被记得。
4. 语气贴合给出的「语气定位」。
5. 不要凭空编造时间对比;只有上下文里真实提供的信息才能用来做对比。

[[TONE]]

[[RULE]]

只输出一句引导文案本身,不要输出 JSON、不要加引号、不要加解释。"""

def _today_key(now: datetime | None = None) -> str:
    s = get_settings()
    base = now or datetime.now().replace(microsecond=0)
    local = base + timedelta(hours=s.timezone_offset_hours)
    return local.strftime("%Y-%m-%d")


def _dominant_emotion(db, user_id: str) -> str | None:
    nodes = store.list_emotion_nodes(db, user_id)
    return nodes[0].emotion if nodes else None


def _score_theme(theme: dict, loss_type: str | None, tone: str) -> float:
    s = 1.0  # 基础分,保证都有机会
    loss = theme.get("loss")
    if not loss or (loss_type and loss_type in loss):
        s += 0.5
    s += _BOOST.get(tone, {}).get(theme["key"], 0.0)
    return s


def get_themes(db, user_id: str, now: datetime | None = None) -> dict:
    """返回今天个性化推荐的若干主题(零 LLM,语气路由 + 每日轻微轮换)。"""
    store.get_or_create_user(db, user_id)
    tone = pick_tone(db, user_id)
    loss_type = store.effective_loss_type(db, user_id)

    pool_themes = [t for t in THEMES if not t.get("loss") or (loss_type and loss_type in t["loss"])]
    ranked = sorted(pool_themes, key=lambda t: _score_theme(t, loss_type, tone["tone"]), reverse=True)
    count = get_settings().daily_theme_count

    # 在推荐带里按日期轻微轮换,避免连续几天完全相同
    pool = ranked[:min(len(ranked), count + 2)]
    seed = int(_today_key(now)[-2:])
    k = seed % len(pool)
    pool = pool[k:] + pool[:k]
    themes = [{"key": t["key"], "title": t["title"], "desc": t["desc"]} for t in pool[:count]]

    reason = (
        "最近你可能需要一点安慰,这几个主题轻轻陪你。"
        if tone["tone"] == SOOTHE
        else "看你最近状态稳一些了,这几个主题帮你再往前走走。"
    )
    return {"themes": themes, "reason": reason, "tone": tone["tone"]}


def _opening_text(themes: list[dict]) -> str:
    """一句简短文案:把两个主题都带进去,给用户两个可选方向(不强制选一个)。"""
    t1 = themes[0]["title"] if len(themes) > 0 else "想聊的事"
    t2 = themes[1]["title"] if len(themes) > 1 else "现在的心情"
    return f"今天想聊聊「{t1}」,还是「{t2}」?"


def _pick_two_themes(db, user_id: str, now: datetime | None = None) -> list[dict]:
    """取今日推荐主题的前两个(与 get_themes 同源、同日一致),供启发文案引用。"""
    return get_themes(db, user_id, now=now)["themes"][:2]


def generate_opening(db, user_id: str, now: datetime | None = None) -> dict:
    """生成今日启发文案:一句简短文案,把今日推荐主题的前两个都带进去(零 LLM、纯模板)。

    依据心情体现在「选哪两个主题」(get_themes 已按语气/主导情绪路由打分),文案本身只是短句拼接。
    返回 {opening, themes, mood, tone};当天已生成过则直接复用(每日一次)。
    """
    store.get_or_create_user(db, user_id)
    tone = pick_tone(db, user_id)
    date_key = _today_key(now)

    existing = store.get_daily_pick(db, user_id, date_key)
    if existing is not None and existing.opening:
        return {"opening": existing.opening, "themes": _pick_two_themes(db, user_id, now),
                "mood": _dominant_emotion(db, user_id), "tone": tone["tone"], "cached": True}

    mood = _dominant_emotion(db, user_id)
    themes = _pick_two_themes(db, user_id, now)
    opening = _opening_text(themes)

    store.upsert_daily_pick(db, user_id, date_key, "", "", opening)

    return {"opening": opening, "themes": themes, "mood": mood, "tone": tone["tone"]}


def generate_theme_guide(db, user_id: str, client: LLMClient, now: datetime | None = None) -> dict:
    """为今日推荐主题生成一句个性化开场引导(LLM + 最近状态 + 真实记忆)。

    - 有真实记忆时:用 fast 模型把「主题方向 + 主导情绪 + 记忆片段」织成一句具体不模板的引导。
    - 无记忆 / mock / LLM 失败:回落主题自带的 desc(固定暖场文案)。
    - 每日幂等缓存到 DailyPick.theme_guide,当天二次访问不再调 LLM。
    返回 {theme, guide}。
    """
    store.get_or_create_user(db, user_id)
    themes = get_themes(db, user_id, now=now)["themes"]
    if not themes:
        return {"theme": None, "guide": None}
    theme = themes[0]
    date_key = _today_key(now)

    cached = store.get_theme_guide(db, user_id, date_key)
    if cached:
        return {"theme": theme, "guide": cached}

    # 真实记忆片段(最近 5 条)是「个性化而非固定」的依据;没有则直接回落 desc,不空跑 LLM。
    mems = store.list_memories(db, user_id)[:5]
    memory_lines = [f"- {e.summary or e.content}" for e in mems if (e.summary or e.content)]
    if not memory_lines or client.mock:
        return {"theme": theme, "guide": theme["desc"]}

    tone = pick_tone(db, user_id)
    dominant = _dominant_emotion(db, user_id)
    hint = _THEME_BY_KEY.get(theme["key"], {}).get("hint", "")
    system = (THEME_GUIDE_SYSTEM.replace("[[TONE]]", tone["prompt"])
              .replace("[[RULE]]", SHARED_RULE))
    brief = (
        f"今日推荐主题:{theme['title']}\n"
        f"主题方向:{hint}\n"
        f"最近主导情绪:{dominant or '暂无'}\n"
        f"TA 的真实记忆片段:\n" + "\n".join(memory_lines)
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": brief},
    ]
    try:
        guide = client.chat(messages, temperature=0.6,
                            model=client.settings.llm_fast_model)["content"].strip()
    except Exception:
        guide = ""
    if not guide:
        return {"theme": theme, "guide": theme["desc"]}

    store.upsert_theme_guide(db, user_id, date_key, theme["key"], theme["title"], guide)
    return {"theme": theme, "guide": guide}
