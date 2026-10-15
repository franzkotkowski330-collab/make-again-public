"""日记工具:聊天里的「说」(第四个 tool calling 能力)。

当用户有「没说出口的话 / 强烈自责 / 想记一个念头或里程碑」时,邀请 TA 把它写成一篇日记:
- target=self:写给自己(自我关怀/宽恕/复盘/里程碑);
- target=other:写给 TA 的不寄出的信(未说出口的话/内疚,死亡场景核心干预)。
纯文本、零上传;用户提交后落 diary_notes(收藏室日记可见)+ 入记忆流(type="note")供召回。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from config import get_settings
from emotion.classifier import classify
from memory import store

# 工具 schema:OpenAI function calling 格式
SELF_NOTE_TOOL = {
    "type": "function",
    "function": {
        "name": "suggest_self_note",
        "description": (
            "当用户有「没说出口的话」(「有好多话没来得及跟 TA 说」「憋在心里」)或「强烈自责内疚」"
            "(「都怪我」「我对不起 TA」)时,直接调用本工具以弹出日记卡片,不要在正文问「要不要写下来」。"
            "target=self 是写给自己的话(宽恕/复盘),target=other 是写给 TA 的不寄出的信(没说出口的话)。"
            "注意:若用户只是想行动、想走出来(求助/功能卡住),改用 suggest_todo_list,不要用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["self", "other"],
                           "description": "self=写给自己;other=写给 TA(逝者/前任/宠物)不寄出的信"},
                "opening": {"type": "string",
                            "description": "一句开场白/提示,帮 TA 起个头,可为空"},
            },
            "required": ["target"],
        },
    },
}

_SELF_DIRECTION = "邀请用户写一段给自己的话:自我关怀、宽恕自己、或记下这个里程碑。"
_OTHER_DIRECTION = "邀请用户写一封写给 TA 的不寄出的信,把没说出口的话写下来。"


def execute_tool(db, user_id: str, tool_call: dict) -> dict:
    """执行工具(纯本地、零 LLM):当日去重 + 文案方向(不落库,用户还没写)。"""
    args = tool_call.get("function", {}).get("arguments") or tool_call.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    target = args.get("target") or "self"
    if target not in ("self", "other"):
        target = "self"
    opening = (args.get("opening") or "").strip()

    offset = get_settings().timezone_offset_hours
    date_key = (datetime.now() + timedelta(hours=offset)).strftime("%Y-%m-%d")
    # 当日已邀过 → 不再刷
    if store.nudge_seen(db, user_id, "note_invite", date_key):
        return {"surface": False, "reason": "今天已经邀请过写给自己,不要重复弹,自然回应即可"}

    store.mark_nudge(db, user_id, "note_invite", date_key)

    direction = _SELF_DIRECTION if target == "self" else _OTHER_DIRECTION
    return {"surface": True, "target": target, "opening": opening, "direction": direction}


def tool_payload(results: list[dict], reply: str) -> dict | None:
    """由工具执行结果组装返回给前端的结构化字段;无要展示的动作时返回 None。"""
    for r in results:
        if r.get("surface"):
            return {
                "type": "self_note",
                "target": r.get("target"),
                "opening": r.get("opening") or "",
                "copy": reply,
            }
    return None


def save_note(db, user_id: str, text: str, target: str = "self", emotion: str | None = None) -> dict:
    """用户提交日记后落库(便签即日记):与 /diary 共用 diary_notes 单一事实源 + 入记忆流供召回。

    target(self/other)仅为聊天引导方向提示,不落库;情绪用词典兜底归类。
    返回与 /diary 一致的 {ok, note_id, emotion, created_at}(created_at 已转东八区)。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False}
    store.get_or_create_user(db, user_id)
    emotion = emotion or classify(text).get("emotion")
    note = store.add_diary_note(db, user_id, text, emotion=emotion)
    store.add_memory(db, user_id, type="note", content=text,
                     summary=text[:60], emotion=None, importance=7.0)
    offset = get_settings().timezone_offset_hours
    return {"ok": True, "note_id": note.id, "emotion": emotion,
            "created_at": (note.ts + timedelta(hours=offset)).isoformat()}
