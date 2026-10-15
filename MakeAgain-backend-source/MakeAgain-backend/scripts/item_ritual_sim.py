"""聊天「物品纪念/寄存」工具端到端模拟(纯 mock,无网络)。

验证:
1. 工具循环:keep 未讲过 → tool=item_keep;keep 已讲过 → tool=None;let_go → item_let_go;无物品 → tool=None
2. has_item_story 去重(精确/包含/记忆流兜底)
3. 上传链路:识别 + 抠图 + 描述简化 + 落库(ItemMemory + 记忆流)

运行(在 backend 目录下):
    python scripts/item_ritual_sim.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent import companion, item as item_mod
from config import Settings
from gateway.client import LLMClient
from memory import store
from memory.async_write import flush_memory_writes
from memory.db import Base
from memory.models import utcnow

# 临时文件库(跨线程共享,避免 :memory: 每个连接各建一个空库)
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
engine = create_engine(f"sqlite:///{_tmp.name.replace(chr(92), '/')}",
                       connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# 让后台记忆回写走同一份临时库 + mock,避免污染 data/app.db / 走真实网络
import memory.async_write as _aw
_aw.SessionLocal = S
_aw.LLMClient = lambda: LLMClient(settings=Settings(mock_llm=True))

client = LLMClient(settings=Settings(mock_llm=True))

db = S()
ok = True


def check(name, cond):
    global ok
    mark = "✓" if cond else "✗ 失败"
    print(f"  [{mark}] {name}")
    if not cond:
        ok = False


def seed_user(uid, loss_type="breakup"):
    store.get_or_create_user(db, uid, loss_type=loss_type)
    for i in range(3):
        e = store.add_memory(db, uid, content=f"第{i}天的倾诉", summary=f"第{i}天的倾诉",
                             emotion={"score": 58, "emotion": "平静"})
        e.ts = utcnow() - timedelta(days=i)
        db.commit()


def tool_call(name, arguments):
    return {"id": "call_1", "type": "function",
            "function": {"name": name, "arguments": arguments}}


class FakeClient:
    """按预设脚本逐次返回 chat 结果(首轮可能带 tool_calls,次轮给最终文案)。"""

    def __init__(self, script):
        self.script = list(script)
        self.settings = Settings(mock_llm=True)
        self.mock = True

    def chat(self, messages, temperature=0.7, max_tokens=None, model=None, tools=None):
        return self.script.pop(0) if self.script else {"content": "", "usage": {}, "tool_calls": []}


print("=" * 64)
print("1. 工具循环:纪念 / 寄存 / 已讲过 / 无物品")
print("=" * 64)

seed_user("ITEM-KEEP")
fake = FakeClient([
    {"content": "", "usage": {}, "tool_calls": [tool_call("suggest_item_ritual", '{"item_name":"那条手链","intent":"keep"}')]},
    {"content": "这条手链对你一定很重要吧?可以把它拍下来传给我,讲讲它的故事。", "usage": {}, "tool_calls": []},
])
r = companion.chat(db, "ITEM-KEEP", "我一直留着那条手链", fake)
flush_memory_writes()
t = r.get("tool") or {}
check("keep 未讲过 → tool.type=item_keep", t.get("type") == "item_keep")
check("keep 未讲过 → upload=True", t.get("upload") is True)
check("keep 未讲过 → copy 非空", bool(t.get("copy")))
check("回复为第二轮个性化文案", r["reply"] == "这条手链对你一定很重要吧?可以把它拍下来传给我,讲讲它的故事。")

# 已讲过(先落一条 ItemMemory)
seed_user("ITEM-KEEP2")
store.add_item_memory(db, "ITEM-KEEP2", item_name="手链", intent="keep", description="他送我的手链",
                      label=None, original_key="item/a.png", cutout_key="item/a_cutout.png")
fake2 = FakeClient([
    {"content": "", "usage": {}, "tool_calls": [tool_call("suggest_item_ritual", '{"item_name":"那条手链","intent":"keep"}')]},
    {"content": "我记得你之前跟我说过这条手链,它还在你心里占着一个位置。", "usage": {}, "tool_calls": []},
])
r2 = companion.chat(db, "ITEM-KEEP2", "我又想起那条手链了", fake2)
flush_memory_writes()
check("keep 已讲过 → tool=None", r2.get("tool") is None)
check("已讲过 → 回复无「上传/拍下来」", "拍下来" not in r2["reply"] and "上传" not in r2["reply"])

seed_user("ITEM-GO")
fake3 = FakeClient([
    {"content": "", "usage": {}, "tool_calls": [tool_call("suggest_item_ritual", '{"item_name":"他的旧手机","intent":"let_go"}')]},
    {"content": "如果看到它就难受,不妨把它寄存在我这里,慢慢松开手。", "usage": {}, "tool_calls": []},
])
r3 = companion.chat(db, "ITEM-GO", "看到他的旧手机我就难受", fake3)
flush_memory_writes()
t3 = r3.get("tool") or {}
check("let_go → tool.type=item_let_go", t3.get("type") == "item_let_go")

seed_user("ITEM-NONE")
fake4 = FakeClient([
    {"content": "嗯,我在听,你慢慢说。", "usage": {}, "tool_calls": []},
])
r4 = companion.chat(db, "ITEM-NONE", "今天有点累", fake4)
flush_memory_writes()
check("无物品提及 → tool=None", r4.get("tool") is None)

print()
print("=" * 64)
print("2. has_item_story 去重")
print("=" * 64)
seed_user("ITEM-DUP")
store.add_item_memory(db, "ITEM-DUP", item_name="他送的围巾", intent="keep", description="",
                      label=None, original_key="item/x.png", cutout_key="item/x_cutout.png")
check("互相包含命中(那条→围巾)", store.has_item_story(db, "ITEM-DUP", "那条围巾"))
check("含前缀也命中(他送的围巾)", store.has_item_story(db, "ITEM-DUP", "他送的围巾"))
check("不相关物品不命中", not store.has_item_story(db, "ITEM-DUP", "旧钱包"))
store.add_memory(db, "ITEM-DUP", content="我一直留着那支钢笔,那是毕业礼物", summary="留着那支钢笔", emotion=None)
check("记忆流兜底命中(钢笔)", store.has_item_story(db, "ITEM-DUP", "那支钢笔"))
check("空名称不误判", not store.has_item_story(db, "ITEM-DUP", "   "))

print()
print("=" * 64)
print("3. 上传链路:识别 + 抠图 + 描述简化 + 落库")
print("=" * 64)


class FakeVision:
    def recognize(self, image):
        return "手链"

    def ground(self, image_url, description):
        return {"label": None, "bbox": None}

    def verify(self, image_url, description):
        return {"match": True, "reason": ""}


class FakeObjStore:
    backend = "local"

    def __init__(self):
        self.deleted: list[str] = []

    def upload(self, key, data, content_type="", prefix=None):
        return f"item/{key}"

    def presigned_url(self, full_key):
        return f"local://{full_key}"

    def ai_matte(self, full_key):
        return b"PNG-CUTOUT-BYTES"

    def delete(self, full_key):
        self.deleted.append(full_key)


long_desc = "这是那条他送我的手链,我们在一起第二年他生日那天,他亲手给我戴上的,那天下着小雨。" * 3
seed_user("ITEM-UP")
up = item_mod.handle_upload(db, "ITEM-UP", b"IMAGE-BYTES", "", "keep", long_desc,
                            client, vision=FakeVision(), obj_store=FakeObjStore())
check("识别标签补全 item_name", up["item_name"] == "手链")
check("label 已存", up["label"] == "手链")
check("描述已简化(≤80字)", len(up["description"]) <= 80)
check("校验通过 ok/match=True", up.get("ok") is True and up.get("match") is True)
check("不返回 original_url", "original_url" not in up)
check("cutout_url 存在", bool(up.get("cutout_url")))
row = store.get_item_memory(db, up["item_id"])
check("ItemMemory 落库且字段完整",
      row is not None and row.intent == "keep"
      and row.original_key == "" and row.cutout_key.endswith("_cutout.png"))
item_mems = [m for m in store.list_memories(db, "ITEM-UP") if m.type == "item"]
check("记忆流新增 type=item 条目", len(item_mems) == 1)

print()
print("=" * 64)
print("结论:", "全部通过" if ok else "存在失败项")
print("=" * 64)
db.close()
engine.dispose()
try:
    os.remove(_tmp.name)
except OSError:
    pass
sys.exit(0 if ok else 1)
