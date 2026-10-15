"""语气路由:根据用户近期情绪状态 + 丧失类型,在「温柔安抚型」与「智者引导型」间选择。

原则(用户明确反馈):
- 产品总目标是帮用户"走出来",不要引导用户去体会/放大伤感。
- 情绪低沉、急性负向 → 温柔安抚(先承接陪伴,不急着讲道理)。
- 状态相对稳定/向好 → 智者引导(温和笃定,把注意力引向已经走过来的进步与往前走的方向)。
- 丧失类型调节:死亡类丧失(亲友离世/宠物离世)的哀伤是长期乃至终身的,
  默认更偏安抚/陪伴,不轻易"引导向前";分手(关系结束)才给"向前"留更多空间。
"""
from __future__ import annotations

from memory import store

SOOTHE = "soothe"   # 温柔安抚型
GUIDE = "guide"     # 智者引导型
ANALYZE = "analyze"  # 旁观者分析型(仅分手场景:平衡归因 + 依恋理论 + 判断关系的建议)

# 急性负向情绪 → 优先安抚
ACUTE_EMOTIONS = {"难过", "孤独", "焦虑", "恐惧", "愤怒"}

# 死亡类丧失:亲友离世(loved_one,含亲人+挚友)、宠物离世(pet)。
# 保留 legacy 值 "family"(早期亲人离世)以兼容存量数据。
DEATH_LOSS_TYPES = {"loved_one", "family", "pet"}

# 全局红线:不引导伤感 + 不编造时间对比
SHARED_RULE = (
    "不要引导用户去体会或放大伤感(如「现在路过,是不是空落落的」「是不是很难受」「心里是不是很痛」);"
    "不要主动追问痛苦细节;把话题轻轻引向当下能做的、已经变好的、继续往前走的方向。"
    "不要凭空编造时间对比(如「比一个月前的自己…」「那时候你连白天都熬不住」):"
    "只有上下文里真实提供的时间依据(「近期状态轨迹」「相关往事」)才能用来做时间对比;"
    "没有依据时,就用当下这句话里的具体变化来鼓励,不要编一个过去的状态。"
)

TONE_PROMPTS = {
    SOOTHE: (
        "语气定位【温柔安抚型】:先承接情绪、给予陪伴与安全感,像轻轻拍着后背的朋友;"
        "不急着讲道理、不催促改变;即便要往前走,也说得慢一点、软一点。"
    ),
    GUIDE: (
        "语气定位【智者引导型】:温和而笃定,用新的视角轻轻点醒;"
        "把注意力引向已经走过来的进步、当下能做的小事、继续往前走的方向,像一位有智慧的长者。"
    ),
    ANALYZE: (
        "语气定位【旁观者分析型】:像一位懂心理学、不站边的旁观者,基于用户画像与记忆里的真实信息,"
        "客观拆解这段关系为什么走到这一步——既看到对方的部分,也温柔点出用户自己可改变的部分,"
        "用去指责化的语言,给可操作的建议;只做关系分析,不下心理疾病诊断;情绪仍不评判。"
        "若用户是被伤害的一方(被出轨/家暴/冷暴力/PUA/被抛弃),不做任何归咎,"
        "转而共情 + 帮助识别不健康关系、重建自我。"
    ),
}


# 归因分析意图(零 LLM 规则匹配,仿 crisis.py):用户主动想知道"为什么/是不是我的错"
_ANALYZE_INTENT = (
    "为什么", "是不是我的错", "是不是我的问题", "我的错", "我的问题", "谁的问题",
    "分析", "复盘", "归因", "依恋", "回避型", "焦虑型", "安全型", "恐惧型",
    "分手原因", "这段关系", "帮我看看", "帮我看", "他怎么会", "她怎么会", "是不是我",
)

# 受害者信号(零 LLM):用户在关系中被伤害。方向化:先排除"我出轨/我劈腿"这类自述加害,再命中被动/他方信号。
_VICTIM_HINTS = (
    "被出轨", "被劈腿", "被绿", "绿了我", "出轨", "劈腿",
    "家暴", "打了我", "动手", "冷暴力", "pua", "被抛弃", "抛弃了我",
    "甩了我", "无缝衔接", "控制欲", "精神控制", "被操纵", "被利用",
)
_SELF_ATTRIB = ("我出轨", "我劈腿")


def detect_analyze_intent(message: str) -> bool:
    """用户当前消息是否在寻求归因分析(零 LLM)。"""
    text = message or ""
    return any(k in text for k in _ANALYZE_INTENT)


def detect_victim_signals(text: str) -> list[str]:
    """检测文本中的受害者信号词;命中自述加害(我出轨/我劈腿)时返回空(不是受害者)。"""
    t = (text or "").lower()
    if any(s in t for s in _SELF_ATTRIB):
        return []
    return [w for w in _VICTIM_HINTS if w in t]


def pick_tone(db, user_id: str, loss_type: str | None = None, message: str | None = None) -> dict:
    """基于丧失类型 + 主导情绪 + 最近情绪均分选择语气(纯本地,零 LLM)。

    - 死亡类丧失(亲友/宠物离世):默认安抚/陪伴,只有状态明显稳定(均分 ≥ 60)才引导向前。
    - 分手(关系结束):引导向前的门槛更低(均分 ≥ 45);用户主动求因 + 状态稳定时切换「旁观者分析型」。
    """
    if loss_type is None:
        store.get_or_create_user(db, user_id)
        loss_type = store.effective_loss_type(db, user_id)
    death_loss = loss_type in DEATH_LOSS_TYPES

    nodes = store.list_emotion_nodes(db, user_id)
    dominant = nodes[0].emotion if nodes else None

    mems = store.list_memories(db, user_id)[:7]
    scores = [m.emotion.get("score") for m in mems if isinstance(m.emotion, dict) and "score" in m.emotion]
    avg = sum(scores) / len(scores) if scores else 50.0

    # 分手场景「旁观者分析」:用户明确求因即可;仅急性情绪(主导情绪为难过/愤怒等)先拦下安抚(优先级最高)。
    if loss_type == "breakup" and message and detect_analyze_intent(message):
        if dominant not in ACUTE_EMOTIONS:
            return {"tone": ANALYZE, "prompt": TONE_PROMPTS[ANALYZE],
                    "reason": "用户主动寻求归因分析,切换到旁观者视角",
                    "avg_score": round(avg, 1)}

    if not scores:
        tone, reason = SOOTHE, "暂无足够情绪数据,先安抚陪伴"
    elif dominant in ACUTE_EMOTIONS:
        tone, reason = SOOTHE, f"近期处于急性负向情绪(主导「{dominant}」),先安抚陪伴"
    else:
        threshold = 60 if death_loss else 45
        if avg < threshold:
            if death_loss:
                reason = f"亲友/宠物离世的思念是长期乃至终身的(均分 {avg:.0f}),先陪伴,不急着往前"
            else:
                reason = f"近期情绪偏低沉(均分 {avg:.0f}),先安抚陪伴"
            tone = SOOTHE
        else:
            tone, reason = GUIDE, f"状态相对稳定(均分 {avg:.0f}),适合温和引导向前"
    return {"tone": tone, "prompt": TONE_PROMPTS[tone], "reason": reason, "avg_score": round(avg, 1)}
