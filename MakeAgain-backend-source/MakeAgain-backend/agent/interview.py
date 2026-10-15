"""访谈 Agent:AI 提问 → 用户回答 → AI 追问,最终生成用户画像 + 对象画像 + 疗愈计划。

对齐需求 F1-2(初始问卷轮询,含轻量心理测评)、F1-3(初始报告 + 写入 memory + 人机协同重生成)。
设计要点:
- 问题清单由若干"维度"构成,AI 在维度内动态追问,而非机械读题。
- 访谈状态持久化在 interview_sessions.state,可中断恢复。
- 每个回答经一次 LLM 调用同时产出:下一步动作 + 追问问题 + 事实 + 情绪,并写回记忆流。
- 结束时生成反馈报告,把两份画像写入 memory,并确认疗愈目标。
"""
from __future__ import annotations

import copy

from agent import async_report as async_report_mod, onboarding
from agent.companion import _victim_context
from emotion.crisis import CRISIS_MESSAGE, detect
from gateway.client import LLMClient
from memory import store
from memory.breakup import extract as breakup_extract
from memory.loved_one import extract as loved_one_extract
from memory.pet import extract as pet_extract
from memory.extract import extract_tags

MAX_FOLLOWUP_PER_DIM = 2
MAX_TURNS = 20

# 亲友离世 = 亲人 + 挚友(同龄人)离世;"family" 为早期旧值,保留映射以兼容存量数据
LOSS_TYPE_LABELS = {"breakup": "分手", "loved_one": "亲友离世", "family": "亲友离世", "pet": "宠物离世"}

# 访谈维度(问题清单)
DIMENSIONS = [
    {
        "key": "object",
        "title": "聊聊 TA",
        "question": "先跟我聊聊 TA 吧——TA 是你什么人,叫什么(昵称也可以)?",
    },
    {
        "key": "memory",
        "title": "共同的记忆",
        "question": "你们之间,有哪些让你反复想起的时刻或地方?挑一件最清晰的说说。",
    },
    {
        "key": "emotion",
        "title": "现在的情绪",
        "question": "最近哪些时刻、哪些事,最容易让你突然难过或走神?",
    },
    {
        "key": "confusion",
        "title": "放不下的事",
        "question": "现在最让你放不下、或者一直想不通的是什么?",
    },
    {
        "key": "goal",
        "title": "你的期望",
        "question": "如果让你选,你更希望的是「彻底忘记、翻篇」,还是「带着这段记忆,继续往前走」?",
    },
]

# 分手专属访谈维度(问题清单,对齐 LEKIA 2.0 CBT 认知层 + 四维疗愈信号)。
# 顺序即一条情绪疗愈弧线:温暖记忆 → 分手事实 → 当下情绪 → 反刍 → 归因 → 相处模式 → 功能 → 目标。
# 每维产出标注在旁,访谈结束后直接初始化 gather_signals 的四维信号 + 两份画像 + 安全边界。
BREAKUP_DIMENSIONS = [
    {
        "key": "object",
        "title": "聊聊 TA",
        "question": "先跟我聊聊 TA 吧——TA 是你什么人,叫什么(昵称也可以),你们在一起多久了?",
        # 产出:对象画像(称呼/关系/时长)、关系背景
    },
    {
        "key": "memory",
        "title": "最难忘的记忆",
        "question": "你们之间,有没有一个让你一想到就心头一暖的画面、地方或瞬间?挑一个最清晰的说说。",
        # 产出:记忆点(报告题眼的情绪锚)、共同记忆
    },
    {
        "key": "event",
        "title": "走到这一步",
        "question": "这段感情是怎么一步步走到分开的?是谁先提的、大概多久以前?",
        # 产出:分手时间线 → 急性期判断(分手多久)、事件经过
    },
    {
        "key": "emotion",
        "title": "现在的你",
        "question": "这几天,你的心情大概是什么样的?哪些时刻最难受?",
        # 产出:情绪基线(baseline)、情绪波动点
    },
    {
        "key": "rumination",
        "title": "反复想起",
        "question": "有哪些念头、画面或想不通的问题,会反复在你脑子里打转、放不下?",
        # 产出:反刍强度(rumination)、困惑点
    },
    {
        "key": "attribution",
        "title": "你怎么想这件事",
        "question": "分开之后,你心里是怎么跟自己解释这件事的?会不会觉得有自己的责任?",
        # 产出:自动思维 + 认知扭曲 + 反思就绪度(reflective_readiness)、受害者识别
    },
    {
        "key": "attachment",
        "title": "相处里的模式",
        "question": "回想你们相处的时候,闹矛盾、有分歧通常是怎么处理的?有没有一种反复出现的老样子?",
        # 产出:依恋线索(attachment)、相处模式、反思就绪度
    },
    {
        "key": "functional",
        "title": "现在的日常",
        "question": "最近你的作息、吃饭、工作学习、还有跟朋友来往,大概是什么状态?有没有在为自己做点什么?",
        # 产出:功能恢复信号(functional_signal)
    },
    {
        "key": "goal",
        "title": "你想要的",
        "question": "如果让你选,你更希望的是「彻底忘记、翻篇」,还是「带着这段记忆,继续往前走」?",
        # 产出:疗愈目标(goal)
    },
]


# 亲友离世专属访谈维度(问题清单,对齐哀伤双过程模型 DPM + 六维疗愈信号)。
# 顺序即一条哀伤疗愈弧线:温暖记忆 → 去世经过 → 当下情绪/思念 → 内疚未竟之事 → 意义重建 → 持续联结/纪念 → 功能 → 目标。
# 每维产出标注在旁,访谈结束后直接初始化 gather_signals 的六维信号 + 两份画像。
LOVED_ONE_DIMENSIONS = [
    {
        "key": "object",
        "title": "聊聊 TA",
        "question": "先跟我聊聊 TA 吧——TA 是你什么人,叫什么(昵称也可以)?",
        # 产出:对象画像(称呼/关系)、关系背景
    },
    {
        "key": "memory",
        "title": "最难忘的记忆",
        "question": "TA 身上有没有一个画面、一句话或一个地方,让你一想到心里就软一下?挑一个最清晰的说说。",
        # 产出:记忆点(报告题眼的情绪锚)、持续联结素材
    },
    {
        "key": "event",
        "title": "TA 是怎么走的",
        "question": "能跟我说说 TA 是什么时候、怎么离开的吗?大概多久以前?",
        # 产出:去世时间线 → 急性期判断、死亡接受起点
    },
    {
        "key": "emotion",
        "title": "现在的你",
        "question": "这几天,你心里大概是什么滋味?哪些时刻最想念、最难受?",
        # 产出:情绪基线(baseline)、渴望思念(yearning)
    },
    {
        "key": "guilt",
        "title": "放不下的自责",
        "question": "有没有什么事,让你一直觉得「要是当时……就好了」,或者总觉得有自己的责任?",
        # 产出:内疚与未竟之事(guilt)、自动思维 + 认知扭曲
    },
    {
        "key": "meaning",
        "title": "你怎么想这件事",
        "question": "TA 的离开,对你意味着什么?TA 有没有教会你、或者留给你什么?",
        # 产出:意义重建(meaning_reconstruction)、死亡接受(death_acceptance)
    },
    {
        "key": "bond",
        "title": "纪念 TA 的方式",
        "question": "你现在会用什么方式想 TA、纪念 TA?有没有想一直为 TA 留着的东西或习惯?",
        # 产出:持续联结质量(continuing_bond)、纪念方式
    },
    {
        "key": "functional",
        "title": "现在的日常",
        "question": "最近你的作息、吃饭、工作学习、还有跟家人朋友来往,大概是什么状态?有没有在为自己做点什么?",
        # 产出:功能恢复信号(functional_signal)
    },
    {
        "key": "goal",
        "title": "你想要的",
        "question": "如果让你选,你更希望的是「把 TA 彻底放下」,还是「带着 TA 的记忆,继续过好自己的生活」?",
        # 产出:疗愈目标(goal)
    },
]


