"""F2-1 软引导提醒 · 模拟测试:给一个真实分手用户,沿一周时间轴观察触发与文案。

运行(在 backend 目录下):
    python scripts/nudge_sim.py

说明:
- 造一个"林薇,初恋 4 年分手"的用户,埋好情绪节点 + 真实记忆。
- 按本地时间轴逐刻拉取 get_nudges,观察:
  1) 哪些时刻触发哪类软引导; 2) 文案是否贴合真实记忆、是否重复;
  3) 同一天内同一规则只提醒一次(去重); 4) 深夜提醒是否与"晚上"节点重复。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

# Windows 控制台默认 GBK,强制 UTF-8 避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from agent import nudge
from gateway.client import LLMClient
from memory import store
from memory.db import SessionLocal, init_db
from memory.models import EmotionNode, MemoryEntry, NudgeLog
from sqlalchemy import delete

UID = "sim-linwei"
TZ = timedelta(hours=8)


def seed(db) -> None:
    """林薇:初恋 4 年分手,记忆集中在晚上/周六/周日/早上/下午。"""
    nodes = [
        ("晚上", "孤独", 6, "晚上"),
        ("周六", "想念", 5, "周六"),
        ("周日", "想念", 4, "周日"),
        ("早上", "空落", 3, "早上"),
        ("下午", "难过", 3, "下午"),
    ]
    for trigger, emotion, freq, time_tag in nodes:
        db.add(EmotionNode(user_id=UID, trigger=trigger, emotion=emotion,
                           intensity=0.7, frequency=freq, place=None, time_tag=time_tag))

    memories = [
        ("以前每周六晚上都会和他打电话,现在手机安静得让人心慌", "晚上"),
        ("周六以前是约会日,现在一个人不知道去哪", "周六"),
        ("早上醒来习惯性想发早安,才发现已经删了联系方式", "早上"),
        ("下午路过以前常去的咖啡店,再也不敢进去", "下午"),
        ("周日以前一起做早餐,现在厨房冷清得可怕", "周日"),
    ]
    for summary, time_tag in memories:
        store.add_memory(db, UID, type="chat", content=summary, summary=summary, time_tag=time_tag)
    db.commit()


def L(y, mo, d, h, mi=0) -> datetime:
    """本地时间 → UTC(供 get_nudges 的 now 参数)。"""
    return datetime(y, mo, d, h, mi) - TZ


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()

    for model in (EmotionNode, MemoryEntry, NudgeLog):
        db.execute(delete(model).where(model.user_id == UID))
    db.commit()
    seed(db)

    print("=" * 60)
    print("F2-1 软引导提醒 · 模拟:林薇(初恋 4 年分手)")
    print("埋点:晚上(孤独·6)/ 周六(想念·5)/ 周日(想念·4)/ 早上(空落·3)/ 下午(难过·3)")
    print("=" * 60)

    # (本地时刻, 说明)
    timeline = [
        (L(2026, 8, 29, 9, 0), "周六 09:00 醒来"),
        (L(2026, 8, 29, 15, 0), "周六 15:00 午后"),
        (L(2026, 8, 29, 22, 0), "周六 22:00 夜晚"),
        (L(2026, 8, 29, 23, 30), "周六 23:30 深夜(应去重,且不与'晚上'重复)"),
        (L(2026, 8, 30, 9, 0), "周日 09:00 醒来"),
        (L(2026, 8, 30, 22, 0), "周日 22:00 夜晚"),
        (L(2026, 8, 31, 15, 0), "周一 15:00 工作日下午"),
        (L(2026, 8, 31, 22, 30), "周一 22:30 夜晚"),
    ]

    day = None
    for now, label in timeline:
        local = now + TZ
        d = local.strftime("%m-%d")
        if d != day:
            day = d
            print(f"\n── {local.strftime('%m-%d %a')} ──")
        r = nudge.get_nudges(db, UID, client, now=now)
        if r["nudges"]:
            for n in r["nudges"]:
                print(f"  [{label}] ({n['rule_key']}) {n['text']}")
        else:
            print(f"  [{label}] (无提醒)")

    db.close()
    print("\n" + "=" * 60)
    print("模拟结束。观察点:文案是否贴合记忆、是否重复、去重与深夜去冗余是否生效。")


if __name__ == "__main__":
    main()
