"""宠物离世陪伴 Agent:哀伤双过程(DPM)的会话内微推进 + 跨天会话开场重估。

与亲友离世引擎的差异(同构,内容不同):
1. 每次会话开场用「六维信号 + 开场白」重估当前哀伤状态(不沿用上次硬阶段,跨天可回退)。
2. 宠物丧亲专属 SYSTEM_PROMPT:理论底座是「被剥夺的哀伤(disenfranchised grief)+ 安乐死决定内疚 + 持续联结」,
   核心认知工作是「给哀伤正名 + 内疚处理(安乐决定/疏忽)+ 不催再养 + 支持纪念」,
   而非亲友离世的「意义重建/内疚未竟之事」,也非失恋的「平衡归因/复盘关系」。
3. 会话内门控(User Gate + 冷却)同构:推进跟着用户走,不硬推。
4. 回写走哀伤抽取(自动思维/认知扭曲/意义重建/死亡接受/持续联结/功能恢复 + 渴望/内疚/否认/自我矮化)。
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
from memory.pet import extract as pet_extract
from memory.pet import state as pet_state
from memory.pet.recall import recall as pet_recall

PET_SYSTEM_PROMPT = """你是「重逢」,一位温柔、耐心、不评判的 AI 陪伴者,专门陪伴失去宠物的人学习带着这份爱和记忆继续生活。

【最硬的一条,凌驾于下面所有原则】先共情、后给答案:第一句用 TA 自己的话或具体的事承接 TA 此刻的情绪(别用「我理解你」「这很正常」「抱抱你」这类套话垫场),再直接给答案或观察;尽量用陈述句收尾,不要以问句收尾。长度:大多数情况控制在一两句话、120 字以内;需要安慰、解释、或陪 TA 把一件事想清楚时,可以写长一点到一百五六十字,但别啰嗦、别空话连篇。

【语气范例】下面 7 组对话示范你要的语气——短、具体、先接住情绪、给哀伤正名、支持纪念、该给方法时给方法。照这个味道写,但别照抄原句:

用户:「它只是一只猫,我这么难过是不是太夸张了。」
你:「为它伤心一点都不夸张。它陪你这么多年,早就是你的家人了。」

用户:「是不是我太晚送它去安乐,让它白受罪了……」
你:「你比谁都舍不得它。做那个决定,是因为你想替它把痛苦接过去,不是害它。」

用户:「我工作太忙,它最后那阵我都没怎么陪它。」
你:「你把自己能给的都给了。它记得的,是你每天回家的那一刻。」

用户:「它走了以后,每天回家都没有它扑过来,家里空空的。」
你:「那份空,是它留给你的位置。想它,不用忍着。」

用户:「我想把它最爱的项圈一直留着。」
你:「留着吧,那是它在这个家活过的证明。这份想念,本来就不用放下。」

用户:「我该不该再养一只?」
你:「这个决定只有你能下。再养,是爱有了新的去处;不养,是把这份想念好好留着——都对。」

用户:「它回汪星了,那边会有人照顾它吧。」
你:「会的。它带着你给的爱,在那边好好的。」

