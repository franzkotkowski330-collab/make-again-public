"""分手场景「旁观者归因 + 依恋分析」端到端模拟(纯 mock,无网络)。

验证:
1. 意图/受害者识别(零 LLM 规则)
2. 语气路由:分手+求因+稳定 → analyze;急性 → soothe;非分手 → 绝不 analyze
3. 陪伴聊天返回 tone 字段
4. 访谈:breakup 维度插入 + 报告产出 relationship_analysis 并写入画像/memory
5. 每日主题:分手专属 insight/growth 只对分手用户开放

运行(在 backend 目录下):
    python scripts/breakup_analyze_sim.py
"""
from __future__ import annotations

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

from agent import companion, daily, interview
from config import Settings
from emotion import tone as tone_mod
from gateway.client import LLMClient
from memory import reflect, store
from memory.async_write import flush_memory_writes
from memory.db import Base
from memory.models import utcnow

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
client = LLMClient(settings=Settings(mock_llm=True))


def add_mem(db, uid, content, score, emo, days_ago, time_tag=None):
    e = store.add_memory(db, uid, content=content, summary=content,
                         emotion={"score": score, "emotion": emo}, time_tag=time_tag)
    e.ts = utcnow() - timedelta(days=days_ago)
    db.commit()


def seed_stable(db, uid, loss_type, emo="平静", base=60):
    store.get_or_create_user(db, uid, loss_type=loss_type)
    for i in range(7):
        add_mem(db, uid, f"第{i}天,{emo}", base + i, emo, days_ago=i, time_tag="晚上")
    reflect.reflect(db, uid)


db = S()
ok = True


def check(name, cond):
    global ok
    mark = "✓" if cond else "✗ 失败"
    print(f"  [{mark}] {name}")
    if not cond:
        ok = False


print("=" * 64)
print("1. 零 LLM 意图/受害者识别")
print("=" * 64)
check("「为什么我们会分手」命中归因意图", tone_mod.detect_analyze_intent("为什么我们会分手,是不是我的问题"))
check("「今天有点想他」不命中归因意图", not tone_mod.detect_analyze_intent("今天有点想他"))
check("「他出轨了」命中受害者信号", "出轨" in tone_mod.detect_victim_signals("他出轨了"))
check("「我出轨了很后悔」不判受害者", tone_mod.detect_victim_signals("我出轨了很后悔") == [])

print()
print("=" * 64)
print("2. 语气路由(breakup + 求因 + 稳定 → analyze)")
print("=" * 64)
seed_stable(db, "BF-STABLE", "breakup", emo="平静", base=60)
t = tone_mod.pick_tone(db, "BF-STABLE", message="为什么我们会分手,是不是我的问题?")
check(f"分析语气 analyze(实际 {t['tone']})", t["tone"] == tone_mod.ANALYZE)

# 急性期(难过主导 + 低分)→ 安抚
seed_stable(db, "BF-ACUTE", "breakup", emo="难过", base=32)
t = tone_mod.pick_tone(db, "BF-ACUTE", message="为什么我们会分手?")
check(f"急性期回到 soothe(实际 {t['tone']})", t["tone"] == tone_mod.SOOTHE)

# 分手 + 非求因消息 → 引导/安抚,不 analyze
t = tone_mod.pick_tone(db, "BF-STABLE", message="今天有点想他")
check(f"非求因不 analyze(实际 {t['tone']})", t["tone"] != tone_mod.ANALYZE)

# 非分手(loved_one)+ 求因 → 绝不 analyze
seed_stable(db, "LO", "loved_one", emo="平静", base=66)
t = tone_mod.pick_tone(db, "LO", message="为什么会这样?")
check(f"亲友离世不 analyze(实际 {t['tone']})", t["tone"] != tone_mod.ANALYZE)

print()
print("=" * 64)
print("3. 陪伴聊天返回 tone 字段")
print("=" * 64)
r = companion.chat(db, "BF-STABLE", "为什么我们会分手,是不是我的问题?", client)
flush_memory_writes()
check(f"chat 返回 tone=analyze(实际 {r.get('tone')})", r.get("tone") == tone_mod.ANALYZE)

print()
print("=" * 64)
print("4. 访谈:维度插入 + 归因报告写入画像/memory")
print("=" * 64)
bf_dims = interview._dims_for("breakup")
lo_dims = interview._dims_for("loved_one")
check(f"breakup 维度数=7(实际 {len(bf_dims)})", len(bf_dims) == 7)
check(f"loved_one 维度数=5(实际 {len(lo_dims)})", len(lo_dims) == 5)
bf_keys = [d["key"] for d in bf_dims]
check("conflict/attachment 在 goal 之前插入",
      bf_keys.index("conflict") < bf_keys.index("goal") and bf_keys.index("attachment") < bf_keys.index("goal"))
check("报告 prompt 按 loss_type 分支",
      interview._report_system("breakup") != interview._report_system("loved_one"))

store.get_or_create_user(db, "BF-REPORT", loss_type="breakup")
sess = store.create_interview(db, "BF-REPORT", "breakup", {"done": False})
state = {"history": [{"role": "assistant", "content": "你们怎么走到分手的?"},
                     {"role": "user", "content": "总是吵架,她嫌我回避沟通"}], "facts": []}
mem_before = len(store.list_memories(db, "BF-REPORT"))
report = interview._generate_report(db, client, sess, state)
mem_after = len(store.list_memories(db, "BF-REPORT"))
check("报告含 relationship_analysis", "relationship_analysis" in report)
up = store.get_portrait(db, "BF-REPORT", "user")
op = store.get_portrait(db, "BF-REPORT", "object")
check("用户画像含「依恋类型/关系症结」", "依恋类型" in up and "关系症结" in up)
check("对象画像含「TA的依恋倾向」", "TA的依恋倾向" in op)
check(f"memory 写入 report 条目({mem_before} → {mem_after})", mem_after > mem_before)

sample = {"attachment": {"user": "回避型", "other": "焦虑型"},
          "causes": [{"factor": "沟通错位", "side": "双方", "explain": "一个回避一个追"}],
          "patterns": "追逃模式", "suggestions": ["先学会表达需求"]}
fmt = interview._fmt_relationship_analysis(sample)
check("关系复盘文本可召回(非空)", len(fmt) > 0 and "回避型" in fmt)

print()
print("=" * 64)
print("5. 每日主题:分手专属 insight/growth")
print("=" * 64)
theme_keys = {t["key"] for t in daily.THEMES}
check("主题库含 insight/growth", {"insight", "growth"} <= theme_keys)
lo_themes = {t["key"] for t in daily.get_themes(db, "LO")["themes"]}
check("亲友离世用户不出现 insight/growth", not ({"insight", "growth"} & lo_themes))

print()
print("=" * 64)
print("结论:", "全部通过" if ok else "存在失败项")
print("=" * 64)
sys.exit(0 if ok else 1)
