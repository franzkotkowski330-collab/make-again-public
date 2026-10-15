"""宠物离世场景的哀伤导向抽取:在通用事实/情绪抽取之上,额外抽宠物哀伤过程字段。

对齐 LEKIA 2.0 认知层「过程证据」(Process Evidence)的宠物丧亲版:
- 字段骨架与亲友离世(丧亲)一致:把「反刍」替换为「渴望思念(yearning)」,
  并新增「死亡现实接受 / 意义重建 / 持续联结质量 / 内疚」。
- 宠物丧亲特有的两点:
  a) 内疚来源是「安乐死决定内疚(太早/太晚送它走)+ 疏忽内疚(没早点发现/没陪它)」,
     区别于亲友离世的「没说再见」内疚;
  b) 新增「自我矮化(self_minimizing)」信号——被剥夺的哀伤(disenfranchised grief)的核心,
     用户说「只是一只猫」「是不是太夸张」时命中,供「给哀伤正名」原则与信号层使用。

字段:
- automatic_thoughts: 隐含的自动思维/核心信念(如「都怪我」「是我结束了它的命」)
- cognitive_distortions: 认知扭曲类型(个人化/灾难化/过度概括等)
- reflective_readiness: 反思就绪度 0-1(能否理性看待它的离开)
- meaning_reconstruction: 意义重建 0-1(能否给它的离开一个说法,如「它教会了我耐心」,Neimeyer)
- death_acceptance: 死亡现实接受 0-1(Worden 任务1)
- continuing_bond: 持续联结质量 0-1(0=冻结/沉溺,1=健康的「它活在心里」,Klass)
- functional_signal: 功能恢复信号 0-1(Track I / DPM 恢复导向)
- yearning / guilt / denial / self_minimizing: 零 LLM 词典判(渴望思念 / 内疚 / 否认死亡 / 自我矮化)
"""
from __future__ import annotations

from memory import extract as base_extract

# 认知扭曲类型(标准 CBT 术语)
COGNITIVE_DISTORTIONS = (
    "灾难化", "全有或全无", "过度概括", "心理过滤", "个人化",
    "读心术", "贴标签", "应该陈述", "情绪化推理", "贬低积极",
)

# 渴望/思念(零 LLM,PGD 核心症状):还想再见到它、反复想起。
_YEARNING_HINTS = (
    "想念", "想它", "想它了", "好想它", "很想它", "想再见", "好想再见",
    "梦见它", "它的照片", "看它的照片", "它的声音", "扑过来", "等它回家",
    "它的小窝", "它的味道", "再也见不到", "见不到它",
)

# 内疚(零 LLM,宠物丧亲特有):安乐死决定 + 疏忽
_GUILT_HINTS = (
    "安乐死", "安乐", "送走它", "送它走", "让它走", "放它走",
    "是不是太早", "是不是太晚", "太早送", "太晚送", "该早点", "早点发现",
    "没发现", "没陪它", "工作忙", "疏忽", "后悔", "对不起它", "白受罪",
    "都怪我", "怪我", "我要是", "要是我", "如果我", "如果那天", "如果当时",
    "我应该", "是我没", "是不是就不会",
)

# 否认死亡(零 LLM,Worden 任务1 未完成):还没接受它已离世
_DENIAL_HINTS = (
    "总觉得它还在", "总觉得它", "还会回来", "等它回来", "听见它",
    "假装", "接受不了", "不愿相信", "不像是真的", "它还活着", "走丢了",
)

# 自我矮化(零 LLM,被剥夺的哀伤核心信号):把「为宠物伤心」不当回事
_SELF_MINIMIZING_HINTS = (
    "只是一只猫", "只是一只狗", "不就是只", "不就一只", "矫情",
    "太夸张", "小题大做", "别人都不理解", "不该这么难过", "至于吗",
)

# 功能恢复信号(零 LLM,Track I):作息/社交/新生活
_FUNCTIONAL_HINTS = (
    "新朋友", "开始运动", "健身", "跑步", "看书", "学习", "旅行", "重新开始",
    "报名", "参加了", "上班", "忙起来", "走出来", "好多了", "想开了", "重新上班",
)

_PET_SYSTEM = "你是宠物丧亲哀伤过程的观察员,只输出 JSON,不要多余文字。"


