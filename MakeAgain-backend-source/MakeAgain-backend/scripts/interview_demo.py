"""访谈体验模拟:用真实模型 + 连贯的"宠物离世"用户剧本,完整演示用户体验。

运行(在 backend 目录下):
    python scripts/interview_demo.py
"""
from __future__ import annotations

import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from agent import interview
from gateway.client import LLMClient
from memory.db import SessionLocal, init_db

# 一个连贯的用户剧本:养了12年的金毛"毛球"上周突然离世
PERSONA = [
    "它叫毛球,是一只金毛,陪我十二年了。上周三走的,走得很突然,我没能见到它最后一面。",
    "它特别黏人,每天我下班回家,它老远就摇着尾巴在门口等。它是那种你一坐下,它就把头搁在你腿上的狗。",
    "其实它最后那几天有点蔫,不爱吃饭,我以为只是老了、天热,没往坏处想。现在特别后悔没早点带它去医院。",
    "最想它的就是晚上,以前它都睡在我床边,我伸手就能摸到它。现在床边空荡荡的,晚上特别安静。",
    "我还留着它的项圈,还有那个它咬了好多年的小黄鸭玩具,都放在它原来睡的地方,没舍得收。",
    "晚上最难熬,尤其是十一点以后。白天忙起来还好,一躺下就会想起它趴在我旁边打呼噜的声音。",
    "我就是很内疚,总觉得要是早点发现它不舒服,它可能还能多陪我几年。别人说只是一只狗,可它对我来说就是家人。",
    "我不想忘记它。我想把这些回忆好好收着,慢慢习惯没有它的日子,但让它一直在我心里。",
    "就是内疚,还有晚上一个人的那种空。我也在想,要不要再养一只,可又怕别人说这么快就忘了它。",
    "我最怕的是时间久了,我会不会把它的样子、它打呼噜的声音都忘了,这让我特别慌。",
    "我确定我不想忘记它。我想好好记着它,但也不想一直陷在难过里,想给它写点什么、做个纪念。",
    "我不想彻底忘掉它。我想带着这份记忆继续往前走,好好记住它,也让自己慢慢从难过里走出来。",
]


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()

    # 1. 问题清单(用户最开始看到的)
    print("【问题清单】(用户可选起始话题)")
    for i, q in enumerate(interview.questions(), 1):
        print(f"  {i}. {q['title']} —— {q['question']}")
    print()

    # 2. 开始访谈
    s = interview.start(db, "demo-pet", loss_type="pet")
    sid = s["session_id"]
    print("=" * 60)
    print(f"AI: {s['question']}")
    print()

    # 3. 逐轮对话
    report = None
    for ans in PERSONA:
        print(f"用户: {ans}")
        print()
        r = interview.answer(db, sid, ans, client)
        if r["action"] == "complete":
            report = r["report"]
            break
        print(f"AI: {r['question']}")
        print()

    # 4. 报告
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
