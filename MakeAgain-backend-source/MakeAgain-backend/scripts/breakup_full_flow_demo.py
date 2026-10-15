"""分手用户「旁观者归因 + 依恋分析」全流程体验模拟(真实模型)。

一条分手用户从第一次打开到持续陪伴几周的完整旅程,重点演示分手场景特有的「分析轴」:
  1. 初始访谈:7 维(比其它丧失多出「走到这一步 / 相处方式」),报告多出 relationship_analysis(依恋 + 平衡归因 + 建议)。
  2. 每日主题:分手专属「看懂这段关系 / 我在关系里的样子」进入推荐。
  3. 陪伴聊天:用户明确求因 → 旁观者分析型;受害者信号(冷暴力)→ 不归咎用户。
  4. 急性对照:主导情绪为难过时,即使明确求因也先安抚(「急性情绪才拦」)。
  5. 软引导:轻轻建议一件具体的小事(去做什么),分手场景仅在状态稳时带一句回看关系。
  6. 跟踪报告:两篇增量报告(第二篇只总结这段时间,不重复上一篇)+ 情绪日历(某天没聊就是空)。

运行(在 backend 目录下,需真实网络):
    python scripts/breakup_full_flow_demo.py
离线结构自检(占位文案,不走真实模型):
    python scripts/breakup_full_flow_demo.py --mock
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import delete

from agent import companion, daily, interview, item as item_mod, nudge, report
from config import Settings
from emotion.tone import detect_victim_signals
from gateway.client import LLMClient
from memory import calendar, reflect, store
from memory.async_write import flush_memory_writes
from memory.db import SessionLocal, init_db
from memory.models import (
    DailyPick, EmotionNode, InterviewSession, ItemMemory, MemoryEntry, NudgeLog, Portrait, StateSnapshot,
)

UID = "flow-jiangyu"      # 主线用户:分手 → 访谈 → 好转 → 求因分析
ACUTE_UID = "flow-acute"  # 对照用户:仍在急性期,演示「急性情绪才拦」
TZ = timedelta(hours=8)

TONE_LABEL = {"soothe": "温柔安抚型", "guide": "智者引导型", "analyze": "旁观者分析型"}
SIDE = {"自己": "你自己", "对方": "TA", "双方": "双方", "外部": "外部因素"}

# 连贯的分手剧本:两年半,「追-躲」循环,想让 AI 帮忙看清关系
PERSONA = [
    "我叫江屿,和前女友苏曼在一起两年半,三个月前分手,是她提的。",
    "我们是在朋友聚会上认识的,她比我大两岁,很成熟独立,一开始其实是她先追的我。",
    "后来矛盾越来越多。我是那种一有矛盾就想立刻说清楚的人,她反而是那种一吵架就关机、躲起来好几天的。",
    "她一躲我就更慌,会不停打电话、发消息;她就说我逼她、让她喘不过气。这种我追她躲的循环,我们经历了好多次。",
    "分手那天是因为一件特别小的事,我说她最近回消息越来越慢,她说她累了,不想再被我的情绪绑架。",
    "我一直想不通,是不是我太没安全感、太黏人,才把这段感情作没的?",
    "可有时候我也觉得委屈,我难受的时候她从来不安慰我,只会躲,留我一个人自己消化。",
    "我知道自己特别害怕失去她,怕到有点过度,可我也只是想要一点回应啊。",
    "我想弄明白我们到底哪里出了问题,也想知道自己在下一段关系里怎么不再重蹈覆辙。",
    "对,我想带着把这些想明白,好好往前走,把自己变得更好。",
]


def L(y, mo, d, h, mi=0) -> datetime:
    """本地时间 → UTC(供 get_themes/nudge 等 now 参数)。"""
    return datetime(y, mo, d, h, mi) - TZ


def emo(name: str, score: int) -> dict:
    valence = -0.6 if score < 45 else (0.4 if score >= 60 else 0.0)
    arousal = 0.7 if score < 45 else 0.3
    return {"emotion": name, "score": score, "valence": valence, "arousal": arousal}


def add_backdated(db, uid: str, days_ago: int, summary: str, name: str, score: int, time_tag: str | None) -> None:
    ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
    db.add(MemoryEntry(user_id=uid, type="chat", content=summary, summary=summary,
                       facts=[], emotion=emo(name, score), importance=5.0, time_tag=time_tag, ts=ts))


# 主线上线前的急性期(day 25~19):主导情绪「难过」,用于对照「急性情绪才拦」
ACUTE_SEED = [
    (25, "整晚睡不着,一直翻她的照片", "难过", 30, "晚上"),
    (24, "一个人吃饭,吃着吃着掉眼泪", "难过", 32, "晚上"),
    (23, "刷到她的朋友圈,心口一阵刺痛", "难过", 33, "晚上"),
    (22, "深夜还是睡不着,想她想到发慌", "难过", 35, "深夜"),
    (21, "周末一个人不知道去哪,好孤独", "孤独", 38, "周末"),
    (20, "想给她发消息,又不敢发", "孤独", 40, "晚上"),
    (19, "翻以前的聊天记录,越看越想", "想念", 42, "晚上"),
]

# 主线好转期(day 17~0):集中「上午·平静」形成最高频情绪节点,把主导情绪拉离急性;
# 其余分散到不同时刻,给报告卡片与情绪日历更自然的素材。
STABLE_SEED = []
for _i in range(10):  # 上午·平静 ×10 → (上午,平静) 成为最高频节点,主导情绪 = 平静
    STABLE_SEED.append((17 - _i, f"第{_i + 1}天,上午状态平稳", "平静", 58 + (_i % 5), "上午"))
STABLE_SEED += [
    (7, "晚上也不再那么难熬", "释怀", 64, "晚上"),
    (6, "和同事吃了顿饭,笑了", "平静", 60, "中午"),
    (5, "开始跑步,状态回来了", "释怀", 66, "下午"),
    (4, "想到她已经没那么痛", "释怀", 65, "傍晚"),
    (3, "偶尔会想,但能继续生活", "想念", 46, "下午"),
    (2, "整理房间,把她的东西收进箱子", "平静", 59, "傍晚"),
    (1, "睡了个安稳觉", "平静", 62, "早上"),
    (0, "今天心情还算平稳", "平静", 61, "晚上"),
]

# 对照用户:纯急性期
ACUTE_CONTRAST = [
    (6, "整晚睡不着,一直刷她的照片", "难过", 28, "晚上"),
    (5, "一个人吃饭,吃着吃着掉眼泪", "难过", 30, "晚上"),
    (4, "刷到她的朋友圈,心口一阵刺痛", "难过", 31, "晚上"),
    (3, "深夜想她想到发慌", "难过", 33, "深夜"),
    (2, "周末一个人,好孤独", "孤独", 36, "周末"),
    (1, "翻聊天记录越看越想", "想念", 40, "晚上"),
    (0, "今晚又睡不着,好难过", "难过", 30, "晚上"),
]

# ---- 多篇报告:两周期时间线 ----
# 报告 #1 覆盖 day 14~7(难过→平静),报告 #2 只总结 day 6~0(平静→释怀);
# day 11 故意留空,让情绪日历里出现「没聊的天」。
REPORT_PERIOD1 = [
    (14, "整晚睡不着,翻她的照片", "难过", 32, "晚上"),
    (14, "想起从前,心口发闷", "难过", 33, "晚上"),
    (13, "一个人吃饭掉眼泪", "难过", 35, "晚上"),
    (13, "周末不知道去哪", "孤独", 36, "周末"),
    (12, "刷到她的动态,一阵刺痛", "孤独", 38, "晚上"),
    (12, "还是忍不住想她", "孤独", 39, "晚上"),
    (12, "想发消息又不敢", "想念", 40, "晚上"),
    # (11) 这天没聊
    (10, "翻聊天记录,越看越想", "想念", 41, "晚上"),
    (10, "睡前又想起她", "想念", 42, "深夜"),
    (9, "白天好一点了", "平静", 52, "上午"),
    (9, "偶尔还是难受", "想念", 44, "下午"),
    (8, "开始试着不想那么多", "平静", 55, "上午"),
    (8, "和同事说说话,好多了", "平静", 56, "中午"),
    (7, "状态稳一点了", "平静", 58, "上午"),
    (7, "晚上没那么难熬了", "平静", 59, "晚上"),
]
REPORT_PERIOD2 = [
    (6, "今天心情还算平稳", "平静", 60, "上午"),
    (6, "工作上有点小进展", "平静", 61, "下午"),
    (5, "第一次觉得可以放下了", "释怀", 64, "晚上"),
    (5, "想到她已经没那么痛", "平静", 62, "傍晚"),
    (4, "决定把她的东西收起来", "释怀", 66, "下午"),
    (4, "释怀的感觉有点不真实", "释怀", 67, "傍晚"),
    (4, "生活慢慢回到正轨", "平静", 63, "中午"),
    (3, "开始跑步了", "平静", 64, "早上"),
    (3, "给自己做了顿饭", "释怀", 65, "晚上"),
    (2, "和好久没见的朋友吃饭", "平静", 64, "中午"),
    (2, "聊起来能笑出来了", "平静", 65, "晚上"),
    (1, "回想这段,更多是感恩", "释怀", 66, "下午"),
    (1, "今天情绪稳定", "平静", 63, "上午"),
    (0, "睡了个安稳觉", "平静", 62, "早上"),
    (0, "今天心情不错", "平静", 63, "晚上"),
]


def seed(db, uid: str, entries) -> None:
    for days_ago, summary, name, score, time_tag in entries:
        add_backdated(db, uid, days_ago, summary, name, score, time_tag)
    db.commit()
    reflect.reflect(db, uid)


def backdate_interview(db, uid: str, days_ago: int) -> None:
    """把访谈记忆的 ts 整体前移,使「最近 7 天」落在好转期,均分回升 → 触发引导/分析语气。"""
    ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
    for m in db.query(MemoryEntry).filter(MemoryEntry.user_id == uid, MemoryEntry.type == "interview"):
        m.ts = ts
    db.commit()


def make_client(mock: bool) -> LLMClient:
    if not mock:
        return LLMClient()
    # 离线自检:静默后台回写的网络报错,并让后台记忆回写也走 mock
    import logging as _logging
    _logging.disable(_logging.CRITICAL)
    import memory.async_write as _aw
    _aw.LLMClient = lambda: LLMClient(settings=Settings(mock_llm=True))
    return LLMClient(settings=Settings(mock_llm=True))


def show_relationship_analysis(ra: dict) -> None:
    if not ra:
        return
    print("◆ 关系复盘(写给你的话)")
    narrative = (ra.get("narrative") or "").strip()
    if narrative:
        print(f"  {narrative}")
        return
    text = interview._fmt_relationship_analysis(ra)  # 旧数据兜底:温和串成一段,不并列
    if text:
        print(f"  {text}")


def show_report(report: dict) -> None:
    print(report.get("summary", ""))
    print()
    print("◆ 你的画像:")
    for k, v in (report.get("user_portrait") or {}).items():
        if v and v != "暂未提及":
            print(f"  - {k}: {v}")
    print()
    print("◆ TA 的画像:")
    for k, v in (report.get("object_portrait") or {}).items():
        if v and v != "暂未提及":
            print(f"  - {k}: {v}")
    ra = report.get("relationship_analysis")
    if ra:
        print()
        show_relationship_analysis(ra)
    goal = report.get("goal") or {}
    print(f"\n◆ 目标: {goal.get('label', '')}")
    if goal.get("reason"):
        print(f"  依据: {goal['reason']}")
    stages = (report.get("heal_plan") or {}).get("stages") or []
    if stages:
        print("◆ 疗愈计划:")
        for st in stages:
            print(f"  [{st.get('time', '')}] {st.get('title', '')} —— {st.get('desc', '')}")


def run_interview(db, client) -> None:
    print("\n" + "=" * 64)
    print("【阶段 1 · 初次访谈】分手场景 7 维(含「走到这一步 / 相处方式」)")
    print("=" * 64)
    for d in interview._dims_for("breakup"):
        print(f"  · {d['title']} —— {d['question']}")
    s = interview.start(db, UID, loss_type="breakup")
    sid = s["session_id"]
    print(f"\nAI(开场): {s['question']}\n")

    report = None
    for ans in PERSONA:
        print(f"用户: {ans}\n")
        r = interview.answer(db, sid, ans, client)
        if r.get("action") == "complete":
            report = r.get("report")
            break
        q = r.get("question") or ""
        if q:
            print(f"AI({r.get('dimension', '')}): {q}\n")

    if report:
        print("—— 访谈完成,生成反馈报告(冷路径推理模型,稍候)——\n")
        show_report(report)
        reflect.reflect(db, UID)
    else:
        print("(本轮访谈未走完,后续流程以历史数据继续演示)\n")


def run_themes_and_opening(db, client, local_morning) -> None:
    print("\n" + "=" * 64)
    print("【阶段 2 · 每日主题 + 启发文案】")
    print("=" * 64)
    themes_r = daily.get_themes(db, UID, now=local_morning)
    print(f"{themes_r['reason']}(当前语气:{TONE_LABEL.get(themes_r['tone'], themes_r['tone'])})\n")
    for t in themes_r["themes"]:
        mark = " ← 分手专属" if t["key"] in ("insight", "growth") else ""
        print(f"  - [{t['key']}] {t['title']} —— {t['desc']}{mark}")

    opening_r = daily.generate_opening(db, UID, now=local_morning)
    print(f"\n今日启发文案(心情:{opening_r['mood'] or '未定'}):")
    print(f"  {opening_r['opening']}")


def run_stable_chat(db, client) -> None:
    print("\n" + "=" * 64)
    print("【阶段 3 · 陪伴聊天】状态好转后 · 用户明确求因 → 旁观者分析")
    print("=" * 64)
    turns = [
        ("我想不通我们为什么会走到分手,是不是我的问题?帮我看看这段关系。", None),
        ("她那种一吵架就关机消失好几天,是不是冷暴力?是我的问题吗?", "冷暴力"),
    ]
    for msg, victim_expect in turns:
        victim = detect_victim_signals(msg)
        print(f"用户: {msg}")
        if victim:
            print(f"  (识别到受害者信号:{victim} → 本轮触发「不归咎用户」安全边界)")
        r = companion.chat(db, UID, msg, client)
        print(f"AI({TONE_LABEL.get(r.get('tone'), r.get('tone'))}): {r['reply']}")
        print()


def run_item_ritual(db, client) -> None:
    print("\n" + "=" * 64)
    print("【阶段 3.5 · 物品纪念/寄存】聊天工具调用(tool calling)")
    print("=" * 64)

    class _FakeLLM:
        """按预设脚本返回 chat 结果:首轮带 tool_calls,次轮给个性化文案。"""
        def __init__(self, script):
            self._script = list(script)
            self.settings = client.settings
            self.mock = True

        def chat(self, messages, temperature=0.7, max_tokens=None, model=None, tools=None):
            return self._script.pop(0) if self._script else {"content": "", "usage": {}, "tool_calls": []}

    def _tc(intent: str, item_name: str, desc: str = "") -> dict:
        return {"id": "call_1", "type": "function",
                "function": {"name": "suggest_item_ritual",
                             "arguments": '{"item_name":"%s","intent":"%s","item_description":"%s"}'
                                          % (item_name, intent, desc)}}

    # ① keep:留作纪念 → 邀请上传 + 提问故事
    fake = _FakeLLM([
        {"content": "", "usage": {}, "tool_calls": [_tc("keep", "那条手链")]},
        {"content": "这条手链对你一定很重要吧?可以把它拍下来传给我,讲讲它背后的故事,我帮你一起留住这份回忆。",
         "usage": {}, "tool_calls": []},
    ])
    r = companion.chat(db, UID, "我一直留着那条手链,舍不得", fake)
    t = r.get("tool") or {}
    print(f"用户: 我一直留着那条手链,舍不得")
    print(f"AI: {r['reply']}")
    print(f"  tool → type={t.get('type')}  item_name={t.get('item_name')}  upload={t.get('upload')}")

    # ② let_go:想放下 → 建议寄存
    fake2 = _FakeLLM([
        {"content": "", "usage": {}, "tool_calls": [_tc("let_go", "他的旧手机")]},
        {"content": "如果看到它就难受,不妨把它寄存到我这里,拍张照片传上来,慢慢松开这只手。",
         "usage": {}, "tool_calls": []},
    ])
    r2 = companion.chat(db, UID, "看到他的旧手机我就一阵难受", fake2)
    t2 = r2.get("tool") or {}
    print(f"\n用户: 看到他的旧手机我就一阵难受")
    print(f"AI: {r2['reply']}")
    print(f"  tool → type={t2.get('type')}  item_name={t2.get('item_name')}  upload={t2.get('upload')}")

    # ③ keep 已讲过 → 去重,不再邀请上传
    store.add_item_memory(db, UID, item_name="手链", intent="keep", description="他送我的手链",
                          label=None, original_key="item/x.png", cutout_key="item/x_cutout.png")
    db.commit()
    fake3 = _FakeLLM([
        {"content": "", "usage": {}, "tool_calls": [_tc("keep", "那条手链")]},
        {"content": "我记得你之前跟我说过这条手链,它还在你心里占着一个位置。", "usage": {}, "tool_calls": []},
    ])
    r3 = companion.chat(db, UID, "我又想起那条手链了", fake3)
    print(f"\n用户: 我又想起那条手链了")
    print(f"AI: {r3['reply']}")
    print(f"  tool → {r3.get('tool')}  (已讲过 → 不再邀请上传)")

    # ④ 上传落库:识别 + 抠图 + 简化描述
    class _FakeVision:
        def recognize(self, image):
            return "手链"

        def ground(self, image_url, description):
            return {"label": None, "bbox": None}

        def verify(self, image_url, description):
            return {"match": True, "reason": ""}

    class _FakeStore:
        backend = "local"

        def __init__(self):
            self.deleted: list[str] = []

        def upload(self, key, data, content_type="", prefix=None):
            return f"{prefix}/{key}"

        def presigned_url(self, full_key):
            return f"local://{full_key}"

        def ai_matte(self, full_key):
            return b"PNG-CUTOUT-BYTES"

        def delete(self, full_key):
            self.deleted.append(full_key)

    up = item_mod.handle_upload(
        db, UID, b"IMAGE-BYTES", "", "keep",
        "他送我的手链,第二年生日亲手给我戴上的,那天下着小雨",
        client, vision=_FakeVision(), obj_store=_FakeStore())
    print(f"\n上传落库(识别「手链」+ 抠图):")
    print(f"  item_name={up['item_name']}  label={up['label']}  intent={up['intent']}")
    print(f"  描述(简化后): {up['description']}")
    print(f"  校验 match={up.get('match')}  抠图 {up['cutout_url']}")


def run_acute_contrast(db, client) -> None:
    print("\n" + "=" * 64)
    print("【阶段 4 · 急性期对照】同一个人,在难过主导时求因 → 先安抚,不分析")
    print("=" * 64)
    print("(以下为另一个急性期用户,演示「急性情绪才拦」)")
    r = companion.chat(db, ACUTE_UID, "我想不通我们为什么会分手,是不是我的问题?", client)
    print(f"用户: 我想不通我们为什么会分手,是不是我的问题?")
    print(f"AI({TONE_LABEL.get(r.get('tone'), r.get('tone'))}): {r['reply']}")
    print()


def run_nudge(db, client, local_night) -> None:
    print("\n" + "=" * 64)
    print("【阶段 5 · 晚间软引导】轻轻建议一件具体的小事 + 分手场景轻轻带一句回看关系")
    print("=" * 64)
    nudges = nudge.get_nudges(db, UID, client, now=local_night)
    if nudges["nudges"]:
        for n in nudges["nudges"]:
            print(f"  ({n['rule_key']}) {n['text']}")
    else:
        print("  (未命中触发条件或已去重)")


def _show_tracking_report(title: str, rep: dict) -> None:
    print(f"\n—— {title} ——")
    if not rep.get("eligible"):
        print(f"  (未生成:{rep.get('reason')})")
        return
    st = rep["state"]
    print(f"  状态:{st['stage_label']} | 均分 {st['baseline']} | 趋势 {st['trend']}")
    for c in rep.get("cards", []):
        tag = " ← 分手专属" if c["key"] in ("attachment", "cause") else ""
        print(f"  · [{c['line']}] {c['text']}{tag}")
    if rep.get("compared"):
        cmp = rep["compared"]
        print(f"  对比上次: {cmp['prev_stage']} → {cmp['curr_stage']}")


def run_reports(db, client) -> None:
    print("\n" + "=" * 64)
    print("【阶段 6 · 跟踪报告】两篇增量报告(第二篇只总结这段时间)")
    print("=" * 64)
    # 重建两周期时间线(清空此前演示的记忆/快照,报告只看这段窗口)
    db.execute(delete(MemoryEntry).where(MemoryEntry.user_id == UID))
    db.execute(delete(StateSnapshot).where(StateSnapshot.user_id == UID))
    db.execute(delete(EmotionNode).where(EmotionNode.user_id == UID))
    db.commit()
    store.get_or_create_user(db, UID, loss_type="breakup")

    now0 = datetime.now(timezone.utc).replace(tzinfo=None)

    # 报告 #1:一周前,覆盖 day 14~7(此时 day 6~0 还没发生)
    for days_ago, summary, name, score, time_tag in REPORT_PERIOD1:
        add_backdated(db, UID, days_ago, summary, name, score, time_tag)
    db.commit()
    r1 = report.build_report(db, UID, client, now=now0 - timedelta(days=7))
    _show_tracking_report("报告 #1(一周前 · 难过→平静)", r1)

    # 报告 #2:今天,只总结 day 6~0(增量,不再重复上一篇的「难过」)
    for days_ago, summary, name, score, time_tag in REPORT_PERIOD2:
        add_backdated(db, UID, days_ago, summary, name, score, time_tag)
    db.commit()
    r2 = report.build_report(db, UID, client, now=now0)
    _show_tracking_report("报告 #2(今天 · 平静→释怀)", r2)


def run_calendar(db) -> None:
    print("\n" + "=" * 64)
    print("【阶段 7 · 情绪日历】整月逐日,某天没聊就是空")
    print("=" * 64)
    cal = calendar.get_calendar(db, UID)
    print(f"情绪日历({cal['month']}):")
    empty = 0
    for day in cal["days"]:
        d = day["date"][5:]  # "MM-DD"
        if day["emotion"]:
            print(f"  {d}  {day['emotion']}  {day['score']}分 ({day['count']}条)")
        else:
            empty += 1
            print(f"  {d}  (空)")
    total = len(cal["days"])
    print(f"  —— 本月共 {total} 天,其中 {empty} 天没聊(空),{total - empty} 天有倾诉。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="离线占位文案,不调用真实模型")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    client = make_client(args.mock)

    for model in (MemoryEntry, EmotionNode, NudgeLog, DailyPick, InterviewSession, StateSnapshot, Portrait, ItemMemory):
        db.execute(delete(model).where(model.user_id.in_([UID, ACUTE_UID])))
    db.commit()

    store.get_or_create_user(db, UID, loss_type="breakup")
    store.get_or_create_user(db, ACUTE_UID, loss_type="breakup")
    seed(db, UID, ACUTE_SEED)
    seed(db, ACUTE_UID, ACUTE_CONTRAST)

    print("=" * 64)
    print("「重逢」分手用户全流程模拟 · 用户:江屿(两年半 · 分手)")
    print("=" * 64)
    if args.mock:
        print("(mock 模式:离线占位文案,仅用于链路自检)\n")

    run_interview(db, client)
    backdate_interview(db, UID, days_ago=18)  # 访谈是几周前的事,让「最近 7 天」落到好转期
    seed(db, UID, STABLE_SEED)                # 铺入「好转期」历史 → 主导情绪脱离急性、均分回升
    run_themes_and_opening(db, client, L(2026, 8, 28, 9, 0))
    run_stable_chat(db, client)
    run_item_ritual(db, client)
    run_acute_contrast(db, client)
    flush_memory_writes()
    run_nudge(db, client, L(2026, 8, 28, 22, 30))
    run_reports(db, client)
    run_calendar(db)

    db.close()
    print("\n" + "=" * 64)
    print("全流程结束:访谈(归因/依恋) → 每日主题 → 启发文案 → 求因分析聊天 → 急性对照 → 软引导 → 跟踪报告(多篇) → 情绪日历。")


if __name__ == "__main__":
    main()