# 宠物离世专属访谈维度(问题清单,对齐哀伤双过程模型 DPM + 六维疗愈信号)。
# 顺序即一条哀伤疗愈弧线:温暖记忆 → 离世经过(含安乐决定) → 当下情绪/思念 → 内疚(安乐+疏忽) → 意义重建 → 持续联结/纪念 → 功能 → 目标。
# 每维产出标注在旁,访谈结束后直接初始化 gather_signals 的六维信号 + 两份画像。
PET_DIMENSIONS = [
    {
        "key": "object",
        "title": "聊聊它",
        "question": "先跟我聊聊它吧——它叫什么名字、是什么(猫/狗/别的)、陪你多久了?",
        # 产出:对象画像(名字/品种/陪伴时长)、关系背景
    },
    {
        "key": "memory",
        "title": "最难忘的记忆",
        "question": "它身上有没有一个画面、一个动作或一个地方,让你一想到心里就软一下?挑一个最清晰的说说。",
        # 产出:记忆点(报告题眼的情绪锚)、持续联结素材
    },
    {
        "key": "event",
        "title": "它是怎么走的",
        "question": "能跟我说说它是什么时候、怎么离开的吗?如果做了安乐,也愿意的话可以说说当时的决定。",
        # 产出:离世时间线 → 急性期判断、死亡接受起点、安乐决定背景
    },
    {
        "key": "emotion",
        "title": "现在的你",
        "question": "这几天,你心里大概是什么滋味?哪些时刻最想它、最难受?",
        # 产出:情绪基线(baseline)、渴望思念(yearning)
    },
    {
        "key": "guilt",
        "title": "放不下的自责",
        "question": "有没有什么事,让你一直觉得「要是当时……就好了」?比如没早点发现它不舒服、那阵子没多陪它;如果当时做了安乐的决定,也愿意的话可以说说你的纠结。",
        # 产出:安乐决定内疚 + 疏忽内疚(guilt)、自动思维 + 认知扭曲
    },
    {
        "key": "meaning",
        "title": "你怎么想这件事",
        "question": "它的离开,对你意味着什么?它有没有教会你、或者留给你什么?",
        # 产出:意义重建(meaning_reconstruction)、死亡接受(death_acceptance)
    },
    {
        "key": "bond",
        "title": "纪念它的方式",
        "question": "你现在会用什么方式想它、纪念它?有没有想一直为它留着的东西(项圈/玩具/照片)或习惯?",
        # 产出:持续联结质量(continuing_bond)、纪念方式
    },
    {
        "key": "functional",
        "title": "现在的日常",
        "question": "最近你的作息、吃饭、工作学习、还有跟家人朋友来往,大概是什么状态?有没有在为自己做点什么?",
        # 产出:功能恢复信号(functional_signal)
    },
    {
        "key": "goal",
        "title": "你想要的",
        "question": "如果让你选,你更希望的是「把它彻底放下」,还是「带着它的记忆,继续过好自己的生活」?",
        # 产出:疗愈目标(goal)
    },
]


def _dims_for(loss_type: str | None) -> list[dict]:
    """按 loss_type 决定访谈维度:分手用分手九维、亲友离世用丧亲九维、宠物离世用宠物九维、其余用通用五维。"""
    if loss_type == "breakup":
        return list(BREAKUP_DIMENSIONS)
    if loss_type == "loved_one":
        return list(LOVED_ONE_DIMENSIONS)
    if loss_type == "pet":
        return list(PET_DIMENSIONS)
    return list(DIMENSIONS)


