"""访谈体验模拟(分手):真实模型 + 连贯的"分手"用户剧本,演示目标早期识别与降追问后的体验。

运行(在 backend 目录下):
    python scripts/interview_demo_breakup.py
"""
from __future__ import annotations

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from agent import interview
from gateway.client import LLMClient
from memory.db import SessionLocal, init_db

# 连贯的"分手"剧本:和初恋谈了四年,对方突然提分手,难以释怀。
PERSONA = [
    "她叫林薇,是我初恋,我们在一起四年了。两个月前她突然提的分手,说感觉变了,回不去了。",
    "我们大学就认识,从朋友到恋人,那时候她总说我是最懂她的人。毕业那年我们还一起搬进了现在这个城市。",
    "她笑起来眼睛是弯的,特别爱喝奶茶。以前她加班到很晚,我都会去公司楼下等她,她看到我就小跑过来。",
    "分开那天她说得很平静,反而我更崩溃。我问是不是我做错了什么,她说不是,就是感觉没了,感情淡了。",
    "最难受的是,我们每天走过的那条街、那家奶茶店,我现在经过都会愣一下。家里还有她没带走的杯子。",
    "我一直想不通,为什么好好的感情说没就没了。是不是我一直忙着工作,没照顾好她?",
    "晚上特别难熬,尤其是一个人的时候。白天上班还能撑住,一到家空荡荡的,就特别想她。",
    "我甚至去翻她以前给我发的消息,越看越难受,又停不下来。朋友劝我别看了,可我就是放不下。",
    "其实我挺想走出来的。我不恨她,只是不甘心。我想试着好好生活,把注意力放回自己身上。",
    "对,我想带着这段回忆好好往前走,而不是一直困在原地。",
]


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()

    print("【问题清单】(用户可选起始话题)")
    for i, q in enumerate(interview.questions(), 1):
        print(f"  {i}. {q['title']} —— {q['question']}")
    print()

    s = interview.start(db, "demo-breakup", loss_type="breakup")
    sid = s["session_id"]
    print("=" * 60)
    print(f"AI: {s['question']}")
    print()

    report = None
    for ans in PERSONA:
        print(f"用户: {ans}")
        print()
        r = interview.answer(db, sid, ans, client)
        if r["action"] == "complete":
            report = r["report"]
            break
        print(f"AI: ({r['dimension']}) {r['question']}")
        print()

    print("=" * 60)
    if not report:
        print("【访谈未走完】剧本轮次用尽,访谈仍在进行中。")
    else:
        print("【访谈完成,AI 生成反馈报告】")
        print()
        print(report["summary"])
        print()
        print("◆ 你的画像:")
        for k, v in report["user_portrait"].items():
            print(f"  - {k}: {v}")
        print()
        print("◆ TA 的画像:")
        for k, v in report["object_portrait"].items():
            print(f"  - {k}: {v}")
        print()
        print(f"◆ 目标: {report['goal']['label']}")
        print(f"  依据: {report['goal']['reason']}")
        print()
        print("◆ 疗愈计划:")
        for st in report["heal_plan"]["stages"]:
            print(f"  [{st['time']}] {st['title']} —— {st['desc']}")
    db.close()


if __name__ == "__main__":
    main()
