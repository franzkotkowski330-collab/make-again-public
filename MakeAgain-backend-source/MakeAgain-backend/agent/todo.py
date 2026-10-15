"""待办清单工具:聊天里的「走」(第三个 tool calling 能力)。

用户在「想行动 / 求助·功能卡住」时,AI 给 1~3 个可勾选的小任务,
帮 TA 把「想走出来」落到一件件马上能做的事上;完成度反哺功能恢复信号。
与物品/照片不同,纯文本、零上传。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from config import get_settings
from memory import store

# 工具 schema:OpenAI function calling 格式
TODO_LIST_TOOL = {
    "type": "function",
    "function": {
        "name": "suggest_todo_list",
        "description": (
            "当用户明确表现出「想行动、想走出来」的信号时,直接调用本工具并填入 1~3 个具体小任务,以弹出可勾选的待办卡片"
            "(不要在正文问「要不要我帮你列…」,也不要只在回复里口头建议就了事)。信号只限两类:"
            "①求助型(「怎么办」「怎么才能不想」「想走出来」「不知道该怎么…」);"
            "②功能卡住(吃不下/睡不好/提不起劲/家里空落落)。任务要具体、马上能做,帮 TA 迈出第一步。"
            "反例(不要调用):用户只是在倾诉难受/想念/追忆/自责,没在求助、也没说身体或行动卡住时,"
            "先共情承接,不要给待办;想说没说出口的话/自责改用 suggest_self_note。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1~3 个具体、可勾选的小任务(如「下楼走十分钟」「给 TA 种的花浇水」),每个一句话",
                },
            },
            "required": ["tasks"],
        },
    },
}

_DIRECTION = "给出 1~3 个具体、马上能做的小任务,温柔地陪 TA 从最小的一步开始,别一次给太多。"


def execute_tool(db, user_id: str, tool_call: dict) -> dict:
    """执行工具(纯本地、零 LLM):当日去重 + 逐条落库。"""
    args = tool_call.get("function", {}).get("arguments") or tool_call.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    tasks = args.get("tasks") or []
    if isinstance(tasks, str):
        tasks = [tasks]
    tasks = [str(t).strip() for t in tasks if str(t).strip()][:3]

    if not tasks:
        return {"surface": False, "reason": "未给出有效任务"}

    offset = get_settings().timezone_offset_hours
    date_key = (datetime.now() + timedelta(hours=offset)).strftime("%Y-%m-%d")
    # 当日已邀过 → 不再刷
    if store.nudge_seen(db, user_id, "todo_invite", date_key):
        return {"surface": False, "reason": "今天已经给过待办清单,不要重复弹,自然回应即可"}

    return {"surface":True,"tasks":[{"content":t,"done":False,"status":"suggested"} for t in tasks],
            "direction":"这些是待确认建议，尚未保存；不要声称已加入清单。"}



def tool_payload(results: list[dict], reply: str) -> dict | None:
    """由工具执行结果组装返回给前端的结构化字段;无要展示的动作时返回 None。"""
    for r in results:
        if r.get("surface"):
            return {
                "type": "todo_list",
                "tasks": r.get("tasks") or [],
                "copy": reply,
            }
    return None


def mark_todo_done(db, todo_id: str) -> dict | None:
    """前端勾选后调用:完成度落库 + 反哺功能恢复信号(薄壳转调 store)。"""
    row = store.mark_todo_done(db, todo_id)
    if row is None:
        return None
    return {"id": row.id, "content": row.content, "done": row.done}


def list_todos(db, user_id: str) -> list[dict]:
    """看板:列出用户所有待办(未勾选在前,按时间倒序)。"""
    store.get_or_create_user(db, user_id)
    offset = get_settings().timezone_offset_hours
    return [{
        "id": r.id, "companion_id": r.companion_id,
        "content": r.content,
        "done": r.done,
        "created_at": r.ts.isoformat()+"Z",
    } for r in store.list_todos(db, user_id)]


def queue_view(db, user_id: str) -> dict:
    """007 小事页队列视图:待办 FIFO(最早加的最先做)+ 最近 4 件已做(完成时间倒序)。

    已做排序用 done_at(完成时间)而非 ts(创建时间);老数据 done_at 为空时回落到 ts。
    """
    store.get_or_create_user(db, user_id)
    offset = get_settings().timezone_offset_hours
    rows = store.list_todos(db, user_id)  # ts desc
    pending = [r for r in rows if not r.done]
    pending.sort(key=lambda r: r.ts)  # FIFO:最早加的排最前
    done = [r for r in rows if r.done]
    done.sort(key=lambda r: r.done_at or r.ts, reverse=True)  # 最近完成的在前
    done = done[:4]
    return {
        "queue": [{
            "id": r.id, "companion_id": r.companion_id,
            "content": r.content,
            "created_at": r.ts.isoformat()+"Z",
        } for r in pending],
        "history": [{
            "id": r.id, "companion_id": r.companion_id,
            "content": r.content,
            "done_at": (r.done_at or r.ts).isoformat()+"Z",
        } for r in done],
    }


_TASK_GEN_SYSTEM = (
    "你是「重逢」的助手。用户在经历失去(分手/亲友离世/宠物离世),刚表达了想走出来、求助或身体难受。"
    "根据 TA 这句话,给出 1~3 个具体、马上能做、温柔的小任务(每个一句话、≤20 字),"
    "要贴合 TA 说的内容、别空泛。只输出一个 JSON 对象,形如 {\"tasks\":[\"下楼走十分钟\",\"给自己泡杯热饮\"]},"
    "不要输出任何解释或额外文字。"
)


def generate_tasks(client, message: str) -> list[str]:
    """模型漏调待办时,用 fast 模型专门生成 1~3 个小任务。mock 下给确定性兜底。

    注意:要求模型输出 {\"tasks\":[...]} 对象,而不是裸数组——gateway.parse_json 只配平 {...}
    片段,裸 [...] 数组永远解析不出,会导致兜底空手而归。
    """
    if client.mock:
        return ["今晚早点休息", "出去走一走"]
    try:
        parsed, _ = client.chat_json(
            [{"role": "system", "content": _TASK_GEN_SYSTEM},
             {"role": "user", "content": message}],
            temperature=0.3, model=client.settings.llm_fast_model,
        )
        tasks = parsed.get("tasks") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
        if isinstance(tasks, list):
            return [str(t).strip() for t in tasks if str(t).strip()][:3]
    except Exception:  # noqa: BLE001 兜底失败不阻塞主链路
        pass
    return []


def fallback_todo(db, user_id: str, client, message: str, reply: str) -> dict | None:
    """模型漏调待办工具时的兜底:检测到「想行动/求助/功能卡住」信号 → 生成 1~3 个小任务并弹卡片。

    复用 execute_tool 做当日去重与落库;当日已邀过或生成失败则返回 None。
    """
    tasks = generate_tasks(client, message)
    if not tasks:
        return None
    r = execute_tool(db, user_id, {"function": {"name": "suggest_todo_list", "arguments": {"tasks": tasks}}})
    return tool_payload([r], reply)
