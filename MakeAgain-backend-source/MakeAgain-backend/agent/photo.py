"""场景照片工具:聊天里的「拍立得」(第二个 tool calling 能力)。

与物品工具不同,这里不做定位/裁切/抠图——用户提到某张具体照片/想记住的场景时,
Wakey 邀请上传,整张照片原样保留,变成看板上带日期的拍立得。
"""
from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

from config import get_settings
from gateway.storage import ObjectStore
from memory import store

# 工具 schema:OpenAI function calling 格式
PHOTO_UPLOAD_TOOL = {
    "type": "function",
    "function": {
        "name": "suggest_photo_upload",
        "description": (
            "当用户提到某张具体的照片/合照、某个想记住的场景画面或地点时直接调用(不要在正文问"
            "「要不要上传」):邀请用户上传这张照片,做成一张拍立得留在看板。"
            "注意:只用于「照片/场景/地点」;若用户提到的是实体物品(毛衣/围巾/手链/旧物等),"
            "用 suggest_item_ritual,不要用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "photo_title": {"type": "string",
                                "description": "照片标题/场景,如「去年冬天在车站」「和他一起看的那场海」"},
                "scene_description": {"type": "string",
                                     "description": "照片背后的场景或故事片段,可为空"},
            },
            "required": ["photo_title"],
        },
    },
}

_DIRECTION = "只邀请用户选择照片；从现有对话整理场景和故事，不重复索要、不编造经历，确认保存前不能说已保存。"

# 描述长度上限:场景描述过长时硬截断(不上 LLM,保持上传路径快且稳)
_MAX_DESC_LEN = 200

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def execute_tool(db, user_id: str, tool_call: dict) -> dict:
    """执行工具(纯本地、零 LLM):去重判断 + 文案方向。"""
    args = tool_call.get("function", {}).get("arguments") or tool_call.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    photo_title = (args.get("photo_title") or "").strip()
    scene_desc = (args.get("scene_description") or "").strip()

    # 已上传过这张照片 → 不再邀请
    if store.has_photo(db, user_id, photo_title):
        return {
            "surface": False,
            "reason": f"用户之前已经上传过「{photo_title}」,不要再重复邀请上传,"
                      f"可温柔回应「我记得你之前放过这张照片…」",
        }

    return {
        "surface": True,
        "photo_title": photo_title,
        "scene_description": scene_desc,
        "direction": _DIRECTION,
        "note": "这张照片是第一次被提到;不要写「我记得你之前放过」或「你之前说过」,当作新信息回应。",
    }


def tool_payload(results: list[dict], reply: str) -> dict | None:
    """由工具执行结果组装返回给前端的结构化字段;无要展示的动作时返回 None。"""
    for r in results:
        if r.get("surface"):
            return {
                "type": "photo_upload",
                "photo_title": r.get("photo_title") or "",
                "scene_description": r.get("scene_description") or "",
                "upload": True,
                "copy": reply,
            }
    return None


def handle_upload(db, user_id: str, image_bytes: bytes, title: str, description: str,
                  content_type: str = "image/jpeg", obj_store: ObjectStore | None = None) -> dict:
    """照片上传核心:整图保留(无抠图) + 落库(PhotoMemory + 记忆流)。返回结构化结果。"""
    store.get_or_create_user(db, user_id)
    obj_store = obj_store or ObjectStore()

    title = (title or "").strip() or "这一刻"
    desc = (description or "").strip()
    if len(desc) > _MAX_DESC_LEN:
        desc = desc[:_MAX_DESC_LEN - 1] + "…"

    ext = _EXT_BY_TYPE.get((content_type or "").lower(), ".jpg")
    prefix = get_settings().cos_photo_prefix
    photo_key = obj_store.upload(f"{uuid4().hex}{ext}", image_bytes,
                                 content_type=content_type or "image/jpeg", prefix=prefix)

    row = store.add_photo_memory(db, user_id, title=title, description=desc, photo_key=photo_key)
    # 入记忆流供后续召回(emotion=None 不污染情绪指标)
    store.add_memory(db, user_id, type="photo", content=desc,
                     summary=f"照片「{title}」", emotion=None, importance=7.0)

    return {
        "ok": True,
        "photo_id": row.id,
        "title": title,
        "description": desc,
        "photo_url": obj_store.presigned_url(photo_key),
        "backend": obj_store.backend,
    }


def list_photos(db, user_id: str, obj_store: ObjectStore | None = None,
                companion_id: str | None = None) -> list[dict]:
    """看板:列出用户所有场景照片(整图预签名 URL + 描述),按时间倒序。"""
    store.get_or_create_user(db, user_id)
    obj_store = obj_store or ObjectStore()
    return [{
        "photo_id": r.id,
        "title": r.title,
        "description": r.description,
        "photo_url": obj_store.presigned_url(r.photo_key),
        "created_at": (r.ts + timedelta(hours=get_settings().timezone_offset_hours)).isoformat(),
    } for r in store.list_photo_memories(db, user_id, companion_id=companion_id)]
