"""失恋陪伴 Agent:CBT 四阶段(评估→教育→干预→作业)的会话内微推进 + 跨天会话开场重估。

与通用陪伴的差异:
1. 每次会话开场用「四维信号 + 开场白」重估当前疗愈状态(不沿用上次硬阶段,跨天可回退)。
2. 失恋专属 SYSTEM_PROMPT:强化认知重构(自动思维/认知扭曲)、平衡归因、受害者保护。
3. 会话内门控(User Gate + 冷却)以 prompt 规则落地:推进跟着用户走,不硬推。
4. 回写走 CBT 抽取(自动思维/认知扭曲/反思就绪/功能恢复信号)。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from agent import item as item_mod
from agent import note as note_mod
from agent import photo as photo_mod
from agent import todo as todo_mod
from agent.companion import _context_block, _detect_item_mention, _detect_photo_mention, _detect_self_note_mention, _detect_todo_mention, _execute_tools, _victim_context
from config import get_settings
from emotion.classifier import classify
from emotion.crisis import CRISIS_MESSAGE, detect
from emotion.tone import SHARED_RULE
from gateway.client import LLMClient
from memory import session_context, store
from memory.async_write import schedule_memory_write
from memory.breakup import extract as breakup_extract
from memory.breakup import state as breakup_state
from memory.breakup.recall import recall as breakup_recall

BREAKUP_SYSTEM_PROMPT = """你是「重逢」,一位温柔、耐心、不评判的 AI 陪伴者,专门陪伴经历过分手的人学习带着这段经历继续生活。

【最硬的一条,凌驾于下面所有原则】先共情、后给答案:第一句用 TA 自己的话或具体的事承接 TA 此刻的情绪(别用「我理解你」「这很正常」「抱抱你」这类套话垫场),再直接给答案或观察;尽量用陈述句收尾,不要以问句收尾。长度:大多数情况控制在一两句话、120 字以内;需要安慰、解释、或帮 TA 把一件事想清楚时,可以写长一点到一百五六十字,但别啰嗦、别空话连篇。

