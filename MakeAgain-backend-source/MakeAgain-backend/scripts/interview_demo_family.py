"""访谈体验模拟(亲人离世):真实模型 + 连贯的"亲人离世"用户剧本,演示目标早期识别与降追问后的体验。

运行(在 backend 目录下):
    python scripts/interview_demo_family.py
"""
from __future__ import annotations

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from agent import interview
from gateway.client import LLMClient
from memory.db import SessionLocal, init_db

# 连贯的"亲人离世"剧本:最疼自己的奶奶因病离世,留下未说出口的遗憾。
PERSONA = [
    "走的是我奶奶,今年年初,胰腺癌,发现的时候已经是晚期,三个月人就没了。",
    "我是奶奶带大的。小时候爸妈忙,都是奶奶接送我上下学,给我做饭,她做的红烧肉我到现在都记得味道。",
    "她特别疼我,总把好吃的留给我。我工作以后回家少了,每次回去她都站在门口张望,老远就喊我小名。",
    "最后那阵子她在医院,人瘦得厉害,还总跟我们说没事,让我们别耽误工作。我请了假去陪她,可总觉得陪得不够。",
    "我最遗憾的,是她走的前一天我还在加班,没能见上最后一面。第二天接到电话的时候,我整个人都懵了。",
    "现在一到饭点,或者看到红烧肉,我就会想起她。过年回家,那个在门口等我的人不在了,家里空了一大块。",
    "我经常自责,总觉得这些年该多回去几趟,多陪陪她。忙工作、忙应酬,把最该陪的人给耽误了。",
    "有些话我还没跟她说,比如谢谢她把我养大,还有我很想她。这些话现在只能自己憋着。",
    "我不想让奶奶的离开变成一个过不去的坎。我想带着她的爱好好生活,也想把没说的话,找个方式说出来。",
    "对,我想带着这份思念继续往前走,把自己照顾好,这也是她最希望看到的。",
]


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()

    print("【问题清单】(用户可选起始话题)")
    for i, q in enumerate(interview.questions(), 1):
        print(f"  {i}. {q['title']} —— {q['question']}")
    print()

    s = interview.start(db, "demo-family", loss_type="loved_one")
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
