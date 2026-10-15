"""核心模块冒烟测试:用真实分手故事跑通 写入→召回→反思→陪伴对话。

运行(在 backend 目录下):
    python scripts/smoke_test.py
"""
from __future__ import annotations

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from agent import companion
from emotion.classifier import classify
from gateway.client import LLMClient
from memory import extract, reflect as reflect_mod, store
from memory.db import SessionLocal, init_db
from memory.models import EmotionNode, MemoryEntry, Portrait
from sqlalchemy import delete

STORY = os.path.join(BACKEND, "..", "故事.txt")


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()  # 无 key 时自动 mock
    uid = "demo-cjr"
    store.get_or_create_user(db, uid, loss_type="breakup")

    # 幂等:清空该 demo 用户的历史数据,保证多次运行结果一致
    for model in (MemoryEntry, EmotionNode, Portrait):
        db.execute(delete(model).where(model.user_id == uid))
    db.commit()

    with open(STORY, encoding="utf-8") as f:
        chunks = [c.strip() for c in f.read().split("\n\n") if c.strip()][:6]

    print("== 写入记忆(抽取事实/情绪)==\n")
    for c in chunks:
        turn = extract.extract_turn(client, c)
        store.add_memory(
            db, uid, type="chat", content=c, summary=turn["summary"],
            facts=turn["facts"], emotion=turn["emotion"], importance=turn["importance"],
            place_tag=turn["place_tag"], time_tag=turn["time_tag"],
        )
        print(f"- 情绪={turn['emotion']['emotion']} score={turn['emotion']['score']} 摘要={turn['summary'][:40]}")

    print("\n== 反思/趋势 ==")
    r = reflect_mod.reflect(db, uid)
    for line in r["insights"]:
        print("洞察:", line)
    print("节点:", r["nodes"])

    print("\n== 陪伴对话 ==")
    resp = companion.chat(db, uid, "我最近总是想起她,尤其是晚上,很难受。", client)
    print("回复:", resp["reply"])
    print("本轮情绪:", resp["emotion"], "召回条数:", resp["recalled"])

    db.close()
    print("\n[OK] 核心链路跑通")


if __name__ == "__main__":
    main()