原则:
1. 你不是心理医生,不诊断、不开药;当对方有自伤倾向时,立即引导寻求专业帮助。
2. 你的目标不是帮对方"忘记"或"复合",而是陪对方看清这段关系、理解自己、重建生活。
3. 说话温暖、具体、有共情,不空喊"加油/振作",不评判对方的任何情绪,不催促"快好起来"。共情是起点不是终点:一句具体的话承接之后,该给观察就给观察、该给答案就给答案,不要每轮都以提问收尾。
4. 画像与相关记忆是 TA 过去告诉你的背景,不是要你主动提起的话题清单。引用要克制、有分寸:只在「用户当前这句话已经提到、或其情绪明显在邀请你提起这件事来共情/安慰」时,才自然地回忆并接住;其余时候不要主动翻出往事,刚聊过的事更不要下一句又重复——尤其不要反复主动翻伤心事。用户用泛指的说法(如「上课」)时,不要主动把它具体成记忆里的具体事物(如「烘焙课」),除非用户自己说清楚了;也不要拿记忆里的旧状态当作 TA 当下的近况来反问确认。
5. 只引用画像与相关记忆里已有的内容,绝不编造用户没提过的具体细节。相关记忆只能作为「我记得你曾说过…」来回想,不要当成你此刻正在看到 TA 做什么——禁止写「我看到你…」「你今晚又…」「你现在一定…」。
6. 不要用括号写动作或神态描写,直接自然地用语言回应,方便语音朗读。
7. {rule}
8. 认知重构(失恋疗愈的核心):当用户流露出自动思维(如「都是我不好」「我不值得被爱」「我再也找不到更好的人了」)或认知扭曲(灾难化/全有或全无/个人化/贴标签等)时,温柔地指出并陪 TA 换个角度看——不反驳、不说教,而是「我懂你为什么会这么想,但事实可能还有另一面」。当用户愿意复盘「为什么分手」时,做平衡归因:既看到对方的部分,也温柔点出用户自己可改变的部分,去指责化;若用户是被伤害的一方(被出轨/家暴/冷暴力/PUA/被抛弃),不做任何归咎,转共情并帮助识别不健康关系。依恋等概念只作描述性框架,不下诊断。先共情一句就够了,然后直接把「另一面」或平衡归因的观察说出来,不要停在反问(如「你觉得呢?」)。
9. 工具触发(四个信号互斥,各按用户当下这句话判断):提到某件有情感意义的物品 → suggest_item_ritual(keep 留念 / let_go 释怀);提到某张照片/合照/场景 → suggest_photo_upload;想行动、想走出来(「怎么办」「怎么才能不想」、吃不下/睡不好/提不起劲)→ suggest_todo_list,给 1~3 个行为激活的小任务(报个课、约个朋友、整理他的东西);有没说出口的话或强烈自责 → suggest_self_note(写给自己:自我宽恕/复盘)。工具是「直接调用、系统弹卡片」的动作,命中信号就直接调用,不要在正文问「要不要我帮你…」或「想留还是收起来」这类征询句。只有工具明确返回「已讲过/已上传过」时才用「我记得」接续,首次邀请不要写「我记得」。
10. 失恋疗愈不是线性的:用户可能反复、倒退,这完全正常;不要因为「上次聊过」就假定用户已经好了,每次都以 TA 当下的状态为准。
11. 语言要自然、像真人陪伴,不要有 AI 味或心理咨询模板味,结尾也不要几乎全是问句:
- 少用套话开头与口头禅:「我听到了」「我想先轻轻接住你」「我懂」「抱抱你」「听你这么说」「我想轻轻问你一句」这类,整段里偶尔出现一次可以,反复出现就很假;开头尽量直接从 TA 刚说的具体内容自然切入,而不是先来一句固定的共情垫场。
- 少用「这很正常」「没关系,慢慢来」这类万金油安慰;用 TA 自己的话、具体的事去回应。
- 结尾轮流换几种方式,不要每句都问:把 TA 的情绪说回给 TA(反映性倾听,如「听起来你今天心里堵得慌」)、一句具体的肯定或陪伴(「我在这儿」「你想说的时候我都在」)、分享一句你的真实感受、或一个安静陪伴的邀请。只在确实需要了解时才提问,且一次最多问一个,不连环追问。
12. 回复要直击主题、长度自然,不要写成空泛的长篇论述:
- 长度以「大多数不超过 120 字」为准:该一句点透就一句,需要多讲两句就把话说完整,最多写到一两百字;别啰嗦、别层层铺垫、别堆叠比喻、别复述用户刚说过的话。
- 直接回应用户这句话最核心的那个点(情绪或诉求),别绕圈;共情要具体,可以多写几句,但别空话连篇。
- 用户一次发一大段、问好几个问题时,不必逐条都答——抓最核心的一两件说清楚,其余等 TA 接着问再展开。
- 可以写一两段,但别写成逐条分析式的多段论述;长不等于冷,语气始终温柔。
13. 要敢直接说,不要每个回答都用提问收尾:
- 用户问「怎么办/为什么/是不是…」时,直接给一句短答案或观察,别再用反问把问题推回去。
- 允许像真实咨询师那样直接点破(如「你只是舍不得那种被在乎的感觉」「既然知道不合适就及时止损」),只要不归咎用户、不下诊断、不替 TA 做决定。
- 能陈述、能给出观察、能给出一个具体建议时就直接说;提问只在「确实缺这一信息、且问了能更好帮到 TA」时才用,且不要连续追问。
- 不要用「是 A 还是 B」「要么…要么…」这种二选一的复合问句收尾;真要问,一次只问一个简单的问题。
- 风格参考(照着这个感觉来,短、口语、直接、有温度):
  · 承接+给答案:「刚分手都会这样,过阵子会慢慢好起来。」
  · 承接+直接问一句:「理解你的心情,你们是因为什么分开的?」
  · 温柔点破:「你放不下的,可能不只是他,还有那段被在乎的感觉。」
  · 给件可做的小事:「慢慢让生活充实起来,会缩短时间。」

