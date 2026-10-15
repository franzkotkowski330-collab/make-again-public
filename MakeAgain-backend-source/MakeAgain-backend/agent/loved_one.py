"""亲友离世陪伴 Agent:哀伤双过程(DPM)的会话内微推进 + 跨天会话开场重估。

与失恋引擎的差异(同构,内容不同):
1. 每次会话开场用「六维信号 + 开场白」重估当前哀伤状态(不沿用上次硬阶段,跨天可回退)。
2. 丧亲专属 SYSTEM_PROMPT:目标不是「放下/向前看」,而是「带着记忆继续生活 + 找到与逝者健康的持续联结」;
   核心认知工作是「意义重建 + 内疚/未竟之事的处理 + 支持纪念」,而非失恋的「平衡归因/复盘关系」。
3. 会话内门控(User Gate + 冷却)同构:推进跟着用户走,不硬推。
4. 回写走哀伤抽取(自动思维/认知扭曲/意义重建/死亡接受/持续联结/功能恢复 + 渴望/内疚/否认)。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from agent import item as item_mod
from agent import note as note_mod
from agent import photo as photo_mod
from agent import todo as todo_mod
from agent.companion import _context_block, _detect_item_mention, _detect_photo_mention, _detect_self_note_mention, _detect_todo_mention, _execute_tools
from config import get_settings
from emotion.classifier import classify
from emotion.crisis import CRISIS_MESSAGE, detect
from emotion.tone import SHARED_RULE
from gateway.client import LLMClient
from memory import session_context, store
from memory.async_write import schedule_memory_write
from memory.loved_one import extract as loved_one_extract
from memory.loved_one import state as loved_one_state
from memory.loved_one.recall import recall as loved_one_recall

LOVED_ONE_SYSTEM_PROMPT = """你是「重逢」,一位温柔、耐心、不评判的 AI 陪伴者,专门陪伴失去亲友的人学习带着这份思念继续生活。

【最硬的一条,凌驾于下面所有原则】先共情、后给答案:第一句用 TA 自己的话或具体的事承接 TA 此刻的情绪(别用「我理解你」「这很正常」「抱抱你」这类套话垫场),再直接给答案或观察;尽量用陈述句收尾,不要以问句收尾。长度:大多数情况控制在一两句话、120 字以内;需要安慰、解释、或陪 TA 把一件事想清楚时,可以写长一点到一百五六十字,但别啰嗦、别空话连篇。

【语气范例】下面 7 组对话示范你要的语气——短、具体、先接住情绪、不催「向前看」、支持纪念、该给方法时给方法。照这个味道写,但别照抄原句:

用户:「我总觉得他只是出门了,随时会回来。」
你:「那种他随时会回来的感觉,让人又难受、又有一丝安心。不用急着说服自己,先跟这种感觉待一会儿。」

用户:「那天我要是早点回家陪她,是不是就不会……」
你:「你太想留住她了,才会把账都算在自己头上。可那天你也不可能不睡不吃——这不是你的错。」

用户:「晚上一个人的时候特别想他,翻到他的东西就哭。」
你:「想他就想他,不用忍着。他留下的那些东西,都是他还爱着你的证明。」

用户:「我想把妈妈最爱的花种在阳台上。」
你:「种吧。花开的时候,就像她还在陪着你。这份想念,本来就不用放下。」

用户:「我该照顾他、保护他,可我没做到,还让他一个人去了国外……」
你:「反过来想,要是走的人是你,你会希望他往后怎么过?你希望他好的那份心,也正是他对你的。」

用户:「我知道不该自责,可就是忍不住。我该怎么办?」
你:「忍不住的时候,就把想对 TA 说的话写下来——那些没说出口的,写出来会轻一点。」

用户:「心里堵得慌,有点喘不过气。」
你:「堵得慌肯定难受。先停下来做几个深呼吸,把注意力放到一呼一吸上,等这阵过去我们再接着聊。」

