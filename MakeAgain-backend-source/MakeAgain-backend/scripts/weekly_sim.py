"""周报(F4)全功能离线端到端测试(纯 mock,零网络,不污染 data/app.db)。

覆盖:
- 到期判断 due():引导未完成 / 数据不足 / 已归档 / 正常到期(后台生成)
- 后台生成 → 轮询 get() 拿到 {cards,state,compared}
- 已看标记 seen() → 本周不再弹
- 看板 list_reports() 按周倒序

运行(在 backend 目录下):
    python scripts/weekly_sim.py
"""
import os
import sys
from datetime import timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 关键:在导入任何后端模块之前强制 mock,否则后台生成会走真实 LLM(网络)。
os.environ["MOCK_LLM"] = "true"

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from agent import onboarding, weekly  # noqa: E402
from memory import store  # noqa: E402
from memory.db import Base  # noqa: E402
from memory.models import utcnow  # noqa: E402

# StaticPool:所有会话(含后台生成线程)共享同一个内存连接,避免每个线程拿到独立的空库。
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# 后台生成 _do() 内部用 weekly.SessionLocal 开独立会话;指向内存库,让生成结果落到测试库。
weekly.SessionLocal = S


def add_mem(db, uid, content, score, emo, days_ago):
    e = store.add_memory(db, uid, content=content, summary=content,
                         emotion={"score": score, "emotion": emo})
    e.ts = utcnow() - timedelta(days=days_ago)
    db.commit()


def seed_rich(db, uid, n_days=10):
    """造 n_days 天、每天 2 条情绪记忆,满足 report_min_days=7 / report_min_memories=15。"""
    for d in range(n_days):
        add_mem(db, uid, f"第{d}天,情绪慢慢平复下来", 55 + d, "平静", days_ago=d)
        add_mem(db, uid, f"第{d}天,偶尔还是会想起", 50 + d, "释怀", days_ago=d)


def line(t):
    print("\n" + "=" * 6 + " " + t + " " + "=" * 6)


# ---- 1. 引导未完成 ----
db_new = S()
store.get_or_create_user(db_new, "NEW", loss_type="breakup")
seed_rich(db_new, "NEW")
line("1. 引导未完成")
print("新用户 phase=new(应 due=False):", weekly.due(db_new, "NEW"))

# ---- 2. 数据不足 ----
db_thin = S()
store.get_or_create_user(db_thin, "THIN", loss_type="breakup")
onboarding.set_phase(db_thin, "THIN", "main")
add_mem(db_thin, "THIN", "今天来写一句", 60, "平静", days_ago=0)
line("2. 数据不足")
print("phase=main 但仅 1 条记忆(应 due=False):", weekly.due(db_thin, "THIN"))

# ---- 3. 已归档 ----
db_arc = S()
store.get_or_create_user(db_arc, "ARC", loss_type="loved_one")
onboarding.set_phase(db_arc, "ARC", "main")
seed_rich(db_arc, "ARC")
c = store.get_or_create_current_companion(db_arc, "ARC")
store.archive_companion(db_arc, c.id, "dissolved")
line("3. 已归档")
print("已归档 + phase=main + 数据足(应 due=False,不再生成):", weekly.due(db_arc, "ARC"))

# ---- 4. 正常到期 → 后台生成 → 轮询 get ----
db = S()
store.get_or_create_user(db, "U", loss_type="breakup")
onboarding.set_phase(db, "U", "main")
seed_rich(db, "U")
line("4. 正常到期 + 后台生成")
r0 = weekly.due(db, "U")
print("首次 due(应 due=True, generating=True, report=None):", r0)
wk = r0["week_key"]
print("week_key:", wk)
weekly.flush()  # 等后台单 worker 生成完
rep = weekly.get(db, "U", wk)
print("生成后 get(应有 cards/state):",
      {"week_key": rep["week_key"],
       "cards": len(rep["report"]["cards"]),
       "lines": [c["line"] for c in rep["report"]["cards"]],
       "state": bool(rep["report"].get("state")),
       "compared": rep["report"].get("compared")})

# ---- 5. 已看标记 + 不再弹 ----
line("5. 已看标记")
print("mark_seen:", weekly.mark_seen(db, "U", wk))
print("再看 due(应 due=False, reason=已查看):", weekly.due(db, "U"))

# ---- 6. 看板 ----
line("6. 看板")
print("list_reports:", [{"week_key": x["week_key"], "seen": x["seen"],
                         "cards": len(x["report"]["cards"])} for x in weekly.list_reports(db, "U")])

print("\n周报全功能离线测试结束。")