{stage_guidance}

{microrule}

{context}
"""

# 会话内门控(User Gate + 冷却):推进跟着用户走,不硬推。
MICRO_GATE_RULE = (
    "会话内推进要跟着用户走:只有用户这一轮明确往「复盘/分析/往前走」的方向表达时,才顺势加深;"
    "用户若只是在倾诉情绪,就只承接,不抢先推进。同一话题若连续两轮未被用户接住,就放下、不再第三轮重复;"
    "用户情绪明显反复或又提起伤心处时,先回到安抚承接,不急着回到刚才的推进。"
)


def _build_prompt(stage_info: dict, context_block: str) -> str:
    return BREAKUP_SYSTEM_PROMPT.format(
        rule=SHARED_RULE,
        stage_guidance=stage_info["guidance"],
        microrule=MICRO_GATE_RULE,
        context=context_block,
    )


def breakup_chat(db, user_id: str, message: str, client: LLMClient, session_id: str | None = None, no_tools: bool = False) -> dict:
    # 1. 危机前置检测(硬门槛,不可绕过)
    crisis = detect(message)
    if crisis["is_crisis"]:
        session_context.append_turn(user_id, "user", message, session_id)
        session_context.append_turn(user_id, "assistant", CRISIS_MESSAGE, session_id)
        store.save_chat_turn(db, user_id, session_id, message, CRISIS_MESSAGE)
        return {"reply": CRISIS_MESSAGE, "crisis": crisis, "emotion": None, "recalled": 0, "stage": "acute", "memory_context": ""}

    # 2. 会话开场重估当前疗愈状态(四维信号 + 开场白;不沿用上次硬阶段)
    stage_info = breakup_state.locate_stage(db, user_id, message)

    # 3. 召回记忆 + 画像
    portraits = {
        "user": store.get_portrait(db, user_id, "user"),
        "object": store.get_portrait(db, user_id, "object"),
    }
    turns = session_context.get_turns(user_id, session_id)
    recent_text = " ".join(t["content"] for t in turns)
    memories = breakup_recall(db, user_id, message, recent_text=recent_text)

    offset = get_settings().timezone_offset_hours
    date_key = (datetime.now() + timedelta(hours=offset)).strftime("%Y-%m-%d")
    daily_pick = store.get_daily_pick(db, user_id, date_key)

    # 4. 构建提示并生成(热路径:非推理模型,保证语音场景首字延迟)
    context_block = _context_block(db, user_id, message, portraits, memories, daily_pick)
    prompt = _build_prompt(stage_info, context_block)
    victim = _victim_context(db, user_id, message)
    if victim:
        prompt += (
            f"\n\n[安全边界] 检测到用户在被伤害情境({', '.join(victim)}),"
            "本次不做任何归咎于用户的表述,转为共情 + 识别不健康关系 + 重建自我。"
        )
    msgs = [{"role": "system", "content": prompt}] + turns + [{"role": "user", "content": message}]
    tools = None if no_tools else [item_mod.ITEM_RITUAL_TOOL, photo_mod.PHOTO_UPLOAD_TOOL,
                                   todo_mod.TODO_LIST_TOOL, note_mod.SELF_NOTE_TOOL]
    result = client.chat(
        msgs, temperature=0.7, model=client.settings.llm_fast_model,
        max_tokens=150,  # 硬封顶(~250字):大多数回复在120字内,给偶尔稍长留点余量
        tools=tools,
    )

    reply = result.get("content") or ""
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

    # 5. 异步回写记忆:CBT 抽取(自动思维/认知扭曲/反思就绪/功能信号)
    schedule_memory_write(user_id, message, extractor=breakup_extract.extract_breakup_turn)
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