STEP_SYSTEM = """你是「重逢」的访谈引导员,正在和一位经历过失去(分手/亲友离世/宠物离世)的人进行初次深度访谈。

你的目标:在温暖、不评判的对话中,慢慢了解 TA 与那个"放不下的人/宠物",梳理事情的原因、情绪波动和困惑点,最终能构成两份画像(用户画像、思念对象画像)并确认疗愈目标。

【铁律:对话要像真人在认真听,不机械、不硬蹭】
- 不复述、不重复用户刚说过的话;要回接就回接 TA 话里的具体画面、事件或情绪,并加一层共情/观察/点破。禁止用「你说…」「你刚才提到…」这类开头复读。
- 不点评称呼、名字、用词等表面东西;回接不上实质内容就直接问。
- 追问钩住具体细节或潜台词;切换话题先承接实质内容再过渡,不空降无关问题。
- 一次只问一个问题,question 必须是真正的问句,不是评论或陈述。

请根据用户最新一次的回答,判断下一步,只输出 JSON:
{
  "action": "followup" | "next" | "complete",
  "question": "你要说的下一句话(追问或下一个问题;action=complete 时为空字符串)",
  "next_dimension": "action=next 时要进入的下一个维度 key(必须是还没被覆盖的维度)",
  "covered_dimensions": ["本次回答实质聊到的维度 key 列表,可为空数组"],
  "facts": [{"kind": "user|object|memory|emotion_trigger|goal|confusion", "fact": "从用户的话里提炼出的一条稳定事实"}],
  "emotion": {"emotion": "难过|愤怒|内疚|回避|释怀|平静|焦虑|孤独|不甘|想念|恐惧|其他", "valence": -1到1(负=消极/正=积极), "arousal": 0到1(情绪强度), "score": 0到100(越高=心情越好)},
  "goal_signal": true 或 false,
  "note": "一句你的判断依据"
}

判断规则:
1. 用户刚说的内容里,若还有值得深挖的情绪、原因、细节或具体场景,action="followup",追问一句。追问要自然、具体,承接的是「你从 TA 话里听出的潜台词、感受或一个具体观察」,而不是把 TA 的话换种说法复述一遍——严禁用「你说……」「你刚才提到……」「我听你说……」这类开头复述原话。宁可少说,也要说出点 TA 没明说、但你能感受到的东西。
2. 只有当当前维度确实聊充分了、信息足以写进画像时,才 action="next",进入下一个还没聊过的维度;next_dimension 填那个维度的 key。宁可多追问一句,也不要急着进入下一维度——画像信息完整比速度重要。
3. 只有当关键信息足以构成画像时才 action="complete"。宠物离世需覆盖:对象(名字/品种)、共同记忆、离世经过(含安乐决定)、当前情绪/思念、内疚(安乐决定+疏忽)、意义重建、持续联结/纪念、日常功能、目标。亲友离世需覆盖:对象、共同记忆、去世经过、当前情绪/思念、内疚与未竟之事、意义重建、持续联结/纪念、日常功能、目标。分手需覆盖:对象、共同记忆、分手经过、当前情绪、反刍/放不下、归因、相处模式、日常功能、目标。通用需覆盖:对象是谁、共同记忆、情绪波动、困惑点、目标。
4. 一次只问一个问题,语气温柔,不评判,不催促"快好起来"。
5. goal_signal 表示用户是否已明确表达了自己的疗愈目标倾向(例如"不想忘记TA""想带着记忆继续往前走""想彻底翻篇""想重新开始生活")。只有用户清楚说出"想要怎样"的方向时才设为 true;自我归咎、自我怀疑(如"是不是我太黏人""是不是我的错")只是困惑,不是目标倾向,不要设为 true。
6. 当失去类型为"分手"时,追问方向除情绪外,也要引导用户说出"分手经过、双方各自的行为与责任、相处模式"这类归因素材,以及"反复想起什么/放不下什么"(反刍)、"作息/社交/工作等日常状态"(功能恢复)。若用户是被伤害一方(被出轨/家暴/冷暴力/PUA/被抛弃),绝不追问"你哪里做错了/是不是你的责任",转而为 TA 的不易共情,并帮 TA 识别这段关系里不健康的部分。
7. 覆盖与跳转:用户可能在一次回答里就顺带聊到了后面几个维度的内容。但只有当用户已把某个维度聊得比较充分、信息足以写进画像时,才把它填进 covered_dimensions(这些维度之后不再重复问);只是蜻蜓点水提了一句、信息还很单薄的维度,不要标记为覆盖,之后仍要正式问一次,否则画像会缺信息。宁可多问一点,不要因为一句话就跳过整个维度。
8. 切换维度时,question 要承接用户上一轮说的具体细节,自然过渡到新话题;不要空降问卷原题,也不要机械地「说到X,再问Y」。
9. 边界处理:用户表示"暂时忘了/记不清/不想说/不方便回答"时,尊重 TA,不要反复追问这一维度,action="next" 进入下一维度,并把该维度放进 covered_dimensions(之后不再回头问);用户答非所问(说了与当前问题无关的内容)时,不要丢掉 TA 的话,温和地把话题拉回当前问题(action="followup"),同时把 TA 话里真实涉及到的维度记进 covered_dimensions 或 facts,不要浪费信息。
10. 急性期门控:用户最新回答若明显处于崩溃/急性痛苦中(哭、崩溃、痛得受不了、刚分手不久等),先以共情承接,多在「当前情绪 / 反刍」这类维度里陪 TA 消化,不要急着把 TA 推进「归因 / 相处模式」这类需要理性复盘的维度;等 TA 情绪缓一点再自然过渡。
11. 当失去类型为"亲友离世"时,追问方向除情绪外,也要引导用户说出"去世经过(多久前/怎么走的)"、"内疚与未竟之事(自责/反事实/没来得及说的话)"、"意义重建(TA教会了你什么/你如何理解这件事)"、"持续联结(你想怎么纪念TA、为TA留着什么)"、"作息/社交/工作等日常状态(功能恢复)"。内疚场景温柔去自责("这不是你的错/你已尽力"),但不强行让用户放下;死亡不可逆,不迎合"TA会回来"的否认幻想,温柔陪伴接受现实,但不催促"向前看/快好起来"。
12. 当失去类型为"宠物离世"时,追问方向除情绪外,也要引导用户说出"离世经过(多久前/怎么走的,若做安乐也请温柔引导说出当时的决定)"、"内疚"、"意义重建(它教会了你什么/你如何理解这件事)"、"持续联结(你想怎么纪念它、为它留着项圈/玩具/照片)"、"作息/社交/工作等日常状态(功能恢复)"。内疚追问必须跟着死亡原因走、不得预设:仅当用户提到"安乐/送它走/让它走"时才追问安乐决定的内疚(太早/太晚送它走);若宠物是自然离世/病故/意外/走失,则不主动追问安乐决定,只问疏忽与遗憾(没早点发现/没陪它)。全程「给哀伤正名」:为宠物伤心是真实正当的,不用"只是宠物"的口吻、不矮化;内疚场景温柔去自责("这不是你的错/你已尽力"),不替用户判断安乐决定对错;死亡不可逆,不迎合"它会回来"的否认幻想(可接纳"彩虹桥/回汪星/回喵星"作为意义框架),不催促"向前看/快好起来",也不催"再养一只"。
13. question 的写法:承接 = 加一层(共情 / 观察 / 点破),不是复述;问题 = 钩住 TA 刚说的具体细节,不是问卷原题;一次只问一个问题;不点评称呼、名字、用词等表面东西。
14. 回调记忆:TA 前面说过相关的事,可以在当下这轮自然接上,让对方感到被记住;但不要为了回调而回调、不要复述原话,只有确实相关时才用。
注意:直接输出 JSON 对象本身,不要输出思考过程或 JSON 之外的文字。"""


BRIDGE_SYSTEM = """你是「重逢」的访谈引导员,现在要从当前话题自然过渡到下一个话题。请写一句给用户的话:
1. 有具体画面、事件或情绪值得回接时才回接,且只回接实质内容,不点评称呼、名字、用词等表面东西。
2. 回接不上实质内容就直接问出新问题,不要硬造过渡。
3. 不复述、不重复用户的上一句话,禁止「你说…」「你刚才提到…」这类开头。
4. 必须含一个真正的问题,一次只问一个,语气温柔、口语化、不评判。
只输出这一句话本身,不要引号、不要解释、不要多余文字。"""


REPORT_SYSTEM = """你是「重逢」的访谈分析师。基于下面的访谈对话,生成一份温柔的反馈报告,并构建两份画像和疗愈计划。只输出 JSON:
{
  "title": "一句有记忆点的标题,凝练这个人和这段失去的核心,温柔有力量,不超过20字,不要复述摘要",
  "keywords": ["3-5个短词,提炼这个人的核心特质,如'细腻''念旧''有韧性'"],
  "summary": "反馈报告正文(150-300字),写给用户,温柔有共情,总结你听到的故事与情绪脉络,并点出一个具体的记忆画面(记忆点)",
  "quote": "一句可直接当作金句的话,温柔、凝练、有记忆点,是整份报告的题眼,不超过30字",
  "user_portrait": {"失去类型":"...","关系与背景":"...","当前情绪状态":"...","情绪波动点":"...","困惑点":"...","未说出口的话":"..."},
  "object_portrait": {"称呼":"...","关系":"...","性格":"...","共同记忆":"...","重要地点与时间":"..."},
  "goal": {"type": "forget|carry_on|uncertain", "label": "忘记 / 带着记忆走下去 / 还没想清楚", "reason": "..."},
  "heal_plan": {"summary":"一句话目标","stages":[{"title":"...","desc":"...","time":"..."}]}
}
要求:忠实于对话内容,不编造;语气温柔;如果某条信息不足,字段写"暂未提及"。
注意:直接输出 JSON 对象本身,不要输出思考过程、解释或 JSON 之外的任何文字。"""


