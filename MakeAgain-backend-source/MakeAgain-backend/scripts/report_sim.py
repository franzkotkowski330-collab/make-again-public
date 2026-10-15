"""定期跟踪报告模拟(纯 mock,无网络):两个不同阶段的用户,展示"报告因人而异"。"""
import os
import sys
from datetime import timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent import report
from config import Settings
from gateway.client import LLMClient
from memory import store
from memory.db import Base
from memory.models import utcnow

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
client = LLMClient(settings=Settings(mock_llm=True))


def add_mem(db, uid, content, score, emo, days_ago, time_tag=None, place_tag=None):
    e = store.add_memory(db, uid, content=content, summary=content,
                         emotion={"score": score, "emotion": emo},
                         time_tag=time_tag, place_tag=place_tag)
    e.ts = utcnow() - timedelta(days=days_ago)
    db.commit()


db = S()

# ---- 用户 LOW:分手,持续低迷(低谷期) ----
store.get_or_create_user(db, "LOW", loss_type="breakup")
store.upsert_portrait(db, "LOW", "user", {"昵称": "阿哲", "loss_type": "breakup"})
store.upsert_portrait(db, "LOW", "object", {"称呼": "小夏", "性格": "温柔", "关系": "前任"})
seq = [("难过", 35), ("想念", 40), ("焦虑", 38), ("难过", 32), ("想念", 42)]
for i in range(20):
    emo, sc = seq[i % len(seq)]
    add_mem(db, "LOW", f"第{i}天,又想起小夏", sc, emo, days_ago=i,
            time_tag="晚上" if i % 2 == 0 else "周六", place_tag="家里" if i % 3 == 0 else None)

# ---- 用户 HEALED:亲友离世,先低落后来转平静/释怀(和解期) ----
store.get_or_create_user(db, "HEALED", loss_type="loved_one", goal="carry_on")
store.upsert_portrait(db, "HEALED", "user", {"昵称": "林薇", "loss_type": "loved_one"})
store.upsert_portrait(db, "HEALED", "object", {"称呼": "奶奶", "性格": "慈祥", "关系": "奶奶"})
for i in range(18):
    if i < 10:
        emo, sc = ("想念", 38 + i % 3) if i % 2 == 0 else ("难过", 40)
    else:
        emo, sc = ("平静", 62 + i % 5) if i % 2 == 0 else ("释怀", 68)
    add_mem(db, "HEALED", f"第{i}天,关于奶奶", sc, emo, days_ago=17 - i,
            time_tag="下午", place_tag="家里")
store.upsert_daily_pick(db, "HEALED", "2026-08-20", "gratitude", "感恩", "……")
store.upsert_daily_pick(db, "HEALED", "2026-08-21", "gratitude", "感恩", "……")
letter = store.add_letter(db, "HEALED", content="奶奶走后总觉得空", summary="奶奶走后总觉得空",
                          loss_type="loved_one", emotion="想念", tags=["病逝"])
reply = store.add_reply(db, letter.id, "HEALED", "给相似经历的人的回信")
store.update_reply_status(db, reply.id, "delivered")


def show(uid):
    print(f"\n===== 用户 {uid} =====")
    print("资格:", report.report_eligibility(db, uid))
    r = report.build_report(db, uid, client)
    st = r["state"]
    print(f"状态: {st['stage_label']} | 均分 {st['baseline']} | 趋势 {st['trend']} | "
          f"平静占比 {st['calm_ratio']} | 和解度 {st['reconcile']}")
    print("卡片:")
    for c in r["cards"]:
        print(f"  · [{c['line']}] {c['text']}")
    print("对比上次:", r["compared"])


show("LOW")
show("HEALED")
