"""真实模型端到端验证:聊天工具调用「物品纪念/寄存」(走真实 DeepSeek 网络)。

验证 companion.chat 的多轮 tool-calling 在真实模型下是否端到端跑通:
1. keep   :提到想留作纪念的物品 → 期望模型吐出 suggest_item_ritual(intent=keep),
           回复 tool.type == "item_keep"、upload=True、copy 非空
2. let_go :提到看到会难过、想放下的物品 → 期望 tool.type == "item_let_go"
3. 已讲过 :先种一条 ItemMemory → 期望 tool is None,且回复不再邀请上传

真实模型有随机性,本脚本用「软断言」:逐场景打印模型实际行为 + 期望,
最后统计「命中期望 / 场景数」并据此退出(命中不足时非零,便于 CI 感知)。

运行(需真实网络):
    python scripts/item_ritual_real.py
"""
from __future__ import annotations

import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent import companion
from gateway.client import LLMClient
from memory import store
from memory.async_write import flush_memory_writes
from memory.db import Base

# 临时文件库(跨线程共享),不污染 data/app.db;后台回写同样走真实模型
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
engine = create_engine(f"sqlite:///{_tmp.name.replace(chr(92), '/')}",
                       connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

import memory.async_write as _aw
_aw.SessionLocal = S

client = LLMClient()  # 真实模型(读 .env,含 key)
db = S()

hit = 0
total = 0


def scenario(name: str, uid: str, msg: str, expected: str) -> None:
    """expected: 'item_keep' | 'item_let_go' | 'none'"""
    global hit, total
    total += 1
    r = companion.chat(db, uid, msg, client)
    flush_memory_writes()
    t = r.get("tool") or {}
    ttype = t.get("type")

    if expected == "none":
        ok = ttype is None
    else:
        ok = ttype == expected
    if ok:
        hit += 1

    print("=" * 64)
    print(f"【{name}】期望 tool={expected}")
    print("=" * 64)
    print(f"用户: {msg}")
    print(f"AI  : {r['reply']}")
    print(f"tool: {ttype!r}  upload={t.get('upload')!r}  item_name={t.get('item_name')!r}")
    print(f"      判定: {'✓ 命中' if ok else '✗ 未命中(真实模型随机,可重跑)'}\n")


def seed(uid: str, loss_type: str = "breakup") -> None:
    store.get_or_create_user(db, uid, loss_type=loss_type)


print("=" * 64)
print("真实 tool-calling 端到端验证(DeepSeek · 需网络)")
print(f"  模型: {client.settings.llm_fast_model} | mock={client.mock}")
print("=" * 64 + "\n")

# 1. keep
seed("REAL-KEEP")
scenario("1 · keep 留作纪念", "REAL-KEEP",
         "我一直留着那条手链,舍不得丢掉,那是他送我的。", "item_keep")

# 2. let_go
seed("REAL-GO")
scenario("2 · let_go 想放下", "REAL-GO",
         "看到他的旧手机我就一阵难受,很想丢掉又舍不得。", "item_let_go")

# 3. 已讲过
seed("REAL-DUP")
store.add_item_memory(db, "REAL-DUP", item_name="手链", intent="keep",
                      description="他送我的手链", label=None,
                      original_key="item/x.png", cutout_key="item/x_cutout.png")
db.commit()
scenario("3 · 已讲过不再问", "REAL-DUP",
         "我又想起那条手链了,心里还是放不下。", "none")

print("=" * 64)
print(f"命中 {hit}/{total}")
print("=" * 64)
db.close()
engine.dispose()
try:
    os.remove(_tmp.name)
except OSError:
    pass
sys.exit(0 if hit == total else 1)