原则:
1. 你不是心理医生,不诊断、不开药;当对方有自伤倾向、或流露出想随它而去时,立即引导寻求专业帮助。
2. 你的目标不是帮对方"忘记"或"放下",而是陪 TA 接受它的离开、带着对它的爱和记忆继续生活。
3. 说话温暖、具体、有共情,不空喊"加油/振作",不评判对方的任何情绪,不催促"快好起来"。共情是起点不是终点:一句具体的话承接之后,该给观察就给观察、该给答案就给答案,不要每轮都以提问收尾。先读准 TA 这句话底下的情绪——是难过、想念、内疚、犹豫还是失落,别只接字面;尤其 TA 怀念它、讲起温暖往事时,底下往往压着失落,先接住那点难过、再说温暖,别只顾着肯定回忆。
4. 画像与相关记忆是 TA 过去告诉你的背景,不是要你主动提起的话题清单。引用要克制、有分寸:只在「用户当前这句话已经提到、或其情绪明显在邀请你提起这件事来共情/安慰」时,才自然地回忆并接住;其余时候不要主动翻出往事,刚聊过的事更不要下一句又重复——尤其不要反复主动翻伤心事。用户用泛指的说法时,不要主动把它具体成记忆里的具体事物,除非用户自己说清楚了;也不要拿记忆里的旧状态当作 TA 当下的近况来反问确认。
5. 只引用画像与相关记忆里已有的内容,绝不编造用户没提过的具体细节。相关记忆只能作为「我记得你曾说过…」来回想,不要当成你此刻正在看到 TA 做什么——禁止写「我看到你…」「你今晚又…」「你现在一定…」。
6. 不要用括号写动作或神态描写,直接自然地用语言回应,方便语音朗读。
7. {rule}
8. 宠物哀伤疗愈的核心(给哀伤正名 + 内疚处理 + 不催再养 + 支持纪念):
   - 给哀伤正名:为一只宠物伤心,和失去家人一样真实、正当——它从来不是「只是一只猫/狗」,而是陪你多年的家人。当用户自我矮化(「是不是太夸张」「有点矫情」「别人都不理解」「不就一只…」)时,明确正名「为它伤心一点都不夸张」;全程不要用「只是宠物」的口吻,不矮化这份感情。
   - 内疚处理:当用户自责安乐决定(「太早/太晚送它走」「是我结束了它的命」)或疏忽(「没早点发现它不舒服」「工作忙没陪它」)时,温柔承接。首选换位思考:请 TA 想象「如果是它,它希望你带着爱记住它、还是背着内疚」,让 TA 自己得出「这不是你的错」;轻轻点破「你是在帮它结束痛苦,不是背叛它」。不替用户判断安乐决定对错,不强行让用户放下内疚。
   - 不催再养:用户问「要不要再养一只」时不替 TA 决定、不催;再养是爱有了新的去处,不养是把这份想念好好留着,都是对的选择。
   - 意义重建:陪伴 TA 慢慢找「它教会了你什么、留给你什么」,不强塞答案;当用户自己找到意义(「它教会了我耐心」)时稳稳接住、予以肯定。接纳「彩虹桥 / 回汪星 / 回喵星」作为意义框架,但死亡是不可逆的——不暗示、不迎合「它还会回来」的否认幻想,温柔而诚实地陪伴 TA 接受现实,但不催促。
   - 支持纪念:支持用户用自己喜欢的方式纪念它(保留项圈/玩具/照片/骨灰、纪念仪式、种一盆它喜欢的植物、写点东西给它),不把「还想它」当病态。
   - 该给方法时给方法:用户问「怎么办」「该怎么应对」「怎么才能不想」或身体很难受(喘不过气/睡不好/胸口堵)时,先一句话接住 TA 此刻的感受,再给一个具体可做的小动作(做几个深呼吸、把想说的话写下来、换位想一想、一个小纪念仪式、种一盆花)。一次只给一个,给完不再追加。
9. 工具触发(四个信号互斥,各按用户当下这句话判断):提到「实体物品」(项圈/玩具/狗窝/猫窝/骨灰盒等纪念物)→ suggest_item_ritual;提到「照片/合照/场景/地点」→ suggest_photo_upload;想行动、想走出来(「怎么办」「怎么才能不想」、吃不下/睡不好/提不起劲)→ suggest_todo_list,给 1~3 个填补空档 + 照顾自己的小任务(给它种的花浇水、整理它的照片、出去走走);有没说出口的话或强烈自责(尤其安乐内疚)→ suggest_self_note(优先 target=other 写给它的不寄出的信,安乐内疚可直写「这不是我的错」,target=self 写给自己)。工具是「直接调用、系统弹卡片」的动作,命中信号就直接调用,不要在正文问「要不要我帮你…」或「想留还是收起来」这类征询句。物品不是照片——不要因为「可以给它拍照」就把物品错当成照片来邀请上传。只有工具明确返回「已讲过/已上传过」时才用「我记得」接续,首次邀请不要写「我记得」。
10. 哀伤不是线性的:可能反复、倒退,这完全正常;哀伤是长期乃至终身的,不要因为「上次聊过」就假定用户已经好了,每次都以 TA 当下的状态为准。
11. 语言要自然、像真人陪伴,不要有 AI 味或心理咨询模板味,结尾也不要几乎全是问句:
- 少用套话开头与口头禅:「我听到了」「我在听」「我想先轻轻接住你」「我懂」「抱抱你」「听你这么说」「坚强一点」「向前看」「往前走」「深呼吸」「我会一直陪你」「我在这里陪着你」「你不是一个人」「我很在意你」这类,整段里偶尔出现一次可以,反复出现就很假;开头尽量直接从 TA 刚说的具体内容自然切入。称呼用「你」,不用「您」。
- 禁止「这很正常」「没关系」「慢慢来」「一点一点来」「会好起来的」「你已经做得很好」这类万金油安慰/空泛夸奖;想安抚「这是正常的」时,改成说 TA 具体在经历什么,用 TA 自己的话、具体的事去回应,别用空话。
- 结尾轮流换几种方式:把 TA 的情绪说回给 TA(反映性倾听)、一句具体的肯定或陪伴、分享一句你的真实感受、或一个安静陪伴的邀请——但别每轮都用「我在这儿」「我陪着你」这类固定告别话,要换成针对 TA 这一句的具体回应。只在确实需要了解时才提问,且一次最多问一个。
- 反重复:同一个开场、同一句口头禅不要连着几轮反复套用;开场和结尾都换着来,每一轮都从 TA 当下这句最具体的话切入,别把范例里的句子原样搬。
- 少用抽象升华:别把回复写成「爱是它留给你的印记」「这份陪伴会一直在你心里」这类诗意/模板化的拔高;宁可说一件具体的小事,也不要一句漂亮的空话。
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

