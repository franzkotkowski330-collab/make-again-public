"""树洞信箱(F3)全功能离线端到端测试(纯 mock,零网络,不污染 data/app.db)。

覆盖:
- 写信资格(聊天会话门槛 / 已写过)
- 写信(正常 / PII 拦截 / 二次写被拒)
- 回信资格(归档门槛 / 天数不足 / 近 7 天倾诉少 / 均分不足 / 近期急性)
- 匹配(相似来信)
- 回信(正常 / PII 拦截 / 回复自己的信)
- 审核(待审队列 → 通过)
- 弹窗聚合(写信 / 回信邀请 / 收到回信,各只弹一次)
- 看板(我写的树洞信 + 收到的回信 / 我写给他人的回信)
- 运营后台(全部来信 / 官方回信直达)

运行(在 backend 目录下):
    python scripts/treehole_sim.py
"""
import os
import sys
from datetime import timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from agent import treehole  # noqa: E402
from config import Settings  # noqa: E402
from gateway.client import LLMClient  # noqa: E402
from memory import store  # noqa: E402
from memory.db import Base  # noqa: E402
from memory.models import utcnow  # noqa: E402

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
client = LLMClient(settings=Settings(mock_llm=True))


def add_mem(db, uid, content, score, emo, days_ago):
    e = store.add_memory(db, uid, content=content, summary=content,
                         emotion={"score": score, "emotion": emo})
    e.ts = utcnow() - timedelta(days=days_ago)
    db.commit()


def seed_chat(db, uid, n):
    """造 n 个聊天会话(每次进入聊天 = 一个 session_id),用于写信门槛。"""
    for i in range(n):
        store.save_chat_turn(db, uid, f"session-{uid}-{i}", f"第 {i} 次进来聊聊", "我在听")


def archive_user(db, uid):
    """归档当前陪伴(回信资格前置:已结束这段陪伴)。"""
    c = store.get_or_create_current_companion(db, uid)
    store.archive_companion(db, c.id, "dissolved")


def line(t):
    print("\n" + "=" * 6 + " " + t + " " + "=" * 6)


# ---- 用户 A:写信者(亲友离世),已聊 2 次 ----
db = S()
store.get_or_create_user(db, "A", loss_type="loved_one")

line("1. 写信资格")
print("无聊天会话(应不可写):", treehole.write_eligibility(db, "A")["eligible"])
seed_chat(db, "A", 2)
print("聊 2 次后(应可写):", treehole.write_eligibility(db, "A"))

line("2. 写信")
r = treehole.write_letter(db, "A", "奶奶走后,总觉得心里空了一块,很多话没来得及说。", client)
print("正常写信:", r)
letter_a = r["letter_id"]
print("再写一封(应被拒):", treehole.write_letter(db, "A", "又想说点什么", client))

line("3. PII 拦截")
db_pii = S()
store.get_or_create_user(db_pii, "PII", loss_type="breakup")
seed_chat(db_pii, "PII", 2)
print("含手机号(应被拦):", treehole.write_letter(db_pii, "PII", "我电话 13800138000,想聊聊", client))

# ---- 用户 B:回信者(亲友离世),已归档 + 情绪趋稳 ----
dbB = S()
store.get_or_create_user(dbB, "B", loss_type="loved_one")
for i in range(8):
    emo = "平静" if i <= 2 else "释怀"
    add_mem(dbB, "B", f"第{i}天,慢慢接受了", 55 + i * 2, emo, days_ago=i)

line("4. 回信资格")
print("未结束陪伴(应 ended=False 且不可回信):", treehole.reply_eligibility(dbB, "B"))
archive_user(dbB, "B")
print("已归档 + 情绪趋稳(应 eligible):", treehole.reply_eligibility(dbB, "B"))

db_low = S()
store.get_or_create_user(db_low, "LO", loss_type="loved_one")
for i in range(8):
    add_mem(db_low, "LO", f"{i}天前", 45, "平静", days_ago=i)
