"""失恋场景的 CBT 导向抽取:在通用事实/情绪抽取之上,额外抽 CBT 过程字段。

CBT 字段(对齐 LEKIA 2.0 认知层的 Process Evidence):
- automatic_thoughts: 用户话语里隐含的自动思维/核心信念(如「我不值得被爱」)
- cognitive_distortions: 认知扭曲类型(灾难化/全有或全无/个人化等)
- reflective_readiness: 反思就绪度 0-1(能否理性复盘)
- functional_signal: 功能恢复信号 0-1(作息/社交/新生活迹象)
- rumination: 是否仍反刍(零 LLM 词典判,不交 LLM)
"""
from __future__ import annotations

from memory import extract as base_extract

# 认知扭曲类型(标准 CBT 术语)
COGNITIVE_DISTORTIONS = (
    "灾难化", "全有或全无", "过度概括", "心理过滤", "个人化",
    "读心术", "贴标签", "应该陈述", "情绪化推理", "贬低积极",
)

# 反刍信号(零 LLM):还在反复想前任/放不下
_RUMINATION_HINTS = (
    "放不下", "忘不掉", "老是想起", "总是想", "忍不住想", "反复想",
    "回忆", "想起他", "想起她", "走不出来", "难以释怀", "还是会想",
    # 高频反刍表达:忍不住看/想、控制不住、反复翻对方朋友圈
    "忍不住", "控制不住", "翻他朋友圈", "翻她朋友圈",
    # 口语变体:「我老是想…」「我老觉得…」= 反复想/放不下
    "老是想", "老觉得",
)
# 功能恢复信号(零 LLM):作息/社交/工作/新生活
# 注:去掉「工作」——它太宽泛,「他工作越来越忙」这类讲对方的话会被误判成用户的功能恢复。
_FUNCTIONAL_HINTS = (
    "新朋友", "开始运动", "健身", "跑步", "看书", "学习", "旅行", "重新开始",
    "报名", "参加了", "上班", "忙起来", "走出来", "好多了", "想开了",
)

_CBT_SYSTEM = "你是失恋疗愈过程的观察员,只输出 JSON,不要多余文字。"


def extract_cbt(client, text: str) -> dict:
    """抽取 CBT 过程字段。mock / LLM 失败时降级为词典兜底。"""
    if client.mock:
        return lexicon_cbt(text)

    rumination = any(k in (text or "") for k in _RUMINATION_HINTS)

    messages = [
        {"role": "system", "content": _CBT_SYSTEM},
        {"role": "user", "content": (
            "从下面的失恋倾诉中抽取 CBT 过程信息,输出 JSON:\n"
            "{\"automatic_thoughts\": [\"用户话语里隐含的自动思维/核心信念,如'我不值得被爱''都是我不好''我再也找不到更好的人',没有则为空数组\"],\n"
            " \"cognitive_distortions\": [\"认知扭曲类型,只能从:" + "/".join(COGNITIVE_DISTORTIONS) + " 里选,没有则为空数组\"],\n"
            " \"reflective_readiness\": 0到1(能否理性复盘'为什么分手'、能否站旁观者视角,0=纯情绪宣泄/纯自责或指责,1=能客观分析),\n"
            " \"functional_signal\": 0到1(有无恢复功能迹象:作息/社交/工作/新生活/新爱好,0=完全无,1=有明显迹象)}\n\n"
            f"文本:\n{text}"
        )},
    ]
    parsed, _ = client.chat_json(messages, temperature=0.2, model=client.settings.llm_fast_model)
    if not parsed or not isinstance(parsed, dict):
        return lexicon_cbt(text)
    return {**_normalize(parsed), "rumination": rumination}


def _normalize(d: dict) -> dict:
    def _clamp01(x, default=0.0):
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
        "functional_signal": _clamp01(d.get("functional_signal"), 0.0),
    }


# 否定守卫:如「很少想起他」「不太会想他」是否定反刍,不应计为反刍信号。
_SIGNAL_NEG = ("很少", "不太", "不怎么", "不再", "几乎不", "不会", "没怎么", "没", "别", "不", "未")


def _negated(pre: str) -> bool:
    # 「特别」= 程度副词(不是否定);「未来」= 名词(不是「未」否定)。这两种先排除。
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


def lexicon_cbt(text: str) -> dict:
    """零 LLM 词典兜底:命中词表粗判(含反刍,带否定守卫)。供 mock 降级与评估预热复用。"""
    rumination = _hits(text, _RUMINATION_HINTS)
    functional = _hits(text, _FUNCTIONAL_HINTS)
    return {
        "automatic_thoughts": [],
        "cognitive_distortions": [],
        "reflective_readiness": 0.3 if functional else 0.1,
        "functional_signal": 0.6 if functional else 0.0,
        "rumination": rumination,
    }


def extract_breakup_turn(client, text: str) -> dict:
    """失恋版抽取:通用抽取 + CBT 字段,返回完整 turn(供落库)。"""
    turn = base_extract.extract_turn(client, text)
    turn["cbt"] = extract_cbt(client, text)
    return turn