REPORT_SYSTEM_BREAKUP = """你是「重逢」的访谈分析师。基于下面的访谈对话,为一位经历过"分手"的用户生成一份温柔的反馈报告,并构建两份画像、疗愈计划与一段关系复盘。只输出 JSON:
{
  "title": "一句有记忆点的标题,凝练这个人和这段关系,温柔有力量,不超过20字,不要复述摘要",
  "keywords": ["3-5个短词,提炼这个人的核心特质或这段关系的关键,如'付出型''委屈''慢慢释怀'"],
  "summary": "反馈报告正文(150-300字),写给用户,温柔有共情,总结你听到的故事与情绪脉络,并点出一个具体的记忆画面(记忆点)",
  "quote": "一句可直接当作金句的话,温柔、凝练、有记忆点,是整份报告的题眼,不超过30字",
  "user_portrait": {
    "失去类型":"...", "关系与背景":"...", "当前情绪状态":"...", "情绪波动点":"...", "困惑点":"...", "未说出口的话":"...",
    "依恋类型":"安全型|焦虑型|回避型|恐惧型(矛盾型)|暂未提及",
    "关系中的模式":"用户在关系里反复出现的相处模式",
    "自身盲区":"用户自己可改变、对事不对人的部分",
    "可成长方向":"下一段关系里能做得更好的方向",
    "关系症结":"一句话概括这段关系走到分手的主因"
  },
  "object_portrait": {
    "称呼":"...", "关系":"...", "性格":"...", "共同记忆":"...", "重要地点与时间":"...",
    "TA的行为模式":"TA在关系里反复出现的行为方式",
    "TA的依恋倾向":"安全型|焦虑型|回避型|恐惧型(矛盾型)|暂未提及",
    "TA在关系中的问题":"TA在关系里存在的问题"
  },
  "goal": {"type": "forget|carry_on|uncertain", "label": "忘记 / 带着记忆走下去 / 还没想清楚", "reason": "..."},
  "heal_plan": {"summary":"一句话目标","stages":[{"title":"...","desc":"...","time":"..."}]},
  "relationship_analysis": {
    "attachment": {"user":"用户的依恋类型","basis":"从对话里找出的依据","other":"TA的依恋类型","other_basis":"TA的依据"},
    "causes": [{"factor":"一个具体原因","side":"自己|对方|双方|外部","explain":"一句话解释"}],
    "patterns": "一句话概括两人的互动模式",
    "blind_spots": ["用户可改变、对事不对人的盲区"],
    "suggestions": ["判断这段关系 / 自我成长的可操作建议"],
    "narrative": "一段100-180字、直接写给用户的关系复盘,用分析口吻:聚焦相处模式差异、分手归因、你能成长的方向;不要复述故事、不要再次描写共同记忆、不要以'TA的名字'开场回顾关系背景"
  }
}
要求:
1. 忠实于对话内容,不编造;信息不足的字段写"暂未提及"。
2. 分手复盘必须平衡归因:原因覆盖"自己可改变的部分/对方的部分/双方/外部因素",不单方归咎用户。
3. 若用户在关系中被伤害(被出轨/家暴/冷暴力/PUA/被抛弃),不要归咎用户,转为"这不是你的错 + 识别不健康关系 + 自我重建"。
4. 依恋类型是描述性框架(如"回避型依恋倾向"),不下心理疾病诊断;不评判用户情绪。
5. narrative 是直接写给用户的关系复盘正文(150-300字):语气温暖、连贯、像朋友轻轻帮TA回看这段关系,把依恋倾向、分手原因、互动模式、建议自然揉成一段话;不要分条罗列,不要出现"自己/对方/双方"这类标签式表述;平衡归因、不指责,受害者场景不归咎。
6. summary 与 narrative 职责不同,且都会被展示:summary 是情绪正文,侧重"看见你的情绪 + 点出一个具体的记忆画面";narrative 是关系复盘,侧重"相处模式差异 + 分手归因 + 成长方向"。narrative 必须用分析口吻,绝不能复述 summary 已写过的故事、记忆画面或情绪细节,也不要再用称呼开场回顾关系背景。
注意:直接输出 JSON 对象本身,不要输出思考过程、解释或 JSON 之外的任何文字。"""


REPORT_SYSTEM_LOVED_ONE = """你是「重逢」的访谈分析师。基于下面的访谈对话,为一位经历过"亲友离世"的用户生成一份温柔的反馈报告,并构建两份画像、疗愈计划与一段哀伤叙事。只输出 JSON:
{
  "title": "一句有记忆点的标题,凝练这个人和这段思念,温柔有力量,不超过20字,不要复述摘要",
  "keywords": ["3-5个短词,提炼这个人的核心特质或这段思念的关键,如'深情''念旧''慢慢往前走'"],
  "summary": "反馈报告正文(150-300字),写给用户,温柔有共情,总结你听到的故事与情绪脉络,并点出一个具体的记忆画面(记忆点)",
  "quote": "一句可直接当作金句的话,温柔、凝练、有记忆点,是整份报告的题眼,不超过30字",
  "user_portrait": {
    "失去类型":"...", "关系与背景":"...", "当前情绪状态":"...", "情绪波动点":"...", "困惑点":"...", "未说出口的话":"...",
    "内疚与未竟之事":"用户仍放不下的自责或没来得及做的事",
    "死亡接受状态":"否认|接受中|已接受|暂未提及",
    "意义重建":"TA的离开给用户的意义,或用户正在寻找的意义",
    "现在的日常状态":"作息/社交/工作等状态"
  },
  "object_portrait": {
    "称呼":"...", "关系":"...", "性格":"...", "共同记忆":"...", "重要地点与时间":"...",
    "TA留给你的":"TA留给用户的品质、教诲、纪念物或影响",
    "你心里的TA":"用户心里持续记着、联结着的TA的样子"
  },
  "goal": {"type": "forget|carry_on|uncertain", "label": "忘记 / 带着记忆走下去 / 还没想清楚", "reason": "..."},
  "heal_plan": {"summary":"一句话目标","stages":[{"title":"...","desc":"...","time":"..."}]},
  "grief_analysis": {
    "meaning": "一句话概括TA的离开给用户的意义(没有则'暂未提及')",
    "guilt": {"still_guilty":"用户仍在自责的具体点","reframe":"温柔的去自责视角:这不是你的错/你已尽力,不强行让用户放下"},
    "continuing_bond": {"current":"用户现在如何想TA/纪念TA","suggestions":["可持续的纪念方式建议,如种TA喜欢的花、保留一件纪念物、周年日、做TA喜欢的事"]},
    "death_acceptance": "用户对死亡现实的接受程度(温柔而诚实,不迎合'TA会回来'的幻想)",
    "narrative": "一段120-180字、直接写给用户的哀伤叙事:把意义重建、内疚处理、持续联结温柔揉成一段话,像朋友轻轻陪着TA;不要复述故事、不要再次描写共同记忆、不要以'TA的称呼'开场回顾关系背景"
  }
}
要求:
1. 忠实于对话内容,不编造;信息不足的字段写"暂未提及"。
2. 哀伤疗愈的目标不是"忘记/放下",而是"接受失去 + 带着与TA健康的持续联结继续生活"(中文"事死如事生")。全程不催促"向前看/快点好起来",不把"还想TA"当病态,支持用户用自己的方式纪念。
3. 内疚处理:温柔指出"这不是你的错/你已尽力",但不强行让用户放下内疚;未说出口的话可以轻轻陪TA想象"TA会想对你说什么"。
4. 死亡是不可逆的:不暗示、不迎合"TA会回来/还会见面"的否认幻想,温柔陪伴接受现实,但不催促。
5. narrative 是直接写给用户的哀伤叙事正文:语气温暖、连贯、像朋友轻轻陪着TA回看这段思念,把意义、内疚的放下、持续联结、可继续生活的方向自然揉成一段话;不要分条罗列,不要出现"意义重建/持续联结/内疚"这类标签式表述;不归咎、不催"放下"。
6. summary 与 narrative 职责不同,且都会被展示:summary 是情绪正文,侧重"看见你的情绪 + 点出一个具体的记忆画面";narrative 是哀伤叙事,侧重"意义 + 内疚的温柔处理 + 持续联结 + 继续生活的方向"。narrative 绝不能复述 summary 已写过的故事、记忆画面或情绪细节,也不要再用称呼开场回顾关系背景。
注意:直接输出 JSON 对象本身,不要输出思考过程、解释或 JSON 之外的任何文字。"""


