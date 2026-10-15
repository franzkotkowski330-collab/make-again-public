"""亲友离世场景的哀伤导向抽取:在通用事实/情绪抽取之上,额外抽哀伤过程字段。

对齐 LEKIA 2.0 认知层「过程证据」(Process Evidence)的丧亲版:
- 失恋抽「自动思维 / 认知扭曲 / 反思就绪 / 功能恢复 / 反刍」;
- 丧亲把「反刍」替换为「渴望思念(yearning)」,并新增丧亲特有的
  「死亡现实接受 / 意义重建 / 持续联结质量 / 内疚与未竟之事」。

字段:
- automatic_thoughts: 隐含的自动思维/核心信念(如「都怪我」「如果那天我陪着他就不会」)
- cognitive_distortions: 认知扭曲类型(个人化/灾难化/过度概括等)
- reflective_readiness: 反思就绪度 0-1(能否理性看待死亡)
- meaning_reconstruction: 意义重建 0-1(能否给死亡一个说法,Neimeyer)
- death_acceptance: 死亡现实接受 0-1(Worden 任务1)
- continuing_bond: 持续联结质量 0-1(0=冻结/沉溺,1=健康的「TA活在心里」,Klass)
- functional_signal: 功能恢复信号 0-1(Track I / DPM 恢复导向)
- yearning / guilt / denial: 零 LLM 词典判(渴望思念 / 内疚未竟之事 / 否认死亡)
"""
from __future__ import annotations

from memory import extract as base_extract

# 认知扭曲类型(标准 CBT 术语)
COGNITIVE_DISTORTIONS = (
    "灾难化", "全有或全无", "过度概括", "心理过滤", "个人化",
    "读心术", "贴标签", "应该陈述", "情绪化推理", "贬低积极",
)

# 渴望/思念(零 LLM,PGD 核心症状):还想再见到 TA、反复想起。
# 注:「想念」无歧义;「想他/想她」在哀伤语境里绝大多数是「思念」而非「认为」,带否定守卫兜「不想他」。
_YEARNING_HINTS = (
    "想念", "想他", "想她", "好想他", "好想她", "好想再见", "好想你", "很想他", "很想她",
    "想见他", "想见她", "想再见", "梦见", "再也见不到", "见不到了",
    "翻他照片", "翻她照片", "看他的照片", "看她的照片", "他的声音", "她的声音",
)

# 内疚/未竟之事(零 LLM,丧亲特有):自责、反事实、没来得及
_GUILT_HINTS = (
    "都怪我", "怪我", "我要是", "要是我", "如果我", "如果那天", "如果当时",
    "是不是就不会", "早知道", "没来得及", "没见到最后一面", "最后一面",
    "我应该", "是我没", "后悔", "对不起他", "对不起她",
)

# 否认死亡(零 LLM,Worden 任务1 未完成):还没接受 TA 已离世
_DENIAL_HINTS = (
    "总觉得他还在", "总觉得她还在", "总觉得他", "总觉得她",
    "只是出差", "还会回来", "推门进来", "假装", "接受不了",
    "不愿相信", "不像是真的", "他还活着", "她还活着",
)

# 功能恢复信号(零 LLM,Track I):作息/社交/新生活
_FUNCTIONAL_HINTS = (
    "新朋友", "开始运动", "健身", "跑步", "看书", "学习", "旅行", "重新开始",
    "报名", "参加了", "上班", "忙起来", "走出来", "好多了", "想开了", "重新上班",
)

_GRIEF_SYSTEM = "你是丧亲哀伤过程的观察员,只输出 JSON,不要多余文字。"


def extract_grief_cbt(client, text: str) -> dict:
    """抽取丧亲 CBT/哀伤过程字段。mock / LLM 失败时降级为词典兜底。"""
    if client.mock:
        return lexicon_grief(text)

    yearning = _hits(text, _YEARNING_HINTS)
    guilt = _hits(text, _GUILT_HINTS)
    denial = _hits(text, _DENIAL_HINTS)

    messages = [
        {"role": "system", "content": _GRIEF_SYSTEM},
        {"role": "user", "content": (
            "从下面的丧亲倾诉中抽取哀伤过程信息,输出 JSON:\n"
            "{\"automatic_thoughts\": [\"用户话语里隐含的自动思维/核心信念,如'都怪我''如果那天我陪着他就不会''我不该说那句话',没有则为空数组\"],\n"
            " \"cognitive_distortions\": [\"认知扭曲类型,只能从:" + "/".join(COGNITIVE_DISTORTIONS) + " 里选,没有则为空数组\"],\n"
            " \"reflective_readiness\": 0到1(能否理性看待死亡、能否站旁观者视角,0=纯情绪宣泄/纯自责,1=能客观分析),\n"
            " \"meaning_reconstruction\": 0到1(能否给死亡一个说法/意义,如'他走得不痛苦''他教会了我珍惜',0=完全想不通为什么,1=已找到意义),\n"
            " \"death_acceptance\": 0到1(是否接受 TA 已离世这一现实,0=否认/总觉得 TA 还在,1=已接受),\n"
            " \"continuing_bond\": 0到1(与逝者的持续联结质量,0=冻结/沉溺(我永远停在失去TA那天),1=健康联结(TA活在我心里、我用纪念的方式继续爱TA)),\n"
            " \"functional_signal\": 0到1(有无恢复功能迹象:作息/社交/工作/新生活,0=完全无,1=有明显迹象)}\n\n"
            f"文本:\n{text}"
        )},
    ]
    parsed, _ = client.chat_json(messages, temperature=0.2, model=client.settings.llm_fast_model)
    if not parsed or not isinstance(parsed, dict):
        return lexicon_grief(text)
    return {**_normalize(parsed), "yearning": yearning, "guilt": guilt, "denial": denial}


