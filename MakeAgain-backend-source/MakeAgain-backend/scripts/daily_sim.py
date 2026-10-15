"""每日主题 + 启发文案 + 聊天演变 + 情绪日历 · 端到端模拟。

运行(在 backend 目录下):
    python scripts/daily_sim.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from agent import companion, daily
from gateway.client import LLMClient
from memory import calendar, reflect, store
from memory.async_write import flush_memory_writes
from memory.db import SessionLocal, init_db
from memory.models import DailyPick, EmotionNode, MemoryEntry
from sqlalchemy import delete

UID = "sim-daily-linwei"


def add_backdated(db, days_ago: int, summary: str, emotion: dict, time_tag: str | None) -> None:
    ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
    db.add(MemoryEntry(user_id=UID, type="chat", content=summary, summary=summary,
                       facts=[], emotion=emotion, importance=5.0, time_tag=time_tag, ts=ts))


def node_map(db) -> dict:
    # 节点唯一标识为 (trigger, emotion),同 trigger 可对应多个情绪
    return {(n.trigger, n.emotion): n.frequency for n in store.list_emotion_nodes(db, UID)}


def seed(db) -> None:
    store.get_or_create_user(db, UID, loss_type="breakup")
    store.upsert_portrait(db, UID, "user", {"昵称": "林薇", "loss_type": "breakup"})
    store.upsert_portrait(db, UID, "object", {"称呼": "他", "关系": "初恋四年"})

    # 过去 6 天的心情(score 越高心情越好),含时间标签
    days = [
        (6, "他走后我整晚睡不着,一直刷手机", {"emotion": "难过", "score": 32, "valence": -0.6, "arousal": 0.7}, "晚上"),
        (5, "周六以前是我们的约会日,现在一个人不知道去哪", {"emotion": "想念", "score": 40, "valence": -0.4, "arousal": 0.6}, "周六"),
        (4, "早上醒来习惯性想发早安,才发现已经删了联系方式", {"emotion": "孤独", "score": 38, "valence": -0.5, "arousal": 0.5}, "早上"),
        (3, "晚上又想起他说的那些话,有点想哭", {"emotion": "想念", "score": 42, "valence": -0.4, "arousal": 0.6}, "晚上"),
        (2, "今天好好吃了一顿饭,感觉平静了一些", {"emotion": "平静", "score": 60, "valence": 0.2, "arousal": 0.3}, None),
        (1, "试着把合照收进抽屉了,好像没那么痛了", {"emotion": "释怀", "score": 66, "valence": 0.4, "arousal": 0.3}, None),
    ]
    for days_ago, summary, emotion, time_tag in days:
        add_backdated(db, days_ago, summary, emotion, time_tag)
    db.commit()
    # 从记忆流重建情绪节点,供个性化推荐使用
    reflect.reflect(db, UID)


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()

    for model in (MemoryEntry, EmotionNode, DailyPick):
        db.execute(delete(model).where(model.user_id == UID))
    db.commit()
    seed(db)

    print("=" * 64)
    print("每日主题 + 启发文案 + 聊天演变 + 情绪日历 · 模拟(林薇·分手)")
    print("=" * 64)

    # 1. 个性化主题推荐
    r1 = daily.get_themes(db, UID)
    print(f"\n【今日主题推荐】{r1['reason']}")
    for t in r1["themes"]:
        print(f"  - [{t['key']}] {t['title']} —— {t['desc']}")

    # 2. 今日总启发文案(依据心情,不绑定主题)
    r2 = daily.generate_opening(db, UID)
    print(f"\n【今日启发文案 · 心情:{r2['mood'] or '未定'}】")
    print(f"  {r2['opening']}")

    # 3. 一句倾诉 → 后台抽取/打分/落库/节点演变
    before_mem = len(store.list_memories(db, UID))
    before_nodes = node_map(db)
    msg = "晚上路过那家咖啡店,又想起他以前总在那里等我"
    r3 = companion.chat(db, UID, msg, client)
    flush_memory_writes()  # 等待后台回写完成
    after_mem = len(store.list_memories(db, UID))
    after_nodes = node_map(db)
    print(f"\n【聊天演变】回复: {r3['reply']}")
    print(f"  记忆条数: {before_mem} → {after_mem}")
    print(f"  情绪节点演变(trigger/emotion: frequency):")
    all_keys = set(before_nodes) | set(after_nodes)
    for trig, emo in sorted(all_keys, key=lambda k: -(after_nodes.get(k, 0))):
        b = before_nodes.get((trig, emo), 0)
        a = after_nodes.get((trig, emo), 0)
        print(f"    {trig}/{emo}: {b} → {a}")

    # 4. 情绪日历
    cal = calendar.get_calendar(db, UID)
    print(f"\n【情绪日历 · {cal['month']}】")
    for d in cal["days"]:
        if d["score"] is None:
            print(f"  {d['date']}  (空)")
            continue
        bar = "█" * int(round(d["score"] / 10))
        print(f"  {d['date']}  {d['emotion']:<3} score={d['score']:<5} n={d['count']}  {bar}")

    db.close()
    print("\n" + "=" * 64)
    print("模拟结束。观察点:主题个性化、启发文案贴合记忆、聊天后记忆与情绪节点演变、日历趋势。")


if __name__ == "__main__":
    main()