REPORT_SYSTEM_PET = """你是「重逢」的访谈分析师。基于下面的访谈对话,为一位经历过"宠物离世"的用户生成一份温柔的反馈报告,并构建两份画像、疗愈计划与一段哀伤叙事。只输出 JSON:
{
  "title": "一句有记忆点的标题,凝练这个人和这段思念,温柔有力量,不超过20字,不要复述摘要",
  "keywords": ["3-5个短词,提炼这个人的核心特质或这段思念的关键,如'念旧''温柔''慢慢往前走'"],
  "summary": "反馈报告正文(150-300字),写给用户,温柔有共情,总结你听到的故事与情绪脉络,并点出一个具体的记忆画面(记忆点)",
  "quote": "一句可直接当作金句的话,温柔、凝练、有记忆点,是整份报告的题眼,不超过30字",
  "user_portrait": {
    "失去类型":"...", "关系与背景":"...", "当前情绪状态":"...", "情绪波动点":"...", "困惑点":"...", "未说出口的话":"...",
    "内疚与未竟之事":"用户仍放不下的自责(安乐决定或疏忽),或没来得及做的事",
    "死亡接受状态":"否认|接受中|已接受|暂未提及",
    "意义重建":"它的离开给用户的意义,或用户正在寻找的意义",
    "现在的日常状态":"作息/社交/工作等状态",
    "哀伤正名":"用户是否在自我矮化('只是宠物''是不是太夸张'),以及你需要怎么为 TA 的哀伤正名"
  },
  "object_portrait": {
    "称呼":"...", "关系":"猫/狗/品种/陪伴时长", "性格":"...", "共同记忆":"...", "重要地点与时间":"...",
    "它留给你的":"它留给用户的品质、陪伴、影响或纪念物",
    "你心里的它":"用户心里持续记着、联结着的它的样子"
  },
  "goal": {"type": "forget|carry_on|uncertain", "label": "忘记 / 带着记忆走下去 / 还没想清楚", "reason": "..."},
  "heal_plan": {"summary":"一句话目标","stages":[{"title":"...","desc":"...","time":"..."}]},
  "pet_grief_analysis": {
    "meaning": "一句话概括它的离开给用户的意义(没有则'暂未提及')",
    "guilt": {"still_guilty":"用户仍在自责的具体点(安乐决定或疏忽)","reframe":"温柔的去自责视角:这不是你的错/你已尽力,不替用户判断安乐决定对错"},
    "continuing_bond": {"current":"用户现在如何想它/纪念它","suggestions":["可持续的纪念方式建议,如保留项圈、种它喜欢的花、周年日、写点东西给它"]},
    "death_acceptance": "用户对它已离世这一现实的接受程度(温柔而诚实,不迎合'它还会回来'的幻想,可接纳'彩虹桥/回汪星'作为意义框架)",
    "disenfranchised": "用户是否把哀伤矮化/藏起,以及你需要如何为其正名",
    "narrative": "一段120-180字、直接写给用户的哀伤叙事:把意义重建、内疚处理、持续联结温柔揉成一段话,像朋友轻轻陪着TA;不要复述故事、不要再次描写共同记忆、不要以'它的名字'开场回顾关系背景"
  }
}
要求:
1. 忠实于对话内容,不编造;信息不足的字段写"暂未提及"。
2. 哀伤疗愈的目标不是"忘记/放下",而是"接受失去 + 带着与它健康的持续联结继续生活"。全程不催促"向前看/快点好起来",不把"还想它"当病态,不催"再养一只",支持用户用自己的方式纪念。
3. 给哀伤正名:为宠物伤心是真实正当的,它从来不是"只是一只猫/狗";用户自我矮化时温柔正名,不矮化这份感情。
4. 内疚处理:温柔指出"这不是你的错/你已尽力",但不强行让用户放下内疚,也不替用户判断安乐决定对错。
5. 死亡是不可逆的:不暗示、不迎合"它还会回来"的否认幻想,温柔陪伴接受现实,但不催促;可接纳"彩虹桥/回汪星/回喵星"作为意义框架。
6. narrative 是直接写给用户的哀伤叙事正文:语气温暖、连贯、像朋友轻轻陪着TA回看这段思念,把意义、内疚的放下、持续联结、可继续生活的方向自然揉成一段话;不要分条罗列,不要出现"意义重建/持续联结/内疚"这类标签式表述;不归咎、不催"放下"、不催"再养一只"。
7. summary 与 narrative 职责不同,且都会被展示:summary 是情绪正文,侧重"看见你的情绪 + 点出一个具体的记忆画面";narrative 是哀伤叙事,侧重"意义 + 内疚的温柔处理 + 持续联结 + 继续生活的方向"。narrative 绝不能复述 summary 已写过的故事、记忆画面或情绪细节,也不要再用称呼开场回顾关系背景。
注意:直接输出 JSON 对象本身,不要输出思考过程、解释或 JSON 之外的任何文字。"""


def _report_system(loss_type: str | None) -> str:
    if loss_type == "breakup":
        return REPORT_SYSTEM_BREAKUP
    if loss_type == "loved_one":
        return REPORT_SYSTEM_LOVED_ONE
    if loss_type == "pet":
        return REPORT_SYSTEM_PET
    return REPORT_SYSTEM


def _fmt_relationship_analysis(ra: dict) -> str:
    """把关系复盘压成一段可召回/可展示的文本:优先用 LLM 写好的温暖 narrative,兜底用模板串成连贯段落(不做分列罗列)。"""
    narrative = (ra.get("narrative") or "").strip()
    if narrative:
        return narrative

    parts: list[str] = []
    att = ra.get("attachment") or {}
    user_att, other_att = att.get("user"), att.get("other")
    if user_att and user_att != "暂未提及" and other_att and other_att != "暂未提及":
        parts.append(f"你更偏「{user_att}」的相处方式,TA 更偏「{other_att}」")
    elif user_att and user_att != "暂未提及":
        parts.append(f"你的依恋倾向更偏「{user_att}」")

    cause_txt = "、".join(
        f"{c.get('factor')}" + (f"({c.get('explain')})" if c.get("explain") else "")
        for c in (ra.get("causes") or [])
        if isinstance(c, dict) and c.get("factor") and c.get("factor") != "暂未提及"
    )
    if cause_txt:
        parts.append(f"走到分手,更多在于 {cause_txt}")

    if ra.get("patterns") and ra["patterns"] != "暂未提及":
        parts.append(f"你们之间是「{ra['patterns']}」的相处模式")

    sugg = [str(s) for s in (ra.get("suggestions") or []) if s and str(s) != "暂未提及"]
    if sugg:
        parts.append("往后可以试着" + "、".join(sugg))

    if not parts:
        return ""
    return "。".join(parts) + "。"


def _fmt_grief_analysis(ga: dict) -> str:
    """把哀伤叙事压成一段可召回/可展示的文本:优先用 LLM 写好的温暖 narrative,兜底用模板串成连贯段落。"""
    narrative = (ga.get("narrative") or "").strip()
    if narrative:
        return narrative

    parts: list[str] = []
    meaning = (ga.get("meaning") or "").strip()
    if meaning and meaning != "暂未提及":
        parts.append(f"TA 的离开让你慢慢懂得了 {meaning}")

    guilt = ga.get("guilt") or {}
    still = (guilt.get("still_guilty") or "").strip()
    reframe = (guilt.get("reframe") or "").strip()
    if still and reframe:
        parts.append(f"你一直放不下 {still},但 {reframe}")

    cb = ga.get("continuing_bond") or {}
    sugg = [str(s) for s in (cb.get("suggestions") or []) if s and str(s) != "暂未提及"]
    if sugg:
        parts.append("往后可以" + "、".join(sugg) + "来纪念 TA")

    if not parts:
        return ""
    return "。".join(parts) + "。"