def extract_pet_cbt(client, text: str) -> dict:
    """抽取宠物丧亲 CBT/哀伤过程字段。mock / LLM 失败时降级为词典兜底。"""
    if client.mock:
        return lexicon_pet(text)

    yearning = _hits(text, _YEARNING_HINTS)
    guilt = _hits(text, _GUILT_HINTS)
    denial = _hits(text, _DENIAL_HINTS)
    self_minimizing = _hits(text, _SELF_MINIMIZING_HINTS)

    messages = [
        {"role": "system", "content": _PET_SYSTEM},
        {"role": "user", "content": (
            "从下面的宠物丧亲倾诉中抽取哀伤过程信息,输出 JSON:\n"
            "{\"automatic_thoughts\": [\"用户话语里隐含的自动思维/核心信念,如'都怪我''是我结束了它的命''我该早点发现',没有则为空数组\"],\n"
            " \"cognitive_distortions\": [\"认知扭曲类型,只能从:" + "/".join(COGNITIVE_DISTORTIONS) + " 里选,没有则为空数组\"],\n"
            " \"reflective_readiness\": 0到1(能否理性看待它的离开、能否站旁观者视角,0=纯情绪宣泄/纯自责,1=能客观分析),\n"
            " \"meaning_reconstruction\": 0到1(能否给它的离开一个说法/意义,如'它走得不痛苦''它教会了我耐心',0=完全想不通为什么,1=已找到意义),\n"
            " \"death_acceptance\": 0到1(是否接受它已离世这一现实,0=否认/总觉得它还在,1=已接受),\n"
            " \"continuing_bond\": 0到1(与它的持续联结质量,0=冻结/沉溺(我永远停在失去它那天),1=健康联结(它活在我心里、我用纪念的方式继续爱它)),\n"
            " \"functional_signal\": 0到1(有无恢复功能迹象:作息/社交/工作/新生活,0=完全无,1=有明显迹象)}\n\n"
            f"文本:\n{text}"
        )},
    ]
    parsed, _ = client.chat_json(messages, temperature=0.2, model=client.settings.llm_fast_model)
    if not parsed or not isinstance(parsed, dict):
        return lexicon_pet(text)
    return {
        **_normalize(parsed),
        "yearning": yearning, "guilt": guilt, "denial": denial,
        "self_minimizing": self_minimizing,
    }


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


# 否定守卫:如「不怪自己了」「不再想它」不应计为相应信号。
# 单字否定有子串碰撞:『特别』的『别』『未来』的『未』都不是否定,先排除。
_SIGNAL_NEG = ("很少", "不太", "不怎么", "不再", "几乎不", "不会", "没怎么", "没", "别", "不", "未")


def _negated(pre: str) -> bool:
    if pre.endswith("特别") or pre.endswith("未来"):
        return False
    # 「是不是/能不能/要不要」这类 A不A 反问不是否定(如「是不是太夸张」仍应命中自我矮化),排除。
    if any(q in pre for q in ("是不是", "能不能", "要不要", "会不会", "该不该")):
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


def lexicon_pet(text: str) -> dict:
    """零 LLM 词典兜底:命中词表粗判(含渴望/内疚/否认/自我矮化,带否定守卫)。供 mock 降级与评估预热复用。"""
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
        "self_minimizing": _hits(text, _SELF_MINIMIZING_HINTS),
    }


# 宠物离世场景的思念对象提示:让通用抽取的 summary/facts 用「宠物」而非失恋的「前任」或丧亲的「亲人」。
_OBJECT_HINT = (
    "这是宠物离世场景,倾诉里的思念对象是一只宠物(猫/狗/其他动物),不是人、不是前任/亲人。"
    "概括 summary 和写 facts 时,把它当作一只宠物来描述(名字、品种、陪伴时长);"
    "绝不能写成「前任/前女友/前男友/对方」这类失恋用词,也不要写成「母亲/父亲」这类亲属词。"
)


def extract_pet_turn(client, text: str) -> dict:
    """宠物离世版抽取:通用抽取 + 宠物哀伤过程字段,返回完整 turn(供落库)。"""
    turn = base_extract.extract_turn(client, text, object_hint=_OBJECT_HINT)
    turn["cbt"] = extract_pet_cbt(client, text)
    return turn