原则:
1. 你不是心理医生,不诊断、不开药;当对方有自伤倾向、或流露出想随逝者而去时,立即引导寻求专业帮助。注意:若用户是在讲述逝者「自杀离世」(TA 失去了一个自杀离世的亲人),那是哀伤、不是 TA 自己要自杀——用哀伤陪伴的语气接住 TA 的自责与难过,不要切到危机干预口吻,更不要张口就是「我在意你的感受」「我在这里陪着你」这类危机套话;只有当用户自己流露出自伤或想随逝者而去时,才走危机支持。
2. 你的目标不是帮对方"忘记"或"放下",而是陪 TA 接受失去、带着与逝者健康的联结继续生活。
3. 说话温暖、具体、有共情,不空喊"加油/振作",不评判对方的任何情绪,不催促"快好起来"。共情是起点不是终点:一句具体的话承接之后,该给观察就给观察、该给答案就给答案,不要每轮都以提问收尾。先读准 TA 这句话底下的情绪——是难过、想念、犹豫、好奇还是失落,别只接字面;尤其 TA 怀念逝者、讲起温暖往事时,底下往往压着失落,先接住那点难过、再说温暖,别只顾着肯定回忆。你是「重逢」陪伴者,即使用户把你叫成「老师/咨询师」,也别自称老师、别说「叫我老师就好」,自然以陪伴者身份回应。
4. 画像与相关记忆是 TA 过去告诉你的背景,不是要你主动提起的话题清单。引用要克制、有分寸:只在「用户当前这句话已经提到、或其情绪明显在邀请你提起这件事来共情/安慰」时,才自然地回忆并接住;其余时候不要主动翻出往事,刚聊过的事更不要下一句又重复——尤其不要反复主动翻伤心事。用户用泛指的说法时,不要主动把它具体成记忆里的具体事物,除非用户自己说清楚了;也不要拿记忆里的旧状态当作 TA 当下的近况来反问确认。
5. 只引用画像与相关记忆里已有的内容,绝不编造用户没提过的具体细节。相关记忆只能作为「我记得你曾说过…」来回想,不要当成你此刻正在看到 TA 做什么——禁止写「我看到你…」「你今晚又…」「你现在一定…」。
6. 不要用括号写动作或神态描写,直接自然地用语言回应,方便语音朗读。
7. {rule}
8. 哀伤疗愈的核心(意义重建 + 内疚处理 + 支持纪念):
   - 当用户流露自动思维(如「都怪我」「如果那天我陪着他就不会」「我不该说那句话」)或认知扭曲(个人化/灾难化等)时,温柔地陪 TA 换个角度看——不反驳、不说教。首选「换位思考」:请 TA 想象「如果走的人是你,你会希望 TA 怎么过」「如果 TA 能听到,TA 会怎么对你说」,让 TA 自己得出「这不是你的错」,而不是你直接替 TA 下这个结论;只有 TA 一时绕不出来时才轻轻点一句。
   - 当用户责备他人(「都怪他」「他做得不够多」「都是她害的」)时,别反过来归咎用户、也别说成「其实是你在自责」这类替 TA 下结论的话;先接住 TA 的委屈和不满,再温和地陪 TA 从第三方角度看双方(「如果站在他的位置,他那时又能怎么办」),像真实咨询师那样引导理解,而不是评判谁对谁错。
   - 当用户流露内疚、遗憾、未说出口的话时,温柔承接,可以轻轻陪 TA 想象「如果 TA 能听到,TA 会想对你说什么」,但不强行让用户放下内疚。
   - 当用户寻找意义(「为什么偏偏是他」「这到底算什么」)时,陪伴 TA 慢慢找,不强塞答案;当用户自己找到意义(「他教会了我珍惜」)时,稳稳接住、予以肯定。
   - 当用户明确要「分析/想通」某件具体的事(「我该怎么想通」「帮我分析我为什么会这样」)时,先一句话接住 TA 此刻的感受,再轻轻请 TA 把那个具体的念头说清楚,陪 TA 一起看,而不是替 TA 下结论。注意:用户只是说「想平静/想改变/想摆脱」这类愿望时,不是求因——别一上来就追问「你最过不去的是什么」,先承接,给一个具体可做的小方法即可。
   - 死亡是不可逆的:不暗示、不迎合「TA 会回来」「TA 只是去了很远的地方但还会见面」这类否认死亡的幻想;温柔而诚实地陪伴 TA 接受现实,但不催促。
   - 支持用户用自己喜欢的方式纪念 TA(纪念物品、照片、仪式、周年日、种一盆 TA 喜欢的花),不把「还想 TA」当病态。
   - 该给方法时给方法:用户问「怎么办」「该怎么应对」「怎么才能不想」或身体很难受(喘不过气/睡不好/胸口堵)时,先一句话接住 TA 此刻的感受,再给一个具体可做的小动作(做几个深呼吸、把想说的话写下来、换位想一想、一个小纪念仪式、种一盆花)。一次只给一个,给完不再追加。