def _normalize(d: dict) -> dict:
    def _clamp01(x, default=0.5):
        try:
            return max(0.0, min(1.0, float(x)))
        except (TypeError, ValueError):
            return default

    thoughts = [str(t) for t in (d.get("automatic_thoughts") or []) if t]
    distortions = [str(t) for t in (d.get("cognitive_distortions") or []) if t]
    # 硬校验:扭曲类型必须落在标准词表内,防 LLM 造词
    distortions = [t for t in distortions if t in COGNITIVE_DISTORTIONS]
    return {
        "automatic_thoughts": thoughts,
        "cognitive_distortions": distortions,
        "reflective_readiness": _clamp01(d.get("reflective_readiness"), 0.3),
        "meaning_reconstruction": _clamp01(d.get("meaning_reconstruction"), 0.5),
        "death_acceptance": _clamp01(d.get("death_acceptance"), 0.5),
        "continuing_bond": _clamp01(d.get("continuing_bond"), 0.5),
        "functional_signal": _clamp01(d.get("functional_signal"), 0.0),
    }


# 否定守卫:如「不怪自己了」「不再想他」不应计为相应信号。
# 单字否定有子串碰撞:『特别』的『别』『未来』的『未』都不是否定,先排除。
_SIGNAL_NEG = ("很少", "不太", "不怎么", "不再", "几乎不", "不会", "没怎么", "没", "别", "不", "未")


def _negated(pre: str) -> bool:
    if pre.endswith("特别") or pre.endswith("未来"):
        return False
    return any(n in pre for n in _SIGNAL_NEG)


def _hits(text: str, hints: tuple[str, ...]) -> bool:
    t = text or ""
    for k in hints:
        i = t.find(k)
        while i != -1:
            pre = t[max(0, i - 3):i]
            if not _negated(pre):
                return True
            i = t.find(k, i + 1)
    return False


def lexicon_grief(text: str) -> dict:
    """零 LLM 词典兜底:命中词表粗判(含渴望/内疚/否认,带否定守卫)。供 mock 降级与评估预热复用。"""
    functional = _hits(text, _FUNCTIONAL_HINTS)
    denial = _hits(text, _DENIAL_HINTS)
    return {
        "automatic_thoughts": [],
        "cognitive_distortions": [],
        "reflective_readiness": 0.3 if functional else 0.1,
        "meaning_reconstruction": 0.5,
        "death_acceptance": 0.2 if denial else 0.5,
        "continuing_bond": 0.5,
        "functional_signal": 0.6 if functional else 0.0,
        "yearning": _hits(text, _YEARNING_HINTS),
        "guilt": _hits(text, _GUILT_HINTS),
        "denial": denial,
    }


# 丧亲场景的思念对象提示:让通用抽取的 summary/facts 用「亲人/逝者」而非失恋的「前任/前女友」。
_OBJECT_HINT = (
    "这是亲友离世场景,倾诉里的思念对象是逝去的亲人(母亲/父亲/配偶/子女/长辈),"
    "不是前任/前女友/前男友。概括 summary 和写 facts 时,用文本里出现的真实关系词"
    "(妈妈/母亲/爸爸/父亲/爱人/奶奶等);若只出现「她/他」,用「母亲/亲人」或保留「她/他」,"
    "绝不能写成「前任/前女友/前男友/对方」这类失恋用词。"
)


def extract_loved_one_turn(client, text: str) -> dict:
    """丧亲版抽取:通用抽取 + 哀伤过程字段,返回完整 turn(供落库)。"""
    turn = base_extract.extract_turn(client, text, object_hint=_OBJECT_HINT)
    turn["cbt"] = extract_grief_cbt(client, text)
    return turn