def _fmt_pet_analysis(pa: dict) -> str:
    """把宠物哀伤叙事压成一段可召回/可展示的文本:优先用 LLM 写好的温暖 narrative,兜底用模板串成连贯段落。"""
    narrative = (pa.get("narrative") or "").strip()
    if narrative:
        return narrative

    parts: list[str] = []
    meaning = (pa.get("meaning") or "").strip()
    if meaning and meaning != "暂未提及":
        parts.append(f"它走后你慢慢懂得了 {meaning}")

    guilt = pa.get("guilt") or {}
    still = (guilt.get("still_guilty") or "").strip()
    reframe = (guilt.get("reframe") or "").strip()
    if still and reframe:
        parts.append(f"你一直放不下 {still},但 {reframe}")

    cb = pa.get("continuing_bond") or {}
    sugg = [str(s) for s in (cb.get("suggestions") or []) if s and str(s) != "暂未提及"]
    if sugg:
        parts.append("往后可以" + "、".join(sugg) + "来纪念它")

    if not parts:
        return ""
    return "。".join(parts) + "。"


def questions() -> list[dict]:
    return [{"key": d["key"], "title": d["title"], "question": d["question"]} for d in DIMENSIONS]


def start(db, user_id: str, loss_type: str, question_key: str | None = None) -> dict:
    store.get_or_create_user(db, user_id, loss_type=loss_type)
    # 首次进入才推进到「访谈中」;已进入过(中断恢复/已完成)不再回退阶段。
    if onboarding.get_phase(db, user_id) == "new":
        onboarding.set_phase(db, user_id, "interview")

    dims = _dims_for(loss_type)
    dim_idx = 0
    if question_key:
        for i, d in enumerate(dims):
            if d["key"] == question_key:
                dim_idx = i
                break

    first_q = dims[dim_idx]["question"]
    state = {
        "dimension_idx": dim_idx,
        "followup_count": 0,
        "asked": [first_q],
        "covered": [],
        "facts": [],
        "history": [{"role": "assistant", "content": first_q}],
        "done": False,
        "report": None,
    }
    session = store.create_interview(db, user_id, loss_type, state)
    return {"session_id": session.id, "question": first_q, "loss_type": loss_type}


def answer(db, session_id: str, user_answer: str, client: LLMClient, async_report: bool = False) -> dict:
    session = store.get_interview(db, session_id)
    if session is None:
        raise ValueError("session not found")
    state = copy.deepcopy(session.state or {})
    if state.get("done"):
        return {"action": "done", "report": state.get("report")}

    state.setdefault("history", []).append({"role": "user", "content": user_answer})

    # 危机门控(硬门槛,不可绕过):访谈中流露出自伤倾向时,立即暂停访谈、给危机支持,不追问、不推进。
    crisis = detect(user_answer)
    if crisis["is_crisis"]:
        state["history"].append({"role": "assistant", "content": CRISIS_MESSAGE})
        store.add_memory(
            db, session.user_id, type="interview", content=user_answer,
            summary=user_answer[:120], facts=[], emotion=None, importance=8.0,
        )
        store.update_interview(db, session, state=state)
        return {"action": "crisis", "crisis": crisis, "message": CRISIS_MESSAGE}

    dims = _dims_for(session.loss_type)
    victim = _victim_context(db, session.user_id, user_answer) if session.loss_type == "breakup" else None
    step = _decide_step(client, session.loss_type, dims, state, user_answer, victim=victim)

    action = step.get("action")
    if action not in ("followup", "next", "complete"):
        action = "followup"

    question = step.get("question") or ""
    facts = step.get("facts") if isinstance(step.get("facts"), list) else []
    emotion = _normalize_emotion(step.get("emotion"))

    # 写回记忆流(访谈本身也是一种倾诉)
    place_tag, time_tag = extract_tags(user_answer)
    # 分手/亲友离世场景:额外抽 CBT/哀伤过程字段,让访谈直接初始化 gather_signals 的疗愈信号
    # (否则访谈结束后信号仍为空)。分手抽自动思维/认知扭曲/反刍/功能,丧亲抽渴望/内疚/意义/死亡接受/持续联结。
    if session.loss_type == "breakup":
        cbt = breakup_extract.extract_cbt(client, user_answer)
    elif session.loss_type == "loved_one":
        cbt = loved_one_extract.extract_grief_cbt(client, user_answer)
    elif session.loss_type == "pet":
        cbt = pet_extract.extract_pet_cbt(client, user_answer)
    else:
        cbt = None
    store.add_memory(
        db, session.user_id, type="interview", content=user_answer,
        summary=user_answer[:120], facts=facts, emotion=emotion, importance=6.0,
        place_tag=place_tag, time_tag=time_tag, cbt=cbt,
    )
    for f in facts:
        if isinstance(f, dict):
            state.setdefault("facts", []).append(f)

    # 覆盖追踪:当前维度问过即算覆盖,再合并 LLM 判定本次回答顺带聊到的维度,
    # 避免后面再重复问用户已经说过的事。
    covered = list(dict.fromkeys(state.get("covered", [])))
    cur_key = dims[state["dimension_idx"]]["key"]
    if cur_key not in covered:
        covered.append(cur_key)
    for k in (step.get("covered_dimensions") or []):
        if isinstance(k, str) and k not in covered:
            covered.append(k)
    state["covered"] = covered

    goal_idx = len(dims) - 1
    goal_key = dims[goal_idx]["key"]

    # 目标早期识别:用户已明确表达疗愈目标倾向(goal_signal=true)且目标维度还没聊过时,
    # 直接跳到目标维度做一次确认,不再把剩余维度走完。
    jumped_to_goal = False
    if bool(step.get("goal_signal")) and state["dimension_idx"] < goal_idx and goal_key not in covered:
        state["dimension_idx"] = goal_idx
        state["followup_count"] = 0
        action = "next"
        question = _bridge_question(client, session.loss_type, dims, state, goal_idx)
        jumped_to_goal = True

    # 追问次数保护
    if action == "followup" and state["followup_count"] >= MAX_FOLLOWUP_PER_DIM:
        action = "next"

    # 最后一个维度(目标)不再追问:用户确认疗愈目标后立即收尾生成报告,
    # 避免在"要不要写点纪念"这类延伸话题上继续拖延,影响用户获得反馈的节奏。
    if action == "followup" and state["dimension_idx"] == goal_idx:
        action = "complete"

    turns = len([h for h in state["history"] if h["role"] == "user"])
    if turns >= MAX_TURNS:
        action = "complete"

    if action == "followup":
        state["followup_count"] += 1
    elif jumped_to_goal:
        # 刚跳到目标维度:本轮先问出目标确认问题,下一轮用户回答后再收尾。
        pass
    elif action == "next":
        # 进入下一个还没被覆盖的维度,用 LLM 承接上下文写好的过渡问题(缺省才退回问卷原题)
        next_idx, next_q = _resolve_next(client, session.loss_type, dims, covered, step, state, state["dimension_idx"])
        if next_idx is not None:
            state["dimension_idx"] = next_idx
            state["followup_count"] = 0
            question = next_q
        else:
            action = "complete"

    if action == "complete":
        if async_report:
            # 报告生成放到后台:立即标记完成、推进阶段并返回,前端轮询 report_ready。
            state["done"] = True
            state["generating"] = True
            state["report"] = None
            state["report_ready"] = False
            store.update_interview(db, session, state=state, status="completed")
            onboarding.set_phase(db, session.user_id, "report")
            async_report_mod.schedule_report(
                session.user_id, session.id, session.loss_type, copy.deepcopy(state["history"])
            )
            return {"action": "complete", "done": True, "generating": True}

        report = _generate_report(db, client, session, state)
        state["done"] = True
        state["report"] = report
        state["report_ready"] = True
        store.update_interview(db, session, state=state, status="completed")
        onboarding.set_phase(db, session.user_id, "report")
        return {"action": "complete", "report": report}

    state["asked"].append(question)
    state["history"].append({"role": "assistant", "content": question})
    store.update_interview(db, session, state=state)
    return {
        "action": action,
        "question": question,
        "dimension": dims[state["dimension_idx"]]["title"],
        "emotion": emotion,
    }


