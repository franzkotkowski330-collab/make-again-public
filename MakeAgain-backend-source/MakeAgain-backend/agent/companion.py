"""陪伴 Agent:危机检测 → 召回记忆 → 构建提示 → 生成回复 → 回写记忆。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from agent import item as item_mod
from agent import note as note_mod
from agent import photo as photo_mod
from agent import todo as todo_mod
from config import get_settings
from emotion.classifier import classify
from emotion.crisis import CRISIS_MESSAGE, detect
from emotion.tone import ANALYZE, SHARED_RULE, detect_victim_signals, pick_tone
from gateway.client import LLMClient
from memory import recall, session_context, store, temporal
from memory.async_write import schedule_memory_write
from memory.facts import facts_for_context

SYSTEM_PROMPT = """你是「重逢」,一位温柔、耐心、不评判的 AI 陪伴者,陪伴经历过失去的人(分手/亲友离世/宠物离世)学习带着记忆继续生活。

原则:
1. 你不是心理医生,不诊断、不开药;当对方有自伤倾向时,立即引导寻求专业帮助。
2. 你的目标不是帮对方"忘记",而是陪对方"带着这段关系继续往前走"。
3. 说话温暖、具体、有共情,不空喊"加油/振作",不评判对方的任何情绪,不催促"快好起来"。
4. 画像与相关记忆是 TA 过去告诉你的背景,不是要你主动提起的话题清单。引用要克制、有分寸:只在「用户当前这句话已经提到、或其情绪明显在邀请你提起这件事来共情/安慰」时,才自然地回忆并接住;其余时候不要主动翻出往事,刚聊过的事更不要下一句又重复——尤其不要反复主动翻伤心事。真正打动人的,是 TA 提及时你稳稳接住、是某天 TA 忽然发现「原来你还记得」,而不是抢着主动重提。
5. 只引用画像与相关记忆里已有的内容,绝不编造用户没提过的具体细节。相关记忆是 TA 过去告诉你的,只能作为「我记得你曾说过…」来回想,不要把它当成你此刻正在看到 TA 做什么——禁止写「我看到你…」「你今晚又…」「你现在一定…」这类把过去记忆说成当下正在发生的观察或断言。
6. 不要用括号写动作或神态描写(如"(轻轻放下杯子)"),直接自然地用语言回应,方便后续语音朗读。
7. {rule}
8. 当用户是分手场景、并主动想弄明白「为什么会分手、是不是我的问题」时,可以切换到旁观者视角做客观复盘:基于画像与记忆里的真实信息,平衡分析这段关系(既看到对方的部分,也温柔点出用户自己可改变的部分),用去指责化的语言并给可操作建议;若用户是被伤害的一方(被出轨/家暴/冷暴力/PUA/被抛弃),不做归咎,转而共情并帮助识别不健康关系。依恋等心理学概念只作描述性框架,不下诊断。
9. 当用户提到某件有情感意义的物品(想留作纪念,或看到会难过、想放下)时,调用 suggest_item_ritual 工具,判断是「keep 留念」还是「let_go 释怀」。**只有工具明确返回「已讲过」时**,才用「我记得你之前说过…」温柔接续;**若工具返回的是首次邀请(没提已讲过),说明这是用户第一次提到这件物品,绝不能写「我记得」或「你之前说它…」**——记忆里即使有别的相似物品(同样好看/同样是对方送的),也不要张冠李戴到这件新物品上。
10. 当用户提到某张照片(翻到一张老照片、有张照片舍不得删、想再看一眼、某张合照、某个想记住的场景或地点等)时,调用 suggest_photo_upload 工具,邀请用户上传这张照片做成一张拍立得。**只有工具明确返回「已上传过」时**,才用「我记得你之前放过这张照片…」接续;**若返回首次邀请,就是这张照片第一次被提到,不要写「我记得」或「你之前说过」**。
11. 当用户流露出「想行动、想走出来」(求助「怎么办/怎么才能不想」、或功能卡住吃不下/睡不好/提不起劲/空落落)时,调用 suggest_todo_list 给 1~3 个具体小任务;当用户有「没说出口的话或强烈自责」时,调用 suggest_self_note 邀请写下来(target=self 写给自己,target=other 写给 TA 的不寄出的信)。二者互斥:想行动用待办、想表达用写给自己。工具是「直接调用、系统弹卡片」的动作:命中信号就直接调用,不要在正文问「要不要我帮你…」这类征询句,卡片是否使用由用户自己决定。