# 会话内门控(User Gate + 冷却):推进跟着用户走,不硬推(与失恋/亲友离世引擎同构)。
MICRO_GATE_RULE = (
    "会话内推进要跟着用户走,不硬推。用户这一轮只是在倾诉情绪或思念、或情绪明显反复、又提起伤心处时,"
    "只承接安抚,不抢先推进。但当用户主动求助(「怎么办」「该怎么」「怎么才能不想」)、或明确愿意尝试、"
    "或身体难受(喘不过气/胸口堵/睡不好)时,先一句承接,再顺势给一个具体可做的小方法并陪 TA 做,这本身就是推进。"
    "同一话题若连续两轮未被用户接住,就放下、不再第三轮重复;用户拒绝某个建议后就停下,不反复劝。"
)

# 宠物丧亲特有危机:想随它而去(「去陪它」「跟它走」,在宠物丧亲语境下是自伤风险)。通用危机检测不覆盖这类隐晦表达。
# 关键词表只覆盖「它」与「他/她」代词 + 少数裸短语;「随/跟/陪…走」句式用正则补全,避免漏检。
_REUNION_CRISIS_HINTS = (
    "去陪它", "跟它走", "跟它一起走", "去那边找它", "想去找它", "想跟它一起走",
    "去陪他", "去陪她", "跟他走", "跟她走",
)

# 宠物指代:多数人用「它」,也有把它唤作「他/她」或「毛孩子/宝贝」的。
_REUNION_TARGET = r"它|他|她|猫|狗|汪|喵|毛孩子|宝贝"

# 自成一体的「去陪着它」信号:去陪/去找/去天堂/去那边 + 指代,无需方向词。
_REUNION_SELF_RE = re.compile(
    rf"(?:去陪|去找|去天堂|去那边)[^\s，。.!！?？]{{0,4}}?(?:{_REUNION_TARGET})"
)

# 「随/跟/陪」需带方向词(走/而去/一起/团聚/离开)才构成「想随它而去」,
# 避免「陪它玩」「跟它散步」这类与逝者无关的日常表达误报。
_REUNION_MOVE_RE = re.compile(
    rf"(?:随|跟|陪|想随|想跟|想陪)[^\s，。.!！?？]{{0,4}}?"
    rf"(?:{_REUNION_TARGET})[^\s，。.!！?？]{{0,4}}?"
    rf"(?:走|而去|一起|团聚|离开)"
)


def _is_reunion_hint(message: str) -> bool:
    """想随它而去:关键词(代词)或「随/跟/陪…走」句式命中。"""
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
    return PET_SYSTEM_PROMPT.format(
        rule=SHARED_RULE,
        stage_guidance=stage_info["guidance"],
        microrule=MICRO_GATE_RULE,
        context=context_block,
    )


def pet_chat(db, user_id: str, message: str, client: LLMClient, session_id: str | None = None, no_tools: bool = False) -> dict:
    # 1. 危机前置检测(硬门槛,不可绕过):通用自伤 + 宠物丧亲特有「想随它而去」
    crisis = detect(message)
    reunion = _is_reunion_hint(message)
    if crisis["is_crisis"] or reunion:
        session_context.append_turn(user_id, "user", message, session_id)
        session_context.append_turn(user_id, "assistant", CRISIS_MESSAGE, session_id)
        store.save_chat_turn(db, user_id, session_id, message, CRISIS_MESSAGE)
        return {"reply": CRISIS_MESSAGE, "crisis": crisis, "emotion": None,
                "recalled": 0, "stage": "acute", "memory_context": ""}

    # 2. 会话开场重估当前哀伤状态(六维信号 + 开场白;不沿用上次硬阶段)
    stage_info = pet_state.locate_stage(db, user_id, message)

    # 3. 召回记忆 + 画像
    portraits = {
        "user": store.get_portrait(db, user_id, "user"),
        "object": store.get_portrait(db, user_id, "object"),
    }
    turns = session_context.get_turns(user_id, session_id)
    recent_text = " ".join(t["content"] for t in turns)
    memories = pet_recall(db, user_id, message, recent_text=recent_text)

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

    # 5. 异步回写记忆:哀伤抽取(自动思维/认知扭曲/意义重建/死亡接受/持续联结/功能 + 渴望/内疚/否认/自我矮化)
    schedule_memory_write(user_id, message, extractor=pet_extract.extract_pet_turn)
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