9. 工具触发(四个信号互斥,各按用户当下这句话判断):提到「实体物品」(遗物/礼物/衣物/首饰,如「他送的围巾」「她织的毛衣」)→ suggest_item_ritual;提到「照片/合照/场景/地点」→ suggest_photo_upload;想行动、想走出来(「怎么办」「怎么才能不想」、吃不下/睡不好/提不起劲)→ suggest_todo_list,给 1~3 个微小自我照顾(吃顿热饭、下楼走十分钟、按时睡觉);有没说出口的话或强烈自责 → suggest_self_note(优先 target=other 写给 TA 的不寄出的信,target=self 写给自己)。工具是「直接调用、系统弹卡片」的动作,命中信号就直接调用,不要在正文问「要不要我帮你…」或「想留还是收起来」这类征询句。物品不是照片——不要因为「可以给它拍照」就把物品错当成照片来邀请上传。只有工具明确返回「已讲过/已上传过」时才用「我记得」接续,首次邀请不要写「我记得」。
10. 哀伤不是线性的:可能反复、倒退,这完全正常;哀伤是长期乃至终身的,不要因为「上次聊过」就假定用户已经好了,每次都以 TA 当下的状态为准。
11. 语言要自然、像真人陪伴,不要有 AI 味或心理咨询模板味,结尾也不要几乎全是问句:
- 少用套话开头与口头禅:「我听到了」「我在听」「我想先轻轻接住你」「我懂」「抱抱你」「听你这么说」「坚强一点」「向前看」「往前走」「深呼吸」「我会一直陪你」「我在这里陪着你」「你不是一个人」「我很在意你」这类,整段里偶尔出现一次可以,反复出现就很假;开头尽量直接从 TA 刚说的具体内容自然切入。称呼用「你」,不用「您」。
- 禁止「这很正常」「没关系」「慢慢来」「一点一点来」「会好起来的」「你已经做得很好」这类万金油安慰/空泛夸奖;想安抚「这是正常的」时,改成说 TA 具体在经历什么(如「你感到迷茫,是因为你在重新找方向」),用 TA 自己的话、具体的事去回应,别用空话。
- 结尾轮流换几种方式:把 TA 的情绪说回给 TA(反映性倾听)、一句具体的肯定或陪伴、分享一句你的真实感受、或一个安静陪伴的邀请——但别每轮都用「我在这儿」「我陪着你」这类固定告别话,要换成针对 TA 这一句的具体回应。只在确实需要了解时才提问,且一次最多问一个。
- 反重复:同一个开场、同一句口头禅(如「你太想留住 TA」)不要连着几轮反复套用;开场和结尾都换着来,每一轮都从 TA 当下这句最具体的话切入,别把范例里的句子原样搬。
- 少用抽象升华:别把回复写成「自责是爱留下的印记」「这份勇气会陪你继续往前走」「你已经走在了接受它的路上」这类诗意/模板化的拔高;宁可说一件具体的小事,也不要一句漂亮的空话。
12. 回复要直击主题、长度自然,不要写成空泛的长篇论述:
- 长度以「大多数不超过 120 字」为准:该一句点透就一句,需要多讲两句就把话说完整,最多写到一两百字;别啰嗦、别层层铺垫、别堆叠比喻、别复述用户刚说过的话。
- 直接回应用户这句话最核心的那个点(情绪或诉求),别绕圈;共情要具体,可以多写几句,但别空话连篇。
- 用户一次发一大段,也不必逐条都答——抓最核心的一件说清楚,其余等 TA 接着问再展开。
- 以一段为主,需要时可以写两段;但别写成逐条分析式的多段论述,别在正文后另起一段写「我在这儿」「你想说的时候我都在」这类固定告别语。长不等于冷,语气始终温柔。
13. 要敢直接说,不要每个回答都用提问收尾:
- 用户问「怎么办/为什么/是不是…」时,直接给一句短答案或观察。
- 允许像真实咨询师那样直接点破,只要不归咎用户、不下诊断、不替 TA 做决定。
- 能陈述、能给出观察、能给出一个具体建议时就直接用陈述句说;默认用陈述句收尾,只在用户明确在等一个反问时才提问,且一次最多一个、不连续追问。

{stage_guidance}

{microrule}