def revise(db, session_id: str, supplement: str, client: LLMClient) -> dict:
    """人机协同:用户补充叙述后重新生成报告。"""
    session = store.get_interview(db, session_id)
    if session is None:
        raise ValueError("session not found")
    state = copy.deepcopy(session.state or {})
    state.setdefault("history", []).append({"role": "user", "content": f"[补充] {supplement}"})
    report = _generate_report(db, client, session, state)
    state["report"] = report
    store.update_interview(db, session, state=state)
    return report


def _facts_block(state: dict) -> str:
    """把访谈里已收集的事实压成一段,供引导员自然回调(让用户感到被记住)。"""
    lines = []
    for f in (state.get("facts") or [])[-12:]:
        if isinstance(f, dict):
            fact = (f.get("fact") or "").strip()
            if fact:
                lines.append(f"- {fact}")
    return "\n".join(lines) if lines else "(暂无)"


def _decide_step(client: LLMClient, loss_type: str | None, dims: list[dict], state: dict,
                 user_answer: str, victim: list[str] | None = None) -> dict:
    dim = dims[state["dimension_idx"]]
    recent = "\n".join(f"{h['role']}: {h['content'][:100]}" for h in state.get("history", [])[-6:])
    facts_block = _facts_block(state)
    dim_lines = "\n".join(f"- {d['key']}({d['title']}): {d['question']}" for d in dims)
    victim_note = ""
    if victim:
        victim_note = f"\n[安全边界] 检测到用户在被伤害情境({', '.join(victim)}):归因维度不要追问'你哪里做错了',转为共情 + 识别不健康关系。\n"
    messages = [
        {"role": "system", "content": STEP_SYSTEM},
        {"role": "user", "content": (
            f"失去类型:{loss_type or '未知'}\n"
            f"访谈维度清单(按顺序):\n{dim_lines}\n"
            f"当前维度:{dim['title']}(key={dim['key']})\n"
            f"本维度已追问次数:{state.get('followup_count', 0)}(最多 {MAX_FOLLOWUP_PER_DIM} 次)\n"
            f"已覆盖维度:{state.get('covered', []) or '无'}\n"
            f"已问过的问题:{state.get('asked', [])}\n"
            f"已收集到的关键信息(供自然回调,别复述原话):\n{facts_block}\n\n"
            f"最近对话:\n{recent}\n\n"
            f"用户最新回答:\n{user_answer}"
            f"{victim_note}"
        )},
    ]
    parsed, _ = client.chat_json(messages, temperature=0.6, model=client.settings.llm_fast_model)
    if not parsed:
        return {"action": "followup", "question": "", "facts": [], "emotion": {}, "note": "解析失败兜底"}
    return parsed


def _bridge_question(client: LLMClient, loss_type: str | None, dims: list[dict], state: dict, target_idx: int) -> str:
    """为切换到下一维度写一句自然过渡的话(承接上下文),避免空降问卷原题。"""
    target = dims[target_idx]
    recent = "\n".join(f"{h['role']}: {h['content'][:120]}" for h in state.get("history", [])[-4:])
    messages = [
        {"role": "system", "content": BRIDGE_SYSTEM},
        {"role": "user", "content": (
            f"失去类型:{loss_type or '未知'}\n"
            f"要过渡到的新话题:{target['title']}(参考问题「{target['question']}」,但不要照搬)\n"
            f"最近对话:\n{recent}\n\n"
            f"请写一句自然过渡的话。"
        )},
    ]
    try:
        result = client.chat(messages, temperature=0.7, max_tokens=120, model=client.settings.llm_fast_model)
        text = (result.get("content") if isinstance(result, dict) else "").strip()
    except Exception:
        text = ""
    return text or target["question"]


def _resolve_next(client: LLMClient, loss_type: str | None, dims: list[dict], covered: list[str],
                  step: dict, state: dict, current_idx: int) -> tuple[int | None, str]:
    """决定下一个要进入的维度并给出问题文案。

    优先采用 LLM 建议的、未覆盖的维度 + 它已写好的过渡句;
    否则顺序找 current_idx 之后第一个未覆盖维度,并临时让 LLM 写一句自然过渡(承接上下文),
    只有 LLM 失效时才退回问卷原题(兜底,仍有信息量)。
    """
    suggested = step.get("next_dimension")
    llm_q = (step.get("question") or "").strip()
    if isinstance(suggested, str) and suggested:
        for i, d in enumerate(dims):
            if d["key"] == suggested and d["key"] not in covered:
                return i, (llm_q or _bridge_question(client, loss_type, dims, state, i))
    for i in range(current_idx + 1, len(dims)):
        if dims[i]["key"] not in covered:
            return i, _bridge_question(client, loss_type, dims, state, i)
    return None, ""


class ReportGenerationError(RuntimeError):
    """The provider did not return a valid displayable report."""


def _valid_report(parsed):
    return (isinstance(parsed,dict)
            and all(isinstance(parsed.get(k),str) and parsed[k].strip() for k in ("title","summary"))
            and isinstance(parsed.get("quote",""),str)
            and isinstance(parsed.get("keywords",[]),list)
            and all(isinstance(k,str) for k in parsed.get("keywords",[]))
            and all(isinstance(parsed.get(k,{}),dict) for k in ("user_portrait","object_portrait")))


def _build_report_parsed(client: LLMClient, loss_type: str | None, history: list[dict],
                         subject_name: str | None = None) -> dict:
    """生成报告 JSON(纯 LLM 调用,无 DB 副作用),供同步 / 后台两路复用。"""
    conv = "\n".join(f"{h['role']}: {h['content']}" for h in (history or []))
    user_content = f"失去类型:{LOSS_TYPE_LABELS.get(loss_type, loss_type or '未知')}\n\n访谈对话:\n{conv}"
    # 用户在访谈里已确认过 TA 的称呼,优先用它,避免 LLM 从语音转写里又抽出旧名字。
    if subject_name:
        user_content += f"\n\n对象称呼(用户已确认):{subject_name}。请全文统一使用这个称呼,不要改成别的名字。"
    messages = [
        {"role": "system", "content": _report_system(loss_type)},
        {"role": "user", "content": user_content},
    ]
    # 报告走热路径快模型(默认),时延优先;要更高分析质量时,在 .env 把 llm_report_model 设为 llm_model。
    model = client.settings.llm_report_model or client.settings.llm_fast_model
    parsed, meta = client.chat_json(messages, temperature=0.5, max_tokens=4000, model=model)
    if not _valid_report(parsed):
        # 输出可能被截断或格式错误,再试一次
        messages.append({"role": "user", "content": "请只输出一个完整、合法的 JSON 对象,不要任何其他文字。"})
        parsed, _ = client.chat_json(messages, temperature=0.3, max_tokens=4000, model=model)
    if not _valid_report(parsed):
        raise ReportGenerationError("INVALID_REPORT_OUTPUT")
    return parsed


