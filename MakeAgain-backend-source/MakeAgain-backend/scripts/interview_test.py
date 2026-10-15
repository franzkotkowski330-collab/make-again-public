"""访谈 Agent 端到端测试:用真实模型驱动完整访谈 → 生成报告。

运行(在 backend 目录下):
    python scripts/interview_test.py
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

STORY = os.path.join(BACKEND, "..", "故事.txt")


def main() -> None:
    init_db()
    db = SessionLocal()
    client = LLMClient()

    with open(STORY, encoding="utf-8") as f:
        answers = [c.strip() for c in f.read().split("\n\n") if c.strip()][:6]
    # 追加明确覆盖"困惑"与"目标"维度的回答,便于走到 complete
    answers += [
        "最让我放不下的是,我总觉得我们不该就这么结束,好像还有很多话没说完。",
        "其实我不太想彻底忘记他,但也不想一直这么痛苦。我希望带着这段回忆慢慢往前走,先照顾好自己。",
    ]

    s = interview.start(db, "demo-interview", loss_type="breakup")
    sid = s["session_id"]
    print(f"== 开始访谈 (session={sid[:8]}) ==")
    print(f"[AI] {s['question']}\n")

    for i, ans in enumerate(answers, 1):
        print(f"[用户] {ans[:60]}...")
        r = interview.answer(db, sid, ans, client)
        if r["action"] == "complete":
            print("\n== 访谈完成,生成报告 ==")
            break
        print(f"[AI] ({r['action']} · {r.get('dimension','')}) {r['question']}\n")

    # 打印报告
    report = r.get("report") if r["action"] == "complete" else None
    if report:
        print("【反馈报告摘要】")
        print(report["summary"])
        print("\n【用户画像】")
        print(json.dumps(report["user_portrait"], ensure_ascii=False, indent=2))
        print("\n【对象画像】")
        print(json.dumps(report["object_portrait"], ensure_ascii=False, indent=2))
        print("\n【目标】", json.dumps(report["goal"], ensure_ascii=False))
        print("【疗愈计划】", json.dumps(report["heal_plan"], ensure_ascii=False, indent=2))
    else:
        print("(未触发 complete,已达到最大轮次)")

    db.close()


if __name__ == "__main__":
    main()