archive_user(db_low, "LO")
print("已归档但均分不足(应 not eligible):", treehole.reply_eligibility(db_low, "LO"))

db_acute = S()
store.get_or_create_user(db_acute, "AC", loss_type="loved_one")
add_mem(db_acute, "AC", "今天很难过", 40, "难过", days_ago=0)
add_mem(db_acute, "AC", "昨天也难受", 42, "难过", days_ago=1)
add_mem(db_acute, "AC", "前天崩溃了", 45, "难过", days_ago=2)
for i in range(3, 8):
    add_mem(db_acute, "AC", f"{i}天前平静些", 60, "平静", days_ago=i)
archive_user(db_acute, "AC")
print("已归档但近期急性(应 not eligible):", treehole.reply_eligibility(db_acute, "AC"))

db_sparse = S()
store.get_or_create_user(db_sparse, "SP", loss_type="loved_one")
add_mem(db_sparse, "SP", "今天来写一句", 60, "平静", days_ago=0)
for i in range(10, 16):
    add_mem(db_sparse, "SP", f"{i}天前的记录", 60, "平静", days_ago=i)
archive_user(db_sparse, "SP")
print("已归档但近 7 天倾诉少(应 not eligible):", treehole.reply_eligibility(db_sparse, "SP"))

line("5. 匹配")
m = treehole.get_matches(dbB, "B", client)
for x in m["matches"]:
    print("  -", x["letter_id"], x["loss_type"], x["emotion"], "|", x["summary"])

line("6. 回信 + 审核")
rp = treehole.submit_reply(dbB, "B", letter_a, "我也失去过很重要的人,慢慢来,你没有被忘记。", client)
print("B 回信 A(应 ok):", rp)
print("回信含微信(应被拦):", treehole.submit_reply(dbB, "B", letter_a, "加我微信 abc123 详聊", client))
print("回复自己的信(应被拒):", treehole.submit_reply(db, "A", letter_a, "给自己的信", client))
print("待审队列:", treehole.review_pending(dbB))
rid = rp["reply_id"]
print("审批通过:", treehole.approve_reply(dbB, rid))
print("审批后待审剩余:", len(treehole.review_pending(dbB)))

line("7. 弹窗聚合(popups)")
print("A 的弹窗(应只含 reply_received):")
for p in treehole.popups(db, "A", client)["popups"]:
    print("  -", p["kind"], "| reply_id:", p["data"].get("reply_id"))
print("B 的弹窗(应含 reply_invite):")
for p in treehole.popups(dbB, "B", client)["popups"]:
    print("  -", p["kind"], "| matches:", len(p["data"].get("matches", [])))
print("标记 A 的 reply_received 已看:", treehole.mark_popup_seen(db, "A", "reply_received", rid))
print("A 再看弹窗(应空):", treehole.popups(db, "A", client)["popups"])

line("8. 看板")
print("A 的树洞信(letters,应含 B 的回信):")
for L in treehole.my_letters(db, "A"):
    print("  - 信", L["letter_id"], "| 回信:", [(x["reply_id"], x["source"]) for x in L["replies"]])
print("B 的回信(replies,应 delivered):")
for x in treehole.my_replies(dbB, "B"):
    print("  -", x["reply_id"], x["status"], "| 对应信:", (x["letter"] or {}).get("summary"))

line("9. 运营后台")
print("全部来信:", treehole.admin_letters(db))
ar = treehole.admin_reply(db, letter_a, "运营的官方回信:我们一直在这里。", client)
print("官方回信(应直达 delivered):", ar)
print("A 的来信看板(应多一封官方回信):")
for L in treehole.my_letters(db, "A"):
    print("  - 信", L["letter_id"], "| 回信:", [(x["reply_id"], x["source"]) for x in L["replies"]])

print("\n树洞信箱全功能离线测试结束。")