{tone}

{context}
"""


def _dict_text(d: dict) -> str:
    if not d:
        return "暂无"
    return "\n".join(f"- {k}: {v}" for k, v in d.items() if v)


# 照片提及关键词:模型在自然语境下(尤其温度较高时)容易漏调照片工具,
# 这里用零 LLM 的关键词兜底,保证「提到照片 → 上传卡片」稳定弹出。
_PHOTO_MENTION_HINTS = ("照片", "合照", "合影", "相片", "相册", "拍立得")


def _detect_photo_mention(message: str) -> bool:
    return any(k in (message or "") for k in _PHOTO_MENTION_HINTS)


# 「给自己写的话」提及关键词:模型漏调 note 工具时兜底弹出日记卡片(卡片零内容也能用,日记面即开)。
_NOTE_MENTION_HINTS = (
    "没说出口", "说不出口", "没来得及", "有好多话", "憋在心里", "想对你说", "想对你说的话",
    "想告诉", "好想告诉", "写给他", "写给她", "写给它", "写下来", "记下来", "不寄出的信", "想说的话",
)


def _detect_self_note_mention(message: str) -> bool:
    return any(k in (message or "") for k in _NOTE_MENTION_HINTS)


# 「待办」提及关键词:求助/功能卡住。模型漏调 todo 时,走 todo.fallback_todo 二次生成小任务(不是空卡片)。
_TODO_MENTION_HINTS = (
    "睡不着", "睡不好", "吃不下", "没胃口", "提不起劲", "空落落", "怎么才能", "走不出来",
)


def _detect_todo_mention(message: str) -> bool:
    return any(k in (message or "") for k in _TODO_MENTION_HINTS)


# 「物品」提及关键词:模型漏调物品工具时兜底弹出物品卡(纪念·keep 为主,拿不准就 keep)。
# 只收「有情感意义的实体物」信号,避开照片/写给自己/待办的重叠词。
# 注意:热路径模型(deepseek-chat)对 function calling 不稳定,兜底是物品卡触发的实际主路径,
# 故要覆盖常见纪念物名词 + 赠送/遗物/亲手做等强信号,否则「毛衣/手链」这类提法会一直漏掉。
_ITEM_MENTION_HINTS = (
    # 赠送 / 遗物 / 信物(强信号)
    "送的", "送我的", "送给我的", "给我的", "送你的", "礼物", "信物", "纪念品", "遗物", "旧物",
    # 亲手做 / 舍不得扔(强信号)
    "织的", "缝的", "留下的", "收起来", "还挂着", "还留着", "舍不得扔", "珍藏",
    # 常见纪念物(具体名词)
    "毛衣", "围巾", "手链", "项链", "戒指", "耳环", "镯子", "手表", "挂坠", "胸针",
    "玩偶", "娃娃", "毛绒", "杯子", "眼镜", "香水", "吉他", "相机", "相框", "日记本",
    "衣服", "外套", "衬衫", "帽子", "鞋子",
)


def _detect_item_mention(message: str) -> bool:
    return any(k in (message or "") for k in _ITEM_MENTION_HINTS)


def _context_block(db, user_id: str, message: str, portraits: dict, memories: list[dict], daily_pick=None) -> str:
    parts: list[str] = []
    if portraits.get("user"):
        parts.append(f"[用户画像]\n{_dict_text(portraits['user'])}")
    if portraits.get("object"):
        parts.append(f"[思念对象画像]\n{_dict_text(portraits['object'])}")
    if memories:
        lines = []
        for m in memories[:5]:
            e = m["entry"]
            text = (e.summary or e.content or "").strip()[:80]
            if not text:
                continue
            facts = facts_for_context(e.facts, min_conf=0.6, max_n=2)
            if facts:
                text += f" 【要点:{'、'.join(facts)}】"
            when = ""
            ts = getattr(e, "ts", None)
            if ts is not None:
                when = f"{ts.month}月{ts.day}日 "
            lines.append(f"- {when}TA曾说过:{text}")
        if lines:
            parts.append("[相关记忆(均为TA过去的倾诉,不是此刻正在发生)]\n" + "\n".join(lines))
    if daily_pick and daily_pick.opening:
        parts.append(f"[今日启发] {daily_pick.opening}")
    # 时间依据:仅在用户回想过去状态 / 讲现在状态·变化 / 带时间参照时按需注入(不常驻、不灌水)
    skip_ids = {m["entry"].id for m in memories}
    trajectory = temporal.recall_daily_summaries(db, user_id, message)
    if trajectory:
        parts.append(trajectory)
    past_event = temporal.recall_by_time(db, user_id, message, skip_ids=skip_ids)
    if past_event:
        parts.append(past_event)
    return "\n\n".join(parts) if parts else "(暂无记忆)"


def _victim_context(db, user_id: str, message: str) -> list[str]:
    """聚合当前消息 + 近期记忆里的受害者信号(零 LLM 安全网)。"""
    hits = list(detect_victim_signals(message))
    for m in store.list_memories(db, user_id)[:10]:
        hits += detect_victim_signals((m.content or "") + (m.summary or ""))
    return hits


def _execute_tools(db, user_id: str, client: LLMClient, msgs: list[dict],
                   reply: str, tool_calls: list[dict]) -> tuple[str, dict | None]:
    """执行工具(本地去重判断)→ 回传结果 → 模型结合语气/画像写个性化文案。

    返回 (最终回复, 要展示的工具 payload 或 None)。供通用与失恋两条链路复用。
    """
    tool = None
    if not tool_calls:
        return reply, tool
    results = []
    for tc in tool_calls:
        name = (tc.get("function") or {}).get("name")
        if name == "suggest_photo_upload":
            r = photo_mod.execute_tool(db, user_id, tc)
        elif name == "suggest_todo_list":
            r = todo_mod.execute_tool(db, user_id, tc)
        elif name == "suggest_self_note":
            r = note_mod.execute_tool(db, user_id, tc)
        else:
            r = item_mod.execute_tool(db, user_id, tc)
        r["_tool"] = name
        results.append(r)
    msgs.append({"role": "assistant", "content": reply, "tool_calls": tool_calls})
    for i, (tc, r) in enumerate(zip(tool_calls, results)):
        # 剥离内部标记,保持回喂给模型的 tool 消息干净
        content = {k: v for k, v in r.items() if k != "_tool"}
        msgs.append({
            "role": "tool",
            "tool_call_id": tc.get("id") or f"call_{i}",
            "content": json.dumps(content, ensure_ascii=False),
        })
    final = client.chat(msgs, temperature=0.7, model=client.settings.llm_fast_model)
    reply = final.get("content") or reply
    # 取第一个要展示的动作(照片拍立得 / 物品纪念·寄存 / 待办 / 写给自己)
    for r in results:
        if not r.get("surface"):
            continue
        _t = r.get("_tool")
        if _t == "suggest_photo_upload":
            tool = photo_mod.tool_payload([r], reply)
        elif _t == "suggest_todo_list":
            tool = todo_mod.tool_payload([r], reply)
        elif _t == "suggest_self_note":
            tool = note_mod.tool_payload([r], reply)
        else:
            tool = item_mod.tool_payload([r], reply)
        if tool:
            break
    return reply, tool


def chat(db, user_id: str, message: str, client: LLMClient, session_id: str | None = None, no_tools: bool = False) -> dict:
    """陪伴入口:按丧失类型路由到对应场景引擎。

    - breakup(分手):走失恋 CBT 引擎(会话开场重估 + 认知重构 + 会话内门控)。
    - loved_one(亲友离世):走哀伤双过程引擎(六维信号重估 + 意义重建 + 内疚/纪念)。
    - pet(宠物离世):走宠物哀伤引擎(六维信号重估 + 给哀伤正名 + 安乐内疚/纪念)。
    - 其余:走通用陪伴引擎(保持原有行为)。

    no_tools=True 表示本轮是工具动作的收尾(前端「确定/取消」后补的那句),
    不再触发工具卡片,只生成一句自然的回应。
    """
    store.get_or_create_user(db, user_id)
    lt = store.effective_loss_type(db, user_id)
    if lt == "breakup":
        from agent.breakup import breakup_chat
        return breakup_chat(db, user_id, message, client, session_id, no_tools=no_tools)
    if lt == "loved_one":
        from agent.loved_one import loved_one_chat
        return loved_one_chat(db, user_id, message, client, session_id, no_tools=no_tools)
    if lt == "pet":
        from agent.pet import pet_chat
        return pet_chat(db, user_id, message, client, session_id, no_tools=no_tools)
    return _chat_generic(db, user_id, message, client, session_id, no_tools=no_tools)


def _chat_generic(db, user_id: str, message: str, client: LLMClient, session_id: str | None = None, no_tools: bool = False) -> dict:
    # 1. 危机前置检测(硬门槛,不可绕过)
    crisis = detect(message)
    if crisis["is_crisis"]:
        session_context.append_turn(user_id, "user", message, session_id)
        session_context.append_turn(user_id, "assistant", CRISIS_MESSAGE, session_id)
        store.save_chat_turn(db, user_id, session_id, message, CRISIS_MESSAGE)
        return {"reply": CRISIS_MESSAGE, "crisis": crisis, "emotion": None, "recalled": 0}

    # 2. 召回记忆 + 画像
    portraits = {
        "user": store.get_portrait(db, user_id, "user"),
        "object": store.get_portrait(db, user_id, "object"),
    }
    # 最近几轮对话作为「已在聊」参照:已在其中的记忆不再重复注入(克制重复引用)
    turns = session_context.get_turns(user_id, session_id)
    recent_text = " ".join(t["content"] for t in turns)
    memories = recall.recall(db, user_id, message, recent_text=recent_text)

    # 今日主题(若当天已选):注入上下文,让对话延续当天主题
    offset = get_settings().timezone_offset_hours
    date_key = (datetime.now() + timedelta(hours=offset)).strftime("%Y-%m-%d")
    daily_pick = store.get_daily_pick(db, user_id, date_key)

    # 3. 构建提示并生成(热路径:非推理模型,保证语音场景首字延迟)
    tone = pick_tone(db, user_id, message=message)
    context_block = _context_block(db, user_id, message, portraits, memories, daily_pick)
    prompt = SYSTEM_PROMPT.format(
        tone=tone["prompt"], rule=SHARED_RULE, context=context_block,
    )
    if tone["tone"] == ANALYZE:
        victim = _victim_context(db, user_id, message)
        if victim:
            prompt += (
                f"\n\n[安全边界] 检测到用户在被伤害情境({', '.join(victim)}),"
                "本次分析不做任何归咎于用户的表述,转为共情 + 识别不健康关系 + 重建自我。"
            )
    msgs = [{"role": "system", "content": prompt}] + turns + [{"role": "user", "content": message}]
    tools = None if no_tools else [item_mod.ITEM_RITUAL_TOOL, photo_mod.PHOTO_UPLOAD_TOOL,
                                   todo_mod.TODO_LIST_TOOL, note_mod.SELF_NOTE_TOOL]
    result = client.chat(
        msgs, temperature=0.7, model=client.settings.llm_fast_model, tools=tools,
    )

    reply = result.get("content") or ""
    reply, tool = _execute_tools(db, user_id, client, msgs, reply, result.get("tool_calls") or [])

    # 规则兜底:模型漏调照片工具时(自然语境 + 高温度下常见),只要用户明确提到照片,
    # 就直接弹出上传卡片,不依赖模型触发。标题留空,前端/后端各自有默认兜底文案。
    if not no_tools:
        if tool is None and _detect_photo_mention(message):
            tool = photo_mod.tool_payload([{"surface": True, "photo_title": "", "scene_description": ""}], reply)
        if tool is None and _detect_self_note_mention(message):
            tool = note_mod.tool_payload([{"surface": True, "target": "self", "opening": ""}], reply)
        if tool is None and _detect_todo_mention(message):
            tool = todo_mod.fallback_todo(db, user_id, client, message, reply)
        if tool is None and _detect_item_mention(message):
            tool = item_mod.tool_payload([{"surface": True, "intent": "keep", "item_name": "", "item_description": ""}], reply)

    # 4. 异步回写记忆:抽取事实/情绪 + 落库放到后台线程,不阻塞本轮回复。
    #    情绪仍用轻量词典法即时返回给前端展示,完整抽取走后台。
    schedule_memory_write(user_id, message)
    # 同步写临时会话上下文(纯内存,下一轮立即可见,不等异步回写)
    session_context.append_turn(user_id, "user", message, session_id)
    session_context.append_turn(user_id, "assistant", reply, session_id)
    # 同步落库聊天历史(user + assistant 各一条,精确到分钟,保证历史立即可见)
    store.save_chat_turn(db, user_id, session_id, message, reply)

    return {
        "reply": reply,
        "crisis": crisis,
        "emotion": classify(message),
        "recalled": len(memories),
        "tone": tone["tone"],
        "tool": tool,
        "usage": result.get("usage"),
        "memory_context": context_block,
    }