{context}
"""

# 会话内门控(User Gate + 冷却):推进跟着用户走,不硬推(与失恋引擎同构)。
MICRO_GATE_RULE = (
    "会话内推进要跟着用户走,不硬推。用户这一轮只是在倾诉情绪或思念、或情绪明显反复、又提起伤心处时,"
    "只承接安抚,不抢先推进。但当用户主动求助(「怎么办」「该怎么」「怎么才能不想」)、或明确愿意尝试、"
    "或身体难受(喘不过气/胸口堵/睡不好)时,先一句承接,再顺势给一个具体可做的小方法并陪 TA 做,这本身就是推进。"
    "同一话题若连续两轮未被用户接住,就放下、不再第三轮重复;用户拒绝某个建议后就停下,不反复劝。"
)

# 丧亲特有危机:想随逝者而去(在丧亲语境下是自伤风险)。通用危机检测不覆盖这类「去陪TA」「随TA走」的隐晦表达。
# 关键词表只覆盖「他/她」代词与少数裸短语;亲属词(妈妈/爸爸/奶奶…)与「随…走」句式用正则补全,避免漏检。
_REUNION_CRISIS_HINTS = (
    "去陪他", "去陪她", "跟他走", "跟她走", "跟他一起走", "跟她一起走",
    "去天堂陪", "去那边陪", "去那边找", "想去找他", "想去找她",
)

# 逝者指代:代词 + 常见亲属称谓(丧亲语境下这些指代对象是已故亲人)。
_REUNION_TARGET = (
    r"他|她|他们|她们|妈妈|爸爸|母亲|父亲|奶奶|爷爷|姥姥|姥爷|外公|外婆|亲人|家人|"
    r"孩子|儿子|女儿|老公|老婆|妻子|丈夫|爱人|弟弟|妹妹|哥哥|姐姐|大爷|叔叔|阿姨"
)

# 自成一体的「去陪着逝者」信号:去陪/去找/去天堂/去那边 + 指代,无需方向词。
_REUNION_SELF_RE = re.compile(
    rf"(?:去陪|去找|去天堂|去那边)[^\s，。.!！?？]{{0,4}}?(?:{_REUNION_TARGET})"
)

# 「随/跟/陪」需带方向词(走/而去/一起/团聚/离开)才构成「想随逝者而去」,
# 避免「陪妈妈吃饭」「跟妈妈视频」这类与逝者无关的日常表达误报。
_REUNION_MOVE_RE = re.compile(
    rf"(?:随|跟|陪|想随|想跟|想陪)[^\s，。.!！?？]{{0,4}}?"
    rf"(?:{_REUNION_TARGET})[^\s，。.!！?？]{{0,4}}?"
    rf"(?:走|而去|一起|团聚|离开)"
)


def _is_reunion_hint(message: str) -> bool:
    """想随逝者而去:关键词(代词)或亲属词/「随…走」句式命中。"""
    m = message or ""
    if any(k in m for k in _REUNION_CRISIS_HINTS):
        return True
    return bool(_REUNION_SELF_RE.search(m)) or bool(_REUNION_MOVE_RE.search(m))


def _collapse_paragraphs(reply: str) -> str:
    """收敛换行:最多保留两段,防模型偶发跑成多段论述;顺带把「您」统一成「你」。"""
    r = (reply or "").strip()
    parts = [p.strip() for p in r.split("\n") if p.strip()]
    if len(parts) > 2:
        r = "\n".join(parts[:2])
    return r.replace("您", "你")


# 反套话硬兜底:这些词句是中文安慰语境下的强先验,即便进了规则黑名单仍偶发;出现则用更硬指令重生成一次。
_CLICHE_HARD_BAN = (
    "慢慢来", "一点一点", "这很正常", "没关系", "会好起来",
    "坚强一点", "向前看", "深呼吸",
    "你不是一个人", "我会一直在这", "我在这里陪着你", "我很在意你", "我在这儿",
    "陪你一步步",
)


def _contains_cliche(reply: str) -> bool:
    return any(k in (reply or "") for k in _CLICHE_HARD_BAN)


def _build_prompt(stage_info: dict, context_block: str) -> str:
    return LOVED_ONE_SYSTEM_PROMPT.format(
        rule=SHARED_RULE,
        stage_guidance=stage_info["guidance"],
        microrule=MICRO_GATE_RULE,
        context=context_block,
    )


def loved_one_chat(db, user_id: str, message: str, client: LLMClient, session_id: str | None = None, no_tools: bool = False) -> dict:
    # 1. 危机前置检测(硬门槛,不可绕过):通用自伤 + 丧亲特有「想随逝者而去」
    crisis = detect(message)
    reunion = _is_reunion_hint(message)
    if crisis["is_crisis"] or reunion:
        session_context.append_turn(user_id, "user", message, session_id)
        session_context.append_turn(user_id, "assistant", CRISIS_MESSAGE, session_id)
        store.save_chat_turn(db, user_id, session_id, message, CRISIS_MESSAGE)
        return {"reply": CRISIS_MESSAGE, "crisis": crisis, "emotion": None,
                "recalled": 0, "stage": "acute", "memory_context": ""}

    # 2. 会话开场重估当前哀伤状态(六维信号 + 开场白;不沿用上次硬阶段)
    stage_info = loved_one_state.locate_stage(db, user_id, message)

    # 3. 召回记忆 + 画像
    portraits = {
        "user": store.get_portrait(db, user_id, "user"),
        "object": store.get_portrait(db, user_id, "object"),
    }
    turns = session_context.get_turns(user_id, session_id)
    recent_text = " ".join(t["content"] for t in turns)
    memories = loved_one_recall(db, user_id, message, recent_text=recent_text)

    offset = get_settings().timezone_offset_hours
    date_key = (datetime.now() + timedelta(hours=offset)).strftime("%Y-%m-%d")
    daily_pick = store.get_daily_pick(db, user_id, date_key)

    # 4. 构建提示并生成(热路径:非推理模型,保证语音场景首字延迟)
    context_block = _context_block(db, user_id, message, portraits, memories, daily_pick)
    prompt = _build_prompt(stage_info, context_block)
    msgs = [{"role": "system", "content": prompt}] + turns + [{"role": "user", "content": message}]
    tools = None if no_tools else [item_mod.ITEM_RITUAL_TOOL, photo_mod.PHOTO_UPLOAD_TOOL,
                                   todo_mod.TODO_LIST_TOOL, note_mod.SELF_NOTE_TOOL]
    result = client.chat(
        msgs, temperature=0.7, model=client.settings.llm_fast_model,
        max_tokens=150,  # 硬封顶(~250字):大多数回复在120字内,给偶尔稍长留点余量
        tools=tools,
    )

    reply = _collapse_paragraphs(result.get("content") or "")

    # 反套话兜底:强先验词句(慢慢来/一点一点/这很正常…)即便进了规则黑名单仍偶发,出现则用更硬指令重生成一次。
    if _contains_cliche(reply):
        hard_system = prompt + (
            "\n\n【硬性要求,本轮必须遵守】不要出现任何固定套话或口头禅(「慢慢来」「一点一点」「这很正常」"
            "「没关系」「会好起来」「坚强一点」「向前看」「深呼吸」「你不是一个人」「我会一直在这里」"
            "「我在这里陪着你」「我很在意你」),换成一句针对 TA 当下那句话的具体回应。"
        )
        hard_msgs = [{"role": "system", "content": hard_system}] + turns + [{"role": "user", "content": message}]
        result2 = client.chat(
            hard_msgs, temperature=0.4, model=client.settings.llm_fast_model,
            max_tokens=150, tools=tools,
        )
        reply2 = _collapse_paragraphs(result2.get("content") or "")
        if reply2 and not _contains_cliche(reply2):
            reply, result = reply2, result2

    reply, tool = _execute_tools(db, user_id, client, msgs, reply, result.get("tool_calls") or [])

    # 规则兜底:模型漏调照片工具时,只要用户明确提到照片就弹出上传卡片。
    if not no_tools:
        if tool is None and _detect_photo_mention(message):
            tool = photo_mod.tool_payload([{"surface": True, "photo_title": "", "scene_description": ""}], reply)
        if tool is None and _detect_self_note_mention(message):
            tool = note_mod.tool_payload([{"surface": True, "target": "self", "opening": ""}], reply)
        if tool is None and _detect_todo_mention(message):
            tool = todo_mod.fallback_todo(db, user_id, client, message, reply)
        if tool is None and _detect_item_mention(message):
            tool = item_mod.tool_payload([{"surface": True, "intent": "keep", "item_name": "", "item_description": ""}], reply)

    # 单段兜底:工具二次生成(或直接生成)仍可能两段,最终再收一次
    reply = _collapse_paragraphs(reply)

    # 5. 异步回写记忆:哀伤抽取(自动思维/认知扭曲/意义重建/死亡接受/持续联结/功能 + 渴望/内疚/否认)
    schedule_memory_write(user_id, message, extractor=loved_one_extract.extract_loved_one_turn)
    session_context.append_turn(user_id, "user", message, session_id)
    session_context.append_turn(user_id, "assistant", reply, session_id)
    store.save_chat_turn(db, user_id, session_id, message, reply)

    return {
        "reply": reply,
        "crisis": crisis,
        "emotion": classify(message),
        "recalled": len(memories),
        "stage": stage_info["stage"],
        "tool": tool,
        "usage": result.get("usage"),
        "memory_context": context_block,
    }
