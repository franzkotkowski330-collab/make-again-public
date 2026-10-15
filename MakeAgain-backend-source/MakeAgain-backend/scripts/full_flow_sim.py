"""「重逢」完整流程模拟:初访访谈 → 每日主题/启发文案 → 陪伴聊天 → 软引导 → 情绪日历/反思。

一条用户从第一次打开到连续陪伴两天的完整链路,真实模型跑通。

运行(在 backend 目录下):
    python scripts/full_flow_sim.py

说明:
- 访谈走 fast 模型逐轮决策,最终报告走冷路径推理模型(deepseek-v4-pro),会稍慢。
- 为演示情绪日历与节点演变,先埋了"过去一周"的心情历史,再叠加访谈与两天的真实聊天。
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

from agent import companion, daily, interview, nudge
from gateway.client import LLMClient
from memory import calendar, reflect, store
from memory.async_write import flush_memory_writes
from memory.db import SessionLocal, init_db
from memory.models import DailyPick, EmotionNode, InterviewSession, MemoryEntry, NudgeLog
from sqlalchemy import delete

UID = "flow-linwei"
TZ = timedelta(hours=8)

# 访谈剧本(紧凑,早期表达目标 → 触发"目标早期识别"快速收尾)
PERSONA = [
    "她叫林薇,是我初恋,我们在一起四年了。两个月前她突然提的分手,说感情淡了。",
    "以前她加班晚,我都会去公司楼下等她,她看到我就一路小跑过来,那是我们最开心的时候。",
    "现在晚上一个人特别难熬,一回家空荡荡的就想她,又忍不住去翻以前的聊天记录。",
    "我一直想不通,是不是我忙着工作没照顾好她,感情才慢慢变淡的。",
    "其实我不恨她,就是不甘心。我想带着这段回忆好好往前走,把生活重新过起来。",
    "对,我想带着这段回忆好好往前走,而不是一直困在原地。",
]


def L(y, mo, d, h, mi=0) -> datetime:
    """本地时间 → UTC(供 get_themes/nudge 等 now 参数)。"""
    return datetime(y, mo, d, h, mi) - TZ


def add_backdated(db, days_ago: int, summary: str, emotion: dict, time_tag: str | None) -> None:
    ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
    db.add(MemoryEntry(user_id=UID, type="chat", content=summary, summary=summary,
                       facts=[], emotion=emotion, importance=5.0, time_tag=time_tag, ts=ts))


def seed_history(db) -> None:
    """过去一周的心情历史(供情绪日历 + 情绪节点有个起点)。"""
    days = [
        (7, "整晚睡不着,一直刷手机", {"emotion": "难过", "score": 30, "valence": -0.6, "arousal": 0.7}, "晚上"),
        (6, "周六约会日,一个人不知道去哪", {"emotion": "想念", "score": 38, "valence": -0.4, "arousal": 0.6}, "周六"),
        (5, "早上习惯性想发早安,才发现删了联系方式", {"emotion": "孤独", "score": 40, "valence": -0.5, "arousal": 0.5}, "早上"),
        (4, "晚上又想起她说的那些话,想哭", {"emotion": "想念", "score": 41, "valence": -0.4, "arousal": 0.6}, "晚上"),
        (3, "好好吃了一顿饭,平静一些", {"emotion": "平静", "score": 58, "valence": 0.2, "arousal": 0.3}, None),
        (2, "把合照收进抽屉,好像没那么痛了", {"emotion": "释怀", "score": 64, "valence": 0.4, "arousal": 0.3}, None),
        (1, "下午路过咖啡店,还是有点走神", {"emotion": "想念", "score": 45, "valence": -0.3, "arousal": 0.5}, "下午"),
    ]
    for days_ago, summary, emotion, time_tag in days:
        add_backdated(db, days_ago, summary, emotion, time_tag)
    db.commit()


def show_report(report: dict) -> None:
    print(report["summary"])
    print()
    print("  ◆ 你的画像:", " / ".join(f"{k}={v}" for k, v in report["user_portrait"].items()))
    print("  ◆ TA 的画像:", " / ".join(f"{k}={v}" for k, v in report["object_portrait"].items()))
    print(f"  ◆ 目标: {report['goal']['label']}({report['goal']['type']})")
    stages = " → ".join(f"{st['title']}" for st in report["heal_plan"]["stages"])
    print(f"  ◆ 疗愈计划: {stages}")


def run_interview(db, client) -> None:
    print("\n" + "=" * 64)
    print("【阶段 1 · 初次访谈】")
    print("=" * 64)
    s = interview.start(db, UID, loss_type="breakup")
    sid = s["session_id"]
    print(f"AI: {s['question']}\n")

    report = None
    for ans in PERSONA:
        print(f"用户: {ans}\n")
        r = interview.answer(db, sid, ans, client)
        if r["action"] == "complete":
            report = r["report"]
            break
        print(f"AI ({r.get('dimension')}): {r['question']}\n")

    if report:
        print("—— 访谈完成,生成反馈报告(冷路径推理模型,稍候)——\n")
        show_report(report)
        # 访谈沉淀了大量情绪记忆,立即重建情绪节点供后续主题/软引导使用
        reflect.reflect(db, UID)
    else:
        print("【访谈未走完】剧本轮次用尽。")


def run_day(db, client, day_no: int, local_morning: datetime, local_night: datetime) -> None:
    print("\n" + "=" * 64)
    print(f"【阶段 {day_no + 1} · 日常陪伴 第{day_no}天】")
    print("=" * 64)

    # 1. 每日主题
    themes_r = daily.get_themes(db, UID, now=local_morning)
    print(f"今日主题: {themes_r['reason']}")
    keys = [t["key"] for t in themes_r["themes"]]
    for t in themes_r["themes"]:
        print(f"  - [{t['key']}] {t['title']} —— {t['desc']}")

    # 2. 今日总启发文案(依据心情)
    opening_r = daily.generate_opening(db, UID, now=local_morning)
    print(f"\n今日启发文案(心情:{opening_r['mood'] or '未定'}):")
    print(f"  {opening_r['opening']}")

    # 3. 陪伴聊天
    msgs = [
        "今天路过我们以前常去的那家店,又想起她了。",
        "我想把那些没来得及说的话,慢慢说给自己听。",
    ]
    msg = msgs[day_no % len(msgs)]
    reply = companion.chat(db, UID, msg, client)
    print(f"\n用户: {msg}")
    print(f"AI: {reply['reply']}")
    print(f"  (即时情绪:{reply['emotion']['emotion']} / 召回记忆 {reply['recalled']} 条)")

    # 4. 等后台记忆回写 + 节点演变完成
    flush_memory_writes()

    # 5. 晚间软引导
    nudges = nudge.get_nudges(db, UID, client, now=local_night)
    if nudges["nudges"]:
        print(f"\n晚间软引导({nudges['now']}):")
        for n in nudges["nudges"]:
            print(f"  ({n['rule_key']}) {n['text']}")
    else:
        print(f"\n晚间软引导({nudges['now']}): 无(未命中触发条件或已去重)")


def run_digest(db) -> None:
    print("\n" + "=" * 64)
    print("【阶段 4 · 沉淀:反思洞察 + 情绪日历】")
    print("=" * 64)

    r = reflect.reflect(db, UID)
    print(f"反思(共 {r['count']} 条记忆):")
    for ins in r["insights"]:
        print(f"  · {ins}")

    print("\n情绪节点(trigger/emotion × frequency):")
    for n in sorted(r["nodes"], key=lambda x: -x["frequency"]):
        print(f"  · {n['trigger']}/{n['emotion']} × {n['frequency']}")

    cal = calendar.get_calendar(db, UID)
    print(f"\n情绪日历({cal['month']}):")
    for d in cal["days"]:
        bar = "█" * int(round(d["score"] / 10))
        print(f"  {d['date']}  {d['emotion']:<3} {d['score']:<5} {bar}")


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()

    for model in (MemoryEntry, EmotionNode, NudgeLog, DailyPick, InterviewSession):
        db.execute(delete(model).where(model.user_id == UID))
    db.commit()
    store.get_or_create_user(db, UID, loss_type="breakup")
    seed_history(db)
    reflect.reflect(db, UID)

    print("=" * 64)
    print("「重逢」完整流程模拟 · 用户:林薇(初恋四年 · 分手)")
    print("=" * 64)

    run_interview(db, client)
    run_day(db, client, 1, L(2026, 8, 28, 9, 0), L(2026, 8, 28, 22, 30))   # 周五
    run_day(db, client, 2, L(2026, 8, 29, 9, 0), L(2026, 8, 29, 23, 0))   # 周六
    run_digest(db)

    db.close()
    print("\n" + "=" * 64)
    print("全流程结束:访谈→每日主题→启发文案→聊天→记忆/节点演变→软引导→反思→情绪日历。")


if __name__ == "__main__":
    main()
