"""危机检测:关键词硬门槛,命中即升级,不依赖 LLM,不可绕过。

安全设计:
- 危机检测必须零 LLM、零网络、极低延迟,任何时刻可用;因此用关键词硬匹配,绝不依赖模型可用性。
- 硬匹配的代价是误报(把"不想死""不会自杀"等否定表述当成危机)。用「否定词守卫」压低误报:
  匹配到高危词后,若紧邻前文是"不/没/别/莫/未/非"等否定词,且关键词本身不以否定词开头,
  则视为被否定、不计入。
- 覆盖取舍上宁多勿漏:误报的代价只是一句温柔的安全提示,漏报的代价是错过真实自伤风险,
  所以对"撞死""想不开"这类口语化自伤表达,宁可多匹配。
"""
from __future__ import annotations

_CRISIS_HIGH = [
    "自杀", "轻生", "不想活", "想死", "活着没意思", "结束自己", "结束生命",
    "一了百了", "跳楼", "割腕", "自残", "烧炭", "上吊",
    # 口语化自伤表达(来自真实用户文字,如「想找个地方撞死算了」)
    "撞死", "想不开", "熬不下去", "活着好累", "想结束一切", "死了算了", "离开这个世界",
]
_CRISIS_MED = [
    "解脱", "安眠药", "伤害自己", "撑不下去", "活不下去", "没有意义了", "想消失",
    "熬不住了", "不想再醒过来", "想逃避一切",
]

# 否定词:关键词紧邻前文出现这些词时,视为"被否定",不计入(如"不想死""不会自杀")。
_NEGATION = ("不", "没", "别", "莫", "未", "非")

# 关键词自身以否定词开头(如"不想活"),属于真实危机信号,不能被当成"被否定"。
_NEG_PREFIX = ("不", "别", "莫", "未", "无")

# 第三人称主体:高危词紧跟这些词之后(如「妈妈自杀」「他跳楼了」),指的是逝者的离世方式,
# 不是用户自己要自伤。若不区分,会把「我妈妈自杀了,我很自责」误判成用户危机、用热线话术
# 顶掉本该有的哀伤陪伴。只覆盖紧跟(关键词紧邻主体词之后),更远的表述仍按高危处理、宁多勿漏。
_THIRD_PERSON = (
    "妈妈", "爸爸", "母亲", "父亲", "奶奶", "爷爷", "姥姥", "姥爷", "外公", "外婆",
    "妻子", "丈夫", "老婆", "老公", "儿子", "女儿", "弟弟", "妹妹", "哥哥", "姐姐",
    "他", "她", "他们", "她们", "亲人", "家人", "朋友", "同事", "同学", "邻居", "亲戚",
    "孩子", "爱人", "伴侣", "孙子", "孙女", "大爷", "大妈", "叔叔", "阿姨", "舅舅", "姑姑",
)


def _is_negated(text: str, idx: int, kw: str) -> bool:
    if kw[:1] in _NEG_PREFIX:
        return False
    pre = text[max(0, idx - 2):idx]
    return any(n in pre for n in _NEGATION)


def _is_third_person(text: str, idx: int) -> bool:
    """高危词紧跟第三人称主体(如「妈妈自杀」「他跳楼」)→ 指逝者而非用户,不触发危机。"""
    for p in _THIRD_PERSON:
        if text[max(0, idx - len(p)):idx] == p:
            return True
    return False


def _matches(text: str, words: list[str]) -> list[str]:
    hit: list[str] = []
    for w in words:
        i = text.find(w)
        while i != -1:
            if not _is_negated(text, i, w) and not _is_third_person(text, i):
                hit.append(w)
                break
            i = text.find(w, i + 1)
    return hit


def detect(text: str) -> dict:
    high = _matches(text, _CRISIS_HIGH)
    med = _matches(text, _CRISIS_MED)
    if high:
        level = 3
    elif med:
        level = 2
    else:
        level = 0
    return {"level": level, "hit": high or med, "is_crisis": level >= 2}


CRISIS_MESSAGE = (
    "我很在意你现在的感受,谢谢你愿意告诉我。如果你正处在很痛苦的状态里,请先停下来,"
    "联系能真正陪在你身边的人或专业热线:\n"
    "全国 24 小时心理援助热线 400-161-9995,紧急情况请拨打 120 或 110。\n"
    "我在这里陪着你,但专业的帮助更重要。"
)
