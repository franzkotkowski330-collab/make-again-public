"""软引导提醒测试(F2-1):验证情绪节点触发 + 时间触发 + 去重 + 夜间去冗余。

运行(在 backend 目录下):
    python scripts/nudge_test.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from agent import nudge
from gateway.client import LLMClient
from memory import store
from memory.db import SessionLocal, init_db
from memory.models import EmotionNode, MemoryEntry, NudgeLog
from sqlalchemy import delete

UID = "demo-nudge"
UID2 = "demo-nudge-night"


def seed(db) -> None:
    # UID:反复在"周六晚上"陷入情绪的用户节点 + 一条周六晚上的真实记忆
    db.add(EmotionNode(user_id=UID, trigger="晚上", emotion="孤独", intensity=0.7,
                       frequency=4, place=None, time_tag="晚上"))
    db.add(EmotionNode(user_id=UID, trigger="周六", emotion="想念", intensity=0.6,
                       frequency=3, place=None, time_tag="周六"))
    store.add_memory(db, UID, type="chat", content="以前每周六晚上都会和他打电话,现在电话再也不会响了。",
                     summary="以前每周六晚上都会和他打电话,现在电话再也不会响了",
                     time_tag="晚上")

    # UID2:只有"周六"节点,没有夜间节点 → 用于验证深夜提醒独立触发
    db.add(EmotionNode(user_id=UID2, trigger="周六", emotion="想念", intensity=0.5,
                       frequency=3, place=None, time_tag="周六"))
    db.commit()


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()

    for model in (EmotionNode, MemoryEntry, NudgeLog):
        db.execute(delete(model).where(model.user_id.in_([UID, UID2])))
    db.commit()
    seed(db)

    # 场景1:周六晚上 23:30 → 晚上 + 周六(2 条),夜间不再单独提醒
    sat_night = datetime(2026, 8, 29, 15, 30)  # UTC 15:30 = 东八区 23:30,周六
    r1 = nudge.get_nudges(db, UID, client, now=sat_night)
    print(f"【周六 23:30 · 有夜间节点】{r1['now']} 共 {len(r1['nudges'])} 条(应为 2:晚上+周六,无 late_night)")
    for n in r1["nudges"]:
        print(f"  - [{n['rule_key']}] {n['text']}")

    # 场景2:同一时刻再次拉取 → 去重,返回空
    r2 = nudge.get_nudges(db, UID, client, now=sat_night)
    print(f"\n【再次拉取同一时刻】条数={len(r2['nudges'])} (应为 0,去重生效)")

    # 场景3:周二下午 15:00 → 无节点命中,非深夜 → 空
    tue_noon = datetime(2026, 8, 25, 7, 0)  # UTC 07:00 = 东八区 15:00,周二
    r3 = nudge.get_nudges(db, UID, client, now=tue_noon)
    print(f"\n【周二 15:00】条数={len(r3['nudges'])} (应为 0)")

    # 场景4:UID2 周六 23:30,无夜间节点 → 深夜提醒 + 周六(2 条)
    r4 = nudge.get_nudges(db, UID2, client, now=sat_night)
    print(f"\n【周六 23:30 · 无夜间节点】条数={len(r4['nudges'])} (应为 2:late_night+周六)")
    for n in r4["nudges"]:
        print(f"  - [{n['rule_key']}] {n['text']}")

    db.close()


if __name__ == "__main__":
    main()