def _persist_report(db, user_id: str, loss_type: str | None, parsed: dict) -> None:
    """报告落库:画像 + 记忆流 + 看板(reports 表)。"""
    # 兜底:即便 LLM 没听进上面的称呼提示,也强制用用户确认过的名字覆盖对象画像的「称呼」。
    companion = store.get_current_companion(db, user_id)
    confirmed_name = (companion.subject_name or "").strip() if companion else ""
    if confirmed_name:
        obj = parsed.get("object_portrait")
        if not isinstance(obj, dict):
            obj = {}
        obj["称呼"] = confirmed_name
        parsed["object_portrait"] = obj

    user_portrait = parsed.get("user_portrait") or {}
    object_portrait = parsed.get("object_portrait") or {}

    # 分手:把关系复盘压成温暖连贯的一段,同时进画像(供跟踪报告/后续展示)与记忆流(供聊天召回)
    ra_text = ""
    if loss_type == "breakup" and parsed.get("relationship_analysis"):
        ra_text = _fmt_relationship_analysis(parsed["relationship_analysis"])
        if ra_text:
            user_portrait["关系复盘"] = ra_text

    # 亲友离世:把哀伤叙事压成温暖连贯的一段,同时进画像与记忆流
    ga_text = ""
    if loss_type == "loved_one" and parsed.get("grief_analysis"):
        ga_text = _fmt_grief_analysis(parsed["grief_analysis"])
        if ga_text:
            user_portrait["哀伤叙事"] = ga_text

    # 宠物离世:把哀伤叙事压成温暖连贯的一段,同时进画像与记忆流
    pa_text = ""
    if loss_type == "pet" and parsed.get("pet_grief_analysis"):
        pa_text = _fmt_pet_analysis(parsed["pet_grief_analysis"])
        if pa_text:
            user_portrait["哀伤叙事"] = pa_text

    store.upsert_portrait(db, user_id, "user", user_portrait, status="draft")
    store.upsert_portrait(db, user_id, "object", object_portrait, status="draft")
    store.add_memory(
        db, user_id, type="report", content=parsed.get("summary", ""),
        summary=(parsed.get("summary") or "")[:120], facts=[], emotion=None, importance=9.0,
    )
    if ra_text:
        store.add_memory(
            db, user_id, type="report", content=ra_text, summary=ra_text[:120],
            facts=[], emotion=None, importance=8.0,
        )
    if ga_text:
        store.add_memory(
            db, user_id, type="report", content=ga_text, summary=ga_text[:120],
            facts=[], emotion=None, importance=8.0,
        )
    if pa_text:
        store.add_memory(
            db, user_id, type="report", content=pa_text, summary=pa_text[:120],
            facts=[], emotion=None, importance=8.0,
        )
    store.save_report(db, user_id, "initial", parsed)


def _generate_report(db, client: LLMClient, session, state: dict) -> dict:
    companion = store.get_current_companion(db, session.user_id)
    subject_name = (companion.subject_name or "").strip() if companion else ""
    parsed = _build_report_parsed(client, session.loss_type, state.get("history", []),
                                  subject_name=subject_name or None)
    _persist_report(db, session.user_id, session.loss_type, parsed)
    return parsed


def progress(session) -> dict:
    """访谈对外状态视图:进度 / 当前维度 / 报告是否就绪,供前端轮询展示。"""
    state = session.state or {}
    dims = _dims_for(session.loss_type)
    idx = state.get("dimension_idx", 0)
    cur = dims[idx] if 0 <= idx < len(dims) else {}
    history = state.get("history", [])
    question = next(
        (h.get("content", "") for h in reversed(history) if h.get("role") == "assistant"), ""
    )
    report_ready = bool(state.get("report_ready"))
    done = bool(state.get("done")) or session.status == "completed"
    return {
        "session_id": session.id, "companion_id":session.companion_id, "phase":session.status,
        "user_id": session.user_id,
        "status": session.status,
        "done": done,
        "generating": bool(state.get("generating")) and not report_ready,
        "report_ready": report_ready,
        "report_status": "ready" if report_ready else "failed" if state.get("report_failed") else "generating" if state.get("generating") else "not_started",
        "question": question,
        "dimension": cur.get("title"),
        "dimension_key": cur.get("key"),
        "progress": {"covered": len(state.get("covered", [])), "total": len(dims)},
    }


def _normalize_emotion(e: dict | None) -> dict:
    e = e or {}
    emo = e.get("emotion") or "其他"
    try:
        valence = max(-1.0, min(1.0, float(e.get("valence", 0.0))))
        arousal = max(0.0, min(1.0, float(e.get("arousal", 0.5))))
        score = max(0, min(100, int(e.get("score", 50))))
    except (TypeError, ValueError):
        valence, arousal, score = 0.0, 0.5, 50
    return {"emotion": emo, "valence": valence, "arousal": arousal, "score": score}


def _fallback_report(state: dict, loss_type: str | None = None) -> dict:
    report = {
        "title": "谢谢你愿意说出来",
        "keywords": ["温柔", "诚实"],
        "summary": "谢谢你愿意把这些告诉我。我们已经一起梳理了这段关系的脉络,接下来会陪你慢慢往前走。",
        "quote": "慢慢来,也算在往前走",
        "user_portrait": {"失去类型": "暂未提及", "关系与背景": "暂未提及", "当前情绪状态": "暂未提及",
                          "情绪波动点": "暂未提及", "困惑点": "暂未提及", "未说出口的话": "暂未提及"},
        "object_portrait": {"称呼": "暂未提及", "关系": "暂未提及", "性格": "暂未提及",
                            "共同记忆": "暂未提及", "重要地点与时间": "暂未提及"},
        "goal": {"type": "uncertain", "label": "还没想清楚", "reason": "需要进一步对话"},
        "heal_plan": {"summary": "先稳定情绪,再逐步明确方向",
                      "stages": [{"title": "照顾好当下", "desc": "先保证睡眠和饮食", "time": "本周"}]},
    }
    if loss_type == "breakup":
        report["user_portrait"].update({
            "依恋类型": "暂未提及", "关系中的模式": "暂未提及",
            "自身盲区": "暂未提及", "可成长方向": "暂未提及", "关系症结": "暂未提及",
        })
        report["object_portrait"].update({
            "TA的行为模式": "暂未提及", "TA的依恋倾向": "暂未提及", "TA在关系中的问题": "暂未提及",
        })
        report["relationship_analysis"] = {
            "attachment": {"user": "暂未提及", "basis": "", "other": "暂未提及", "other_basis": ""},
            "causes": [], "patterns": "暂未提及", "blind_spots": [], "suggestions": [],
        }
    if loss_type == "loved_one":
        report["user_portrait"].update({
            "内疚与未竟之事": "暂未提及", "死亡接受状态": "暂未提及",
            "意义重建": "暂未提及", "现在的日常状态": "暂未提及",
        })
        report["object_portrait"].update({
            "TA留给你的": "暂未提及", "你心里的TA": "暂未提及",
        })
        report["grief_analysis"] = {
            "meaning": "暂未提及",
            "guilt": {"still_guilty": "", "reframe": ""},
            "continuing_bond": {"current": "暂未提及", "suggestions": []},
            "death_acceptance": "暂未提及",
        }
    if loss_type == "pet":
        report["user_portrait"].update({
            "内疚与未竟之事": "暂未提及", "死亡接受状态": "暂未提及",
            "意义重建": "暂未提及", "现在的日常状态": "暂未提及", "哀伤正名": "暂未提及",
        })
        report["object_portrait"].update({
            "它留给你的": "暂未提及", "你心里的它": "暂未提及",
        })
        report["pet_grief_analysis"] = {
            "meaning": "暂未提及",
            "guilt": {"still_guilty": "", "reframe": ""},
            "continuing_bond": {"current": "暂未提及", "suggestions": []},
            "death_acceptance": "暂未提及",
            "disenfranchised": "暂未提及",
        }
    return report
