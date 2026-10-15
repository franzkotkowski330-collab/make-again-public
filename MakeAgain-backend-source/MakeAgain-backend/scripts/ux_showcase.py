"""用户体验展示:把每种场景下 AI 实际生成的文案/内容跑出来,供人工把关。

走真实 LLM(DeepSeek),纯展示、不改任何产品逻辑;临时 SQLite 库,不污染 data/app.db。

展示清单:
1. 危机回复(固定安全文案)
2. 陪伴回复:安抚 / 旁观者分析 / 引导(分手)、亲友离世、宠物离世、物品 keep / let_go
3. 访谈:分手(交互式问答 + 报告)、亲友离世报告、宠物离世报告
4. 每日主题 + 启发文案
5. 软引导提醒文案(深夜 / 情绪节点 / 分手回看)
6. 定期跟踪报告(总结 + 卡片)
7. 树洞:写信(摘要+标签提取) + 匹配
8. 物品描述简化(LLM 压缩)

运行(在 backend 目录下):
    python scripts/ux_showcase.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from agent import companion, daily, interview, item as item_mod, nudge, report, treehole  # noqa: E402
from config import Settings  # noqa: E402
from emotion import crisis as crisis_mod, tone as tone_mod  # noqa: E402
from gateway.client import LLMClient  # noqa: E402
from memory import reflect as reflect_mod, store  # noqa: E402
from memory.async_write import flush_memory_writes  # noqa: E402
from memory.db import Base  # noqa: E402
from memory.models import EmotionNode, utcnow  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
engine = create_engine(f"sqlite:///{_tmp.name.replace(chr(92), '/')}",
                       connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

import memory.async_write as _aw  # noqa: E402
_aw.SessionLocal = S
_aw.LLMClient = lambda: LLMClient(settings=Settings(mock_llm=True))  # 后台抽取走 mock,提速

db = S()
client = LLMClient()  # 真实模型


def hr(title: str) -> None:
    print()
    print("#" * 72)
    print(f"# {title}")
    print("#" * 72)


def sec(title: str) -> None:
    print(f"\n———— {title} ————")


def add_mem(uid: str, content: str, score: int, emo: str, days_ago: int,
            time_tag: str | None = None, place_tag: str | None = None) -> None:
    e = store.add_memory(db, uid, content=content, summary=content,
                         emotion={"score": score, "emotion": emo},
                         time_tag=time_tag, place_tag=place_tag)
    e.ts = utcnow() - timedelta(days=days_ago)
    db.commit()


def tool_call(name: str, args: str) -> dict:
    return {"id": "call_1", "type": "function",
            "function": {"name": name, "arguments": args}}


class ToolShim:
    """首轮强制返回物品工具调用,末轮用真实模型写个性化文案(确定性演示工具路径)。"""

    def __init__(self, real: LLMClient, args: dict):
        self.real = real
        self.settings = real.settings
        self.mock = False
        self._args = args
        self._used = False

    def chat(self, messages, temperature=0.7, max_tokens=None, model=None, tools=None):
        if tools is not None and not self._used:
            self._used = True
            return {"content": "", "usage": {},
                    "tool_calls": [tool_call("suggest_item_ritual",
                                             json.dumps(self._args, ensure_ascii=False))]}
        return self.real.chat(messages, temperature=temperature, max_tokens=max_tokens, model=model)


# =====================================================================
hr("「重逢」AI 生成内容展示(真实模型)")
# =====================================================================

# ---------------------------------------------------------------
hr("1. 危机回复(固定安全文案,非模型生成)")
# ---------------------------------------------------------------
r = companion.chat(db, "U-CRISIS", "我想自杀,活不下去了", client)
print(r["reply"])
print(f"\n(命中词:{r['crisis']['hit']} / 级别:{r['crisis']['level']})")

# ---------------------------------------------------------------
hr("2. 陪伴回复(语气路由 × 丧失类型)")
# ---------------------------------------------------------------
# 分手用户「林薇」:画像 + 记忆,让回复有"记得你"的实感
store.get_or_create_user(db, "LINWEI", loss_type="breakup")
store.upsert_portrait(db, "LINWEI", "user", {
    "昵称": "林薇", "关系与背景": "初恋四年", "当前情绪状态": "晚上容易反复",
    "情绪波动点": "晚上、周六", "困惑点": "是不是自己没照顾好这段感情"})
store.upsert_portrait(db, "LINWEI", "object", {"称呼": "小夏", "关系": "前任", "性格": "温柔"})
add_mem("LINWEI", "以前周六晚上都会一起看电影", 40, "想念", 6, time_tag="周六")
add_mem("LINWEI", "晚上回家看到空荡荡的客厅就难受", 38, "难过", 5, time_tag="晚上")
add_mem("LINWEI", "他加班晚我总去公司楼下等他", 45, "想念", 4, place_tag="公司")
reflect_mod.reflect(db, "LINWEI")

sec("A. 分手 · 急性情绪(安抚型)")
r = companion.chat(db, "LINWEI", "今晚回家,一进门就忍不住掉眼泪,好难受", client)
flush_memory_writes()
print(f"[语气:{r['tone']}] {r['reply']}")

sec("B. 分手 · 主动求因(旁观者分析型)")
r = companion.chat(db, "LINWEI", "我想弄明白,我们为什么会走到分手,是不是我的问题", client)
flush_memory_writes()
print(f"[语气:{r['tone']}] {r['reply']}")

store.get_or_create_user(db, "LINWEI-S", loss_type="breakup")
store.upsert_portrait(db, "LINWEI-S", "user", {"昵称": "林薇", "关系与背景": "初恋四年"})
store.upsert_portrait(db, "LINWEI-S", "object", {"称呼": "小夏", "关系": "前任", "性格": "温柔"})
add_mem("LINWEI-S", "今天好好吃了一顿饭,平静很多", 62, "平静", 3)
add_mem("LINWEI-S", "把合照收进抽屉,慢慢释怀了", 66, "释怀", 2)
add_mem("LINWEI-S", "开始重新规划自己的生活", 68, "平静", 1)
reflect_mod.reflect(db, "LINWEI-S")

sec("C. 分手 · 状态趋稳(引导型)")
r = companion.chat(db, "LINWEI-S", "最近慢慢没那么难过了,想把生活重新过起来", client)
flush_memory_writes()
print(f"[语气:{r['tone']}] {r['reply']}")

sec("D. 分手 · 物品纪念(keep 工具)")
r = companion.chat(db, "LINWEI", "我一直留着那条他送我的手链", ToolShim(client, {"item_name": "那条手链", "intent": "keep"}))
flush_memory_writes()
print(f"[tool:{r['tool']['type'] if r['tool'] else None}] {r['reply']}")

sec("E. 分手 · 物品寄存(let_go 工具)")
r = companion.chat(db, "LINWEI", "看到他的旧手机我就难受,想丢掉又舍不得", ToolShim(client, {"item_name": "他的旧手机", "intent": "let_go"}))
flush_memory_writes()
print(f"[tool:{r['tool']['type'] if r['tool'] else None}] {r['reply']}")

# 亲友离世「奶奶」
store.get_or_create_user(db, "NANAI", loss_type="loved_one")
store.upsert_portrait(db, "NANAI", "user", {"昵称": "小宇", "关系与背景": "奶奶带大的"})
store.upsert_portrait(db, "NANAI", "object", {"称呼": "奶奶", "关系": "奶奶", "性格": "慈祥"})
add_mem("NANAI", "一到晚上就想起奶奶在厨房忙活", 40, "想念", 3, time_tag="晚上")
reflect_mod.reflect(db, "NANAI")

sec("F. 亲友离世 · 思念(默认安抚陪伴)")
r = companion.chat(db, "NANAI", "今晚做了奶奶最拿手的那道菜,一边做一边掉眼泪", client)
flush_memory_writes()
print(f"[语气:{r['tone']}] {r['reply']}")

# 宠物离世「我的猫」
store.get_or_create_user(db, "MAO", loss_type="pet")
store.upsert_portrait(db, "MAO", "user", {"昵称": "阿泽"})
store.upsert_portrait(db, "MAO", "object", {"称呼": "团子", "关系": "养了八年的猫", "性格": "黏人"})
add_mem("MAO", "开门的一瞬间习惯性叫它的名字", 38, "想念", 2, place_tag="家里")
reflect_mod.reflect(db, "MAO")

sec("G. 宠物离世 · 思念(默认安抚陪伴)")
r = companion.chat(db, "MAO", "今天回家,开门的一瞬间又习惯性地喊了它的名字", client)
flush_memory_writes()
print(f"[语气:{r['tone']}] {r['reply']}")

# ---------------------------------------------------------------
hr("3. 访谈报告(分手交互式 + 亲友/宠物脚本式)")
# ---------------------------------------------------------------
sec("A. 分手 · 交互式访谈(真实模型逐步追问)")
s = interview.start(db, "LINWEI", loss_type="breakup")
print(f"AI 开场: {s['question']}")
answers = [
    "她叫小夏,是我初恋,我们在一起四年。两个月前她突然提的分手,说感情淡了。",
    "以前她加班晚,我都会去公司楼下等她,她看到我就一路小跑过来,那是我们最开心的时候。",
    "现在晚上一个人特别难熬,一回家空荡荡的就想她,又忍不住去翻以前的聊天记录。",
    "我一直想不通,是不是我忙着工作没照顾好她,感情才慢慢变淡的。",
    "其实我不恨她,就是不甘心。我想带着这段回忆好好往前走,把生活重新过起来。",
    "对,我想带着这段回忆好好往前走,而不是一直困在原地。",
]
report_out = None
for i, ans in enumerate(answers):
    print(f"\n用户: {ans}")
    r = interview.answer(db, s["session_id"], ans, client)
    if r["action"] == "complete":
        report_out = r["report"]
        break
    print(f"AI ({r.get('dimension','')}): {r['question']}")

if report_out:
    print("\n—— 分手初始报告 ——")
    print(report_out["summary"])
    print("\n[用户画像]", json.dumps(report_out.get("user_portrait", {}), ensure_ascii=False))
    print("[对象画像]", json.dumps(report_out.get("object_portrait", {}), ensure_ascii=False))
    print("[目标]", json.dumps(report_out.get("goal", {}), ensure_ascii=False))
    print("[疗愈计划]", json.dumps(report_out.get("heal_plan", {}), ensure_ascii=False))
    ra = report_out.get("relationship_analysis")
    if ra:
        print("\n[关系复盘·正文]")
        print(ra.get("narrative") or interview._fmt_relationship_analysis(ra))


def report_for(loss_type: str, conv: list) -> dict:
    uid = f"SHOW-{loss_type}"
    store.get_or_create_user(db, uid, loss_type=loss_type)
    sess = store.create_interview(db, uid, loss_type, {"history": []})
    state = {"history": conv}
    return interview._generate_report(db, client, sess, state)


sec("B. 亲友离世 · 报告(脚本式对话)")
rep = report_for("loved_one", [
    {"role": "assistant", "content": "先跟我聊聊 TA 吧"},
    {"role": "user", "content": "我奶奶,从小把我带大。半年前因为癌症走的,走得很突然。"},
    {"role": "assistant", "content": "你们之间最让你反复想起的是什么?"},
    {"role": "user", "content": "她做的红烧肉,还有每个夏天在院子里给我扇扇子的样子。"},
    {"role": "assistant", "content": "最近哪些时刻最容易难过?"},
    {"role": "user", "content": "一个人吃饭的时候,还有逢年过节。"},
    {"role": "assistant", "content": "你最想对奶奶说但没来得及说的是什么?"},
    {"role": "user", "content": "想告诉她,我已经会做饭了,会照顾好自己。"},
])
print(rep["summary"])
print("\n[用户画像]", json.dumps(rep.get("user_portrait", {}), ensure_ascii=False))
print("[对象画像]", json.dumps(rep.get("object_portrait", {}), ensure_ascii=False))
print("[目标]", json.dumps(rep.get("goal", {}), ensure_ascii=False))

sec("C. 宠物离世 · 报告(脚本式对话)")
rep = report_for("pet", [
    {"role": "assistant", "content": "聊聊你的宠物吧"},
    {"role": "user", "content": "我的猫叫团子,养了八年。上个月老死的,走得很安详。"},
    {"role": "assistant", "content": "最让你难忘的瞬间?"},
    {"role": "user", "content": "每天下班它都在门口等我,一进门就蹭我的脚。"},
    {"role": "assistant", "content": "现在最放不下的是什么?"},
    {"role": "user", "content": "总觉得它还躲在哪个角落,想再抱抱它。"},
])
print(rep["summary"])
print("\n[用户画像]", json.dumps(rep.get("user_portrait", {}), ensure_ascii=False))
print("[目标]", json.dumps(rep.get("goal", {}), ensure_ascii=False))

# ---------------------------------------------------------------
hr("4. 每日主题 + 启发文案")
# ---------------------------------------------------------------
themes = daily.get_themes(db, "LINWEI")
print(f"推荐理由: {themes['reason']}")
for t in themes["themes"]:
    print(f"  - [{t['key']}] {t['title']} —— {t['desc']}")

op = daily.generate_opening(db, "LINWEI")
print(f"\n—— 今日启发文案(心情:{op['mood'] or '未定'})——")
print(op["opening"])

# ---------------------------------------------------------------
hr("5. 软引导提醒文案")
# ---------------------------------------------------------------
store.get_or_create_user(db, "LINWEI", loss_type="breakup")
db.add(EmotionNode(user_id="LINWEI", trigger="晚上", emotion="孤独", frequency=3, time_tag="晚上"))
db.add(EmotionNode(user_id="LINWEI", trigger="周六", emotion="想念", frequency=2, time_tag="周六"))
db.commit()


def L(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi) - timedelta(hours=8)


nudges = nudge.get_nudges(db, "LINWEI", client, now=L(2026, 8, 28, 22, 30))
print(f"[{nudges['now']}] 周五晚间(命中「晚上」节点)")
for n in nudges["nudges"]:
    print(f"  ({n['rule_key']}) {n['text']}")

nudges2 = nudge.get_nudges(db, "LINWEI", client, now=L(2026, 8, 29, 15, 0))
print(f"\n[{nudges2['now']}] 周六下午(命中「周六」节点)")
for n in nudges2["nudges"]:
    print(f"  ({n['rule_key']}) {n['text']}")

# ---------------------------------------------------------------
hr("6. 定期跟踪报告")
# ---------------------------------------------------------------
store.get_or_create_user(db, "RPT", loss_type="breakup", goal="carry_on")
store.upsert_portrait(db, "RPT", "object", {"称呼": "小夏", "性格": "温柔"})
for i in range(18):
    days_ago = 17 - i
    if i < 4:
        emo, sc = "难过", 40
    elif i % 2 == 0:
        emo, sc = "平静", 62
    else:
        emo, sc = "释怀", 68
    add_mem("RPT", f"第{i}天,关于小夏", sc, emo, days_ago=days_ago, time_tag="下午")
print("资格:", report.report_eligibility(db, "RPT"))
rr = report.build_report(db, "RPT", client)
st = rr["state"]
print(f"状态: {st['stage_label']} | 均分 {st['baseline']} | 趋势 {st['trend']} | 平静占比 {st['calm_ratio']}")
print("\n卡片:")
for c in rr["cards"]:
    print(f"  [{c['line']}] {c['text']}")

# ---------------------------------------------------------------
hr("7. 树洞信箱(写信摘要/标签 + 匹配)")
# ---------------------------------------------------------------
store.get_or_create_user(db, "TH-W", loss_type="loved_one")
for i in range(3):
    add_mem("TH-W", f"第{i}天的倾诉", 50, "难过", days_ago=i)
w = treehole.write_letter(db, "TH-W", "奶奶走后,我总觉得心里空了一块,很多话没来得及说。每次过年回老家,看到那间空屋子都想哭。", client)
print("写信结果:", w)
if w["ok"]:
    print(f"  提取摘要: {w['summary']}")
    print(f"  提取标签: {w['tags']}")
    print(f"  识别情绪: {w['emotion']}")

store.get_or_create_user(db, "TH-B", loss_type="loved_one")
for i in range(8):
    add_mem("TH-B", f"第{i}天,慢慢接受", 60, "平静", days_ago=i)
print("\n回信资格:", treehole.reply_eligibility(db, "TH-B"))
m = treehole.get_matches(db, "TH-B", client)
print("匹配到的信:")
for x in m["matches"]:
    print(f"  - 情绪={x['emotion']} 摘要={x['summary']}")

# ---------------------------------------------------------------
hr("8. 物品描述简化(LLM 压缩)")
# ---------------------------------------------------------------
long_desc = ("这是那条他送我的手链,我们在一起第二年他生日那天,他亲手给我戴上的,"
             "那天下着小雨,他说以后每个生日都要陪我一起过,当时我特别感动,"
             "后来每次看到这条手链,都会想起那天他认真又笨拙的样子。")
simplified = item_mod.simplify_description(client, long_desc)
print("原文:", long_desc)
print("简化:", simplified)
print(f"(原文 {len(long_desc)} 字 → 简化 {len(simplified)} 字)")

print()
print("=" * 72)
print("展示结束。")
db.close()
engine.dispose()
try:
    os.remove(_tmp.name)
except OSError:
    pass
