"""白盒测试:模拟真实用户,覆盖「重逢」全部功能与全部预设分支。

纯离线、零网络(LLM/语音/视觉/COS 全 mock),在临时 SQLite 库上跑,不污染 data/app.db。

覆盖矩阵(按模块):
- 情绪:classify(词典兜底)/ crisis(高危/中危/否定守卫)/ tone(安抚/引导/旁观者分析/受害者)
- 陪伴:companion.chat(危机短路/普通回复/工具循环 keep·let_go·去重)
- 物品:execute_tool / tool_payload / simplify / _image_size / handle_upload / has_item_story
- 访谈:维度按 loss_type 分叉 / followup 保护 / goal_signal 跳转 / complete / MAX_TURNS / revise
- 每日主题:get_themes(分手专属池) / generate_opening
- 软引导:深夜触发 / 情绪节点触发 / 去重 / 深夜与"晚上"节点去冗余 / 语录兜底
- 报告:资格门槛 / 阶段选卡(低谷 vs 和解) / 卡片上限 / 对比上次
- 树洞:写信资格/写信/PII / 回信资格(天数/近期/稳定/急性) / 匹配 / 回信 / 自回 / 审核
- 安全:scan_pii(手机/座机/邮箱/身份证/社交/URL/地址)
- 记忆:store CRUD / state(阶段+记忆点) / reflect / recall / calendar / extract
- 网关:parse_json / speech(mock) / LLMClient(mock)
- 三种丧失类型 breakup / loved_one / pet 均走一遍

运行(在 backend 目录下):
    python scripts/whitebox_test.py
"""
from __future__ import annotations

import os
import struct
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

from agent import companion, daily, history as history_mod, home as home_mod, interview, item as item_mod, moderation, nudge, onboarding, report, treehole, weekly as weekly_mod  # noqa: E402
from config import Settings  # noqa: E402
from emotion import classifier as classifier_mod, crisis as crisis_mod, tone as tone_mod  # noqa: E402
from gateway.client import LLMClient  # noqa: E402
from gateway.schemas import parse_json  # noqa: E402
from gateway.speech import SpeechClient  # noqa: E402
from memory import calendar as calendar_mod, extract as extract_mod, recall as recall_mod, reflect as reflect_mod, store, state as state_mod  # noqa: E402
from memory.async_write import flush_memory_writes  # noqa: E402
from memory.db import Base  # noqa: E402
from memory.models import ChatMessage, EmotionNode, utcnow  # noqa: E402

# ---- 临时文件库(跨线程共享),并把后台记忆回写导向同一份库 + mock ----
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
engine = create_engine(f"sqlite:///{_tmp.name.replace(chr(92), '/')}",
                       connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

import memory.async_write as _aw  # noqa: E402
_aw.SessionLocal = S
_aw.LLMClient = lambda: LLMClient(settings=Settings(mock_llm=True))

import agent.async_report as _ar  # noqa: E402
_ar.SessionLocal = S

import agent.weekly as _wk  # noqa: E402
_wk.SessionLocal = S
_wk.LLMClient = lambda: LLMClient(settings=Settings(mock_llm=True))

db = S()
client = LLMClient(settings=Settings(mock_llm=True))

_PASS: list[str] = []
_FAIL: list[str] = []


def check(name: str, cond: bool) -> None:
    mark = "✓" if cond else "✗ 失败"
    print(f"  [{mark}] {name}")
    (_PASS if cond else _FAIL).append(name)


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


def add_mem(uid: str, content: str, score: int, emo: str, days_ago: int,
            time_tag: str | None = None, place_tag: str | None = None) -> None:
    e = store.add_memory(db, uid, content=content, summary=content,
                         emotion={"score": score, "emotion": emo},
                         time_tag=time_tag, place_tag=place_tag)
    e.ts = utcnow() - timedelta(days=days_ago)
    db.commit()


def archive_companion_of(uid: str) -> None:
    """建一个当前陪伴并归档,满足「已结束陪伴才可回信」的前置条件。"""
    c = store.create_companion(db, uid)
    store.archive_companion(db, c.id, "dissolved")


def tool_call(name: str, args: str) -> dict:
    return {"id": "call_1", "type": "function",
            "function": {"name": name, "arguments": args}}


class FakeClient:
    """按预设脚本逐次返回 chat / chat_json 结果;覆盖陪伴工具循环与访谈决策。"""

    def __init__(self, script=None, json_script=None):
        self.settings = Settings(mock_llm=True)
        self.mock = True
        self.script = list(script or [])
        self.json_script = list(json_script or [])

    def chat(self, messages, temperature=0.7, max_tokens=None, model=None, tools=None):
        if self.script:
            return self.script.pop(0)
        return {"content": "", "usage": {}, "tool_calls": []}

    def chat_json(self, messages, temperature=0.2, max_tokens=None, model=None):
        if self.json_script:
            return self.json_script.pop(0), {"usage": {}, "raw": ""}
        return None, {"usage": {}, "raw": ""}


def iv_step(action: str, question: str = "", next_dimension: str | None = None,
            covered=None, facts=None, emotion=None, goal_signal: bool = False) -> dict:
    return {
        "action": action, "question": question, "next_dimension": next_dimension,
        "covered_dimensions": covered or [], "facts": facts or [],
        "emotion": emotion or {"emotion": "平静", "valence": 0.0, "arousal": 0.5, "score": 50},
        "goal_signal": goal_signal, "note": "",
    }


def report_json(loss_type: str = "breakup") -> dict:
    r = {
        "summary": "谢谢你愿意把这些告诉我,我们已经一起梳理了这段关系的脉络。",
        "user_portrait": {"失去类型": "分手", "关系与背景": "初恋", "当前情绪状态": "平静",
                          "情绪波动点": "晚上", "困惑点": "是不是我的错", "未说出口的话": "谢谢你"},
        "object_portrait": {"称呼": "小夏", "关系": "前任", "性格": "温柔",
                            "共同记忆": "咖啡店", "重要地点与时间": "周六"},
        "goal": {"type": "carry_on", "label": "带着记忆走下去", "reason": "想继续往前走"},
        "heal_plan": {"summary": "带着记忆继续生活",
                      "stages": [{"title": "照顾好当下", "desc": "睡好吃好", "time": "本周"}]},
    }
    if loss_type == "breakup":
        r["relationship_analysis"] = {
            "attachment": {"user": "焦虑型", "basis": "", "other": "回避型", "other_basis": ""},
            "causes": [{"factor": "沟通不畅", "side": "双方", "explain": ""}],
            "patterns": "一个追一个逃", "blind_spots": ["学会表达需求"],
            "suggestions": ["多沟通"],
            "narrative": "你们之间一个追一个逃,沟通不畅让误会越积越多。往后可以试着更直接地表达自己的需求。",
        }
    return r


def make_png(w: int = 120, h: int = 120) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return (struct.pack(">I", len(d)) + t + d
                + struct.pack(">I", __import__("zlib").crc32(t + d) & 0xFFFFFFFF))

    import zlib
    row = b"\x00" + bytes((200, 80, 60)) * w
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(row * h)) + chunk(b"IEND", b""))


def make_jpeg(w: int = 100, h: int = 50) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", h, w) + b"\x01\x11\x00"
    return b"\xff\xd8" + sof + b"\xff\xd9"


# =====================================================================
section("1. 危机检测(硬门槛 + 否定守卫)")
# =====================================================================
d3 = crisis_mod.detect("我想自杀,活着没意思了")
check("高危词 → level=3", d3["level"] == 3 and d3["is_crisis"])

d2 = crisis_mod.detect("我撑不下去了,想解脱")
check("中危词 → level=2", d2["level"] == 2 and d2["is_crisis"])

d0 = crisis_mod.detect("我不想死,只是最近有点累")
check("否定守卫(不想死)→ level=0", d0["level"] == 0 and not d0["is_crisis"])

d0b = crisis_mod.detect("今天天气不错")
check("无关内容 → level=0", d0b["level"] == 0 and not d0b["is_crisis"])

r_crisis = companion.chat(db, "C1", "我想自杀", client)
check("陪伴聊天危机短路 → 返回安全文案",
      r_crisis["reply"] == crisis_mod.CRISIS_MESSAGE and r_crisis["emotion"] is None)

# =====================================================================
section("2. 情绪分类(词典兜底)")
# =====================================================================
check("难过", classifier_mod.classify("我很难过", client)["emotion"] == "难过")
check("想念", classifier_mod.classify("我一直怀念那段日子", client)["emotion"] == "想念")
check("愤怒", classifier_mod.classify("我很生气,凭什么这样对我", client)["emotion"] == "愤怒")
check("释怀", classifier_mod.classify("我放下了,祝你幸福", client)["emotion"] == "释怀")
check("孤独", classifier_mod.classify("我一个人很孤独", client)["emotion"] == "孤独")
check("无情绪词 → 其他", classifier_mod.classify("嗯好的", client)["emotion"] == "其他")
check("消极情绪打分偏低",
      classifier_mod.classify("我很难过", client)["score"] < 50)

# =====================================================================
section("3. 语气路由(安抚/引导/旁观者分析)")
# =====================================================================
check("分手求因 + 非急性 → 旁观者分析",
      tone_mod.pick_tone(db, "T1", loss_type="breakup", message="为什么分手,是不是我的错")["tone"] == tone_mod.ANALYZE)
check("分手无求因 + 无数据 → 安抚",
      tone_mod.pick_tone(db, "T2", loss_type="breakup", message="我很难过")["tone"] == tone_mod.SOOTHE)
check("宠物离世无数据 → 安抚",
      tone_mod.pick_tone(db, "T3", loss_type="pet")["tone"] == tone_mod.SOOTHE)

# 亲友离世,均分 >= 60 → 引导
store.get_or_create_user(db, "T4", loss_type="loved_one")
for i in range(3):
    add_mem("T4", f"第{i}天", 65, "平静", days_ago=i)
check("亲友离世 + 均分>=60 → 引导",
      tone_mod.pick_tone(db, "T4")["tone"] == tone_mod.GUIDE)

# 分手,均分 >= 45 → 引导
store.get_or_create_user(db, "T5", loss_type="breakup")
for i in range(3):
    add_mem("T5", f"第{i}天", 50, "平静", days_ago=i)
check("分手 + 均分>=45 → 引导",
      tone_mod.pick_tone(db, "T5")["tone"] == tone_mod.GUIDE)

# 分手求因 + 急性主导 → 安抚(优先级最高)
store.get_or_create_user(db, "T6", loss_type="breakup")
for i in range(3):
    add_mem("T6", f"第{i}天很难过", 35, "难过", days_ago=i, time_tag="晚上")
reflect_mod.reflect(db, "T6")
check("分手求因 + 急性主导 → 安抚",
      tone_mod.pick_tone(db, "T6", message="为什么分手")["tone"] == tone_mod.SOOTHE)

check("归因意图识别", tone_mod.detect_analyze_intent("帮我分析一下这段关系"))
check("非归因意图", not tone_mod.detect_analyze_intent("今天天气不错"))
check("受害者信号(出轨)", "出轨" in tone_mod.detect_victim_signals("他出轨了"))
check("受害者信号(冷暴力)", "冷暴力" in tone_mod.detect_victim_signals("他对我冷暴力"))
check("自述加害(我出轨)→ 非受害者", tone_mod.detect_victim_signals("我出轨了") == [])

# =====================================================================
section("4. 陪伴聊天(普通回复 + 工具循环)")
# =====================================================================
r_norm = companion.chat(db, "C2", "我很难过", FakeClient([
    {"content": "我在听,愿意陪你慢慢说。", "usage": {}, "tool_calls": []},
]))
check("普通回复无工具", r_norm["reply"] == "我在听,愿意陪你慢慢说。" and r_norm["tool"] is None)
check("即时情绪返回", r_norm["emotion"]["emotion"] == "难过")

store.get_or_create_user(db, "C3", loss_type="breakup")
add_mem("C3", "以前周六都一起吃饭", 40, "想念", days_ago=1)
r_rec = companion.chat(db, "C3", "我还是一直想他", FakeClient([
    {"content": "嗯", "usage": {}, "tool_calls": []},
]))
flush_memory_writes()
check("召回记忆 > 0", r_rec["recalled"] > 0)

# keep 未讲过 → 工具
r_keep = companion.chat(db, "C4", "我一直留着那条手链", FakeClient([
    {"content": "", "usage": {}, "tool_calls": [tool_call("suggest_item_ritual", '{"item_name":"那条手链","intent":"keep"}')]},
    {"content": "这条手链一定对你很重要,可以拍下来讲给我听。", "usage": {}, "tool_calls": []},
]))
flush_memory_writes()
check("keep 未讲过 → tool=item_keep",
      (r_keep.get("tool") or {}).get("type") == "item_keep" and (r_keep.get("tool") or {}).get("upload") is True)

# keep 已讲过 → 不再问
store.add_item_memory(db, "C5", item_name="手链", intent="keep", description="他送我的手链",
                      label=None, original_key="item/a.png", cutout_key="item/a_cutout.png")
r_dup = companion.chat(db, "C5", "我又想起那条手链了", FakeClient([
    {"content": "", "usage": {}, "tool_calls": [tool_call("suggest_item_ritual", '{"item_name":"手链","intent":"keep"}')]},
    {"content": "我记得你之前说过这条手链,它还在你心里。", "usage": {}, "tool_calls": []},
]))
flush_memory_writes()
check("keep 已讲过 → tool=None", r_dup.get("tool") is None)

# let_go
r_go = companion.chat(db, "C6", "看到他的旧手机我就难受", FakeClient([
    {"content": "", "usage": {}, "tool_calls": [tool_call("suggest_item_ritual", '{"item_name":"他的旧手机","intent":"let_go"}')]},
    {"content": "可以把它寄存到这里,慢慢松开手。", "usage": {}, "tool_calls": []},
]))
flush_memory_writes()
check("let_go → tool=item_let_go", (r_go.get("tool") or {}).get("type") == "item_let_go")

# =====================================================================
section("5. 物品工具(去重 / 描述简化 / 尺寸解析 / 上传)")
# =====================================================================
check("execute keep 未讲过 → surface=True",
      item_mod.execute_tool(db, "I1", tool_call("suggest_item_ritual", '{"item_name":"手链","intent":"keep"}'))["surface"] is True)
store.add_item_memory(db, "I1", item_name="手链", intent="keep", description="",
                      label=None, original_key="item/x.png", cutout_key="item/x_cutout.png")
check("execute keep 已讲过 → surface=False",
      item_mod.execute_tool(db, "I1", tool_call("suggest_item_ritual", '{"item_name":"那条手链","intent":"keep"}'))["surface"] is False)
check("execute let_go → surface=True",
      item_mod.execute_tool(db, "I2", tool_call("suggest_item_ritual", '{"item_name":"旧手机","intent":"let_go"}'))["surface"] is True)

res_keep = [{"surface": True, "intent": "keep", "item_name": "手链", "item_description": "银色手链,心形吊坠"}]
check("tool_payload 有 surface → 返回字段",
      item_mod.tool_payload(res_keep, "文案")["type"] == "item_keep")
check("tool_payload 透传 item_description(供上传卡片预填)",
      item_mod.tool_payload(res_keep, "文案")["item_description"] == "银色手链,心形吊坠")
check("tool_payload 全 surface=False → None",
      item_mod.tool_payload([{"surface": False}], "文案") is None)

check("短描述不截断", item_mod.simplify_description(client, "短描述") == "短描述")
long_desc = "这是一条很长的手链描述,关于一段很久以前的感情。" * 4
check("mock 下长描述硬截断(≤80)", len(item_mod.simplify_description(client, long_desc)) <= 80)

check("PNG 尺寸解析", item_mod._image_size(make_png(100, 50)) == (100, 50))
check("JPEG 尺寸解析", item_mod._image_size(make_jpeg(100, 50)) == (100, 50))
check("非图片 → None", item_mod._image_size(b"not-an-image") is None)

check("has_item_story 互相包含命中", store.has_item_story(db, "I1", "那条手链"))
check("has_item_story 空名不误判", not store.has_item_story(db, "I1", "   "))


class FakeVision:
    def recognize(self, image):
        return "手链"

    def ground(self, image_url, description):
        return {"label": "手链", "bbox": [0, 0, 1, 1]}

    def verify(self, image_url, description):
        return {"match": True, "reason": ""}


class FakeVisionReject(FakeVision):
    def verify(self, image_url, description):
        return {"match": False, "reason": "图中是闹钟,不是你说的手链"}


class FakeObjStore:
    backend = "local"

    def __init__(self):
        self.deleted: list[str] = []

    def upload(self, key, data, content_type="", prefix=None):
        return f"item/{key}"

    def presigned_url(self, full_key):
        return f"local://{full_key}"

    def ai_crop(self, full_key, box):
        return b"CROP"

    def ai_matte(self, full_key):
        return b"PNG-CUTOUT-BYTES"

    def delete(self, full_key):
        self.deleted.append(full_key)


obj_i3 = FakeObjStore()
_png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 100, 100)
up = item_mod.handle_upload(db, "I3", _png, "", "keep", long_desc,
                            client, vision=FakeVision(), obj_store=obj_i3)
row = store.get_item_memory(db, up["item_id"])
check("上传:识别标签补全 item_name", up["item_name"] == "手链")
check("上传:ItemMemory 落库", row is not None and row.intent == "keep")
check("上传:描述已简化", len(up["description"]) <= 80)
check("上传:记忆流新增 type=item",
      any(m.type == "item" for m in store.list_memories(db, "I3")))
check("上传:校验通过 ok/match=True", up["ok"] is True and up["match"] is True)
check("不存原图:original_key 为空", row.original_key == "")
check("不存原图:返回体无 original_url", "original_url" not in up)
check("不存原图:原图+裁切图已删除、抠图保留", len(obj_i3.deleted) == 2
      and all(not k.endswith("_cutout.png") for k in obj_i3.deleted))

# 看板:抠图 + 描述
items = item_mod.list_items(db, "I3", obj_store=FakeObjStore())
check("看板列出物品", len(items) == 1 and items[0]["item_id"] == up["item_id"])
check("看板含 cutout_url + description",
      bool(items[0]["cutout_url"]) and items[0]["description"] == up["description"])

# 抠图校验不通过:不落库、返回提示
up_bad = item_mod.handle_upload(db, "I4", b"IMAGE-BYTES", "手链", "keep", "他送我的手链",
                                client, vision=FakeVisionReject(), obj_store=FakeObjStore())
check("抠图校验不通过 → ok=False", up_bad["ok"] is False and up_bad["match"] is False)
check("不通过返回提示", bool(up_bad["reason"]))
check("不通过不落库", len(store.list_item_memories(db, "I4")) == 0)

# =====================================================================
section("6. 访谈(维度分叉 / 追问保护 / 目标跳转 / 完成 / 上限 / 补充)")
# =====================================================================
check("分手维度 = 7(含 conflict/attachment)",
      [d["key"] for d in interview._dims_for("breakup")]
      == ["object", "memory", "emotion", "confusion", "conflict", "attachment", "goal"])
check("亲友离世维度 = 5",
      [d["key"] for d in interview._dims_for("loved_one")]
      == ["object", "memory", "emotion", "confusion", "goal"])
check("宠物离世维度 = 5",
      [d["key"] for d in interview._dims_for("pet")]
      == ["object", "memory", "emotion", "confusion", "goal"])

s = interview.start(db, "IV1", loss_type="breakup")
check("start 首发对象维度问题", s["question"] == interview.DIMENSIONS[0]["question"])

# 追问两次后第三次强制进入下一维度
cli = FakeClient(json_script=[
    iv_step("followup", "TA 是什么样的人?"),
    iv_step("followup", "还有想补充的吗?"),
    iv_step("followup", "继续说"),
])
a1 = interview.answer(db, s["session_id"], "她叫小夏,是我初恋", cli)
a2 = interview.answer(db, s["session_id"], "我们在一起四年", cli)
a3 = interview.answer(db, s["session_id"], "她性格很温柔", cli)
check("第 1 轮 → followup", a1["action"] == "followup")
check("第 2 轮 → followup", a2["action"] == "followup")
check("第 3 轮强制 → next 且进入「共同的记忆」",
      a3["action"] == "next" and a3["dimension"] == "共同的记忆")

# goal_signal 跳转:早期表达目标 → 直接跳到目标维度
s2 = interview.start(db, "IV2", loss_type="breakup")
cli2 = FakeClient(json_script=[iv_step("followup", "好", goal_signal=True)])
a_jump = interview.answer(db, s2["session_id"], "我想带着这段记忆继续往前走", cli2)
check("goal_signal → 跳到目标维度", a_jump["action"] == "next" and "更希望" in a_jump["question"])

# 完成:生成报告 + 落画像 + 会话 completed
s3 = interview.start(db, "IV3", loss_type="breakup")
cli3 = FakeClient(json_script=[iv_step("complete"), report_json("breakup")])
a_done = interview.answer(db, s3["session_id"], "想带着记忆继续生活", cli3)
check("complete → 返回报告", a_done["action"] == "complete" and a_done["report"]["goal"]["type"] == "carry_on")
check("画像已写入", store.get_portrait(db, "IV3", "user").get("失去类型") == "分手")
check("分手复盘入画像", "关系复盘" in store.get_portrait(db, "IV3", "user"))
check("会话状态 completed", store.get_interview(db, s3["session_id"]).status == "completed")

# 非分手完成(无 relationship_analysis)
s3b = interview.start(db, "IV3B", loss_type="pet")
cli3b = FakeClient(json_script=[iv_step("complete"), report_json("pet")])
a_done_b = interview.answer(db, s3b["session_id"], "很想念我的猫", cli3b)
check("宠物离世 complete", a_done_b["action"] == "complete")

# MAX_TURNS 保护:历史塞满 19 轮用户发言后,再答一轮即 complete
state19 = {"dimension_idx": 0, "followup_count": 0, "asked": ["q0"], "covered": ["object"],
           "facts": [], "history": [{"role": "assistant", "content": "q0"}], "done": False, "report": None}
for i in range(19):
    state19["history"].append({"role": "user", "content": f"回答{i}"})
    state19["history"].append({"role": "assistant", "content": f"追问{i}"})
sid_max = store.create_interview(db, "IV4", "breakup", state19).id
cli4 = FakeClient(json_script=[iv_step("followup"), report_json("breakup")])
a_max = interview.answer(db, sid_max, "第20次回答", cli4)
check("MAX_TURNS → complete", a_max["action"] == "complete")

# revise 人机协同
cli5 = FakeClient(json_script=[report_json("breakup")])
rev = interview.revise(db, s3["session_id"], "其实我还有很多不舍", cli5)
check("revise 重新生成报告", isinstance(rev, dict) and "summary" in rev)

# =====================================================================
section("6b. 引导阶段 + 初始报告 + 异步报告")
# =====================================================================
# 轻量状态机
check("新用户阶段 = new", onboarding.get_phase(db, "OB1") == "new")
onboarding.transition(db, "OB1", "interview")
check("new → interview", onboarding.get_phase(db, "OB1") == "interview")
onboarding.transition(db, "OB1", "interview")  # 幂等
check("幂等重复 interview", onboarding.get_phase(db, "OB1") == "interview")
onboarding.transition(db, "OB1", "report")
try:
    onboarding.transition(db, "OB1", "interview")
    check("非法回退 report→interview 被拒", False)
except ValueError:
    check("非法回退 report→interview 被拒", True)
onboarding.transition(db, "OB1", "main")
check("report → main", onboarding.get_phase(db, "OB1") == "main")

# 同步报告落看板 + 用户视图(复用 IV3 已完成的分手报告)
check("complete 后阶段 = report", onboarding.get_phase(db, "IV3") == "report")
saved = store.get_latest_report(db, "IV3", "initial")
check("初始报告已落 reports 表", saved is not None and bool(saved.get("summary")))
view = onboarding.initial_report_view(saved)
check("用户视图含 title/keywords/summary/quote + 关系分析",
      set(view.keys()) == {"title", "keywords", "summary", "quote", "relationship_analysis"})
check("用户视图不暴露画像/计划",
      "user_portrait" not in view and "goal" not in view and "heal_plan" not in view)

# 异步报告:answer 即时返回 + 后台落库 + report_ready
_ar.LLMClient = lambda: FakeClient(json_script=[report_json("breakup")])
s_async = interview.start(db, "OB-ASYNC", loss_type="breakup")
cli_async = FakeClient(json_script=[iv_step("complete")])
a_async = interview.answer(db, s_async["session_id"], "想带着记忆继续生活", cli_async, async_report=True)
check("async complete 即时返回 generating",
      a_async["action"] == "complete" and a_async.get("generating") is True)
check("async 立即推进 report 阶段", onboarding.get_phase(db, "OB-ASYNC") == "report")
_ar.flush_reports()
check("异步报告落 reports 表", store.get_latest_report(db, "OB-ASYNC", "initial") is not None)
sess_async = store.get_interview(db, s_async["session_id"])
check("interview 状态 report_ready", interview.progress(sess_async)["report_ready"] is True)

# =====================================================================
section("7. 每日主题 + 启发文案")
# =====================================================================
store.get_or_create_user(db, "D-B", loss_type="breakup")
store.get_or_create_user(db, "D-L", loss_type="loved_one")
store.get_or_create_user(db, "D-P", loss_type="pet")

tb = daily.get_themes(db, "D-B")
tl = daily.get_themes(db, "D-L")
check("每日主题数量 = daily_theme_count", len(tb["themes"]) == Settings().daily_theme_count)
check("分手用户主题池含 insight/growth 可能",
      all(t["key"] in {x["key"] for x in daily.THEMES} for t in tb["themes"]))
check("亲友离世不含分手专属主题",
      all(t["key"] not in ("insight", "growth") for t in tl["themes"]))
check("主题返回 reason + tone", bool(tb["reason"]) and tb["tone"] in (tone_mod.SOOTHE, tone_mod.GUIDE))

op = daily.generate_opening(db, "D-B")
check("启发文案非空且落库",
      bool(op["opening"]) and store.get_daily_pick(db, "D-B", daily._today_key()) is not None)
check("启发文案返回心情/语气字段", "mood" in op and "tone" in op)

# 主界面聚合:一次返回三要素
home = home_mod.get_home(db, "D-B", client)
check("home 聚合三要素", set(home.keys()) == {"calendar", "nudges", "themes"})
check("home 日历整月", len(home["calendar"]["days"]) == 31)
check("home 主题数量", len(home["themes"]["themes"]) == Settings().daily_theme_count)

# =====================================================================
section("8. 软引导(深夜/情绪节点/去重/语录)")
# =====================================================================
def L(y, mo, d, h, mi=0):  # 本地时间 → UTC
    return datetime(y, mo, d, h, mi) - timedelta(hours=8)


# 深夜触发(无夜间节点)
store.get_or_create_user(db, "N1", loss_type="breakup")
n1 = nudge.get_nudges(db, "N1", client, now=L(2026, 8, 28, 23, 30))
check("深夜 → rule_key=late_night",
      any(x["rule_key"] == "late_night" and x["type"] == "time" for x in n1["nudges"]))

# 情绪节点触发(frequency >= 2)
store.get_or_create_user(db, "N2", loss_type="breakup")
db.add(EmotionNode(user_id="N2", trigger="晚上", emotion="孤独", frequency=2, time_tag="晚上"))
db.commit()
n2 = nudge.get_nudges(db, "N2", client, now=L(2026, 8, 28, 22, 0))
check("情绪节点 → rule_key=emotion:晚上",
      any(x["rule_key"] == "emotion:晚上" and x["type"] == "emotion" for x in n2["nudges"]))

# 深夜且已有夜间节点 → 不重复深夜提醒
store.get_or_create_user(db, "N3", loss_type="breakup")
db.add(EmotionNode(user_id="N3", trigger="晚上", emotion="孤独", frequency=2, time_tag="晚上"))
db.commit()
n3 = nudge.get_nudges(db, "N3", client, now=L(2026, 8, 28, 23, 30))
check("深夜 + 夜间节点 → 只出情绪节点、不出 late_night",
      any(x["rule_key"] == "emotion:晚上" for x in n3["nudges"])
      and all(x["rule_key"] != "late_night" for x in n3["nudges"]))

# 去重:同日同规则只推一次,第二次落到语录兜底
n2b = nudge.get_nudges(db, "N2", client, now=L(2026, 8, 28, 22, 10))
check("同日再次触发 → 落到 quote 兜底", any(x["rule_key"] == "quote" for x in n2b["nudges"]))

# 无任何触发 → 语录兜底
store.get_or_create_user(db, "N4", loss_type="breakup")
n4 = nudge.get_nudges(db, "N4", client, now=L(2026, 8, 28, 10, 0))
check("无触发 → quote", any(x["rule_key"] == "quote" for x in n4["nudges"]))

# =====================================================================
section("9. 定期跟踪报告(资格 / 阶段选卡 / 上限 / 对比)")
# =====================================================================
check("新用户无资格", report.report_eligibility(db, "R-FRESH")["eligible"] is False)

# 低谷期(分手,持续低迷)
store.get_or_create_user(db, "R-LOW", loss_type="breakup")
store.upsert_portrait(db, "R-LOW", "object", {"称呼": "小夏", "性格": "温柔"})
seq = [("难过", 35), ("想念", 40), ("焦虑", 38), ("难过", 32), ("想念", 42)]
for i in range(20):
    emo, sc = seq[i % 5]
    add_mem("R-LOW", f"第{i}天,又想起小夏", sc, emo, days_ago=i,
            time_tag="晚上" if i % 2 == 0 else None)
check("低谷用户资格通过", report.report_eligibility(db, "R-LOW")["eligible"] is True)
r_low = report.build_report(db, "R-LOW", client)
check("低谷 → stage=0", r_low["state"]["stage"] == 0)
check("总结卡永远第一", r_low["cards"][0]["key"] == "summary")
check("低谷看不到释怀/转变卡",
      all(c["key"] not in ("first_reconcile", "emotion_shift") for c in r_low["cards"]))
check("低谷有倾诉次数卡", any(c["key"] == "total_turns" for c in r_low["cards"]))

# 和解期(亲友离世,先低落后平静/释怀)
store.get_or_create_user(db, "R-HEALED", loss_type="loved_one", goal="carry_on")
store.upsert_portrait(db, "R-HEALED", "object", {"称呼": "奶奶", "性格": "慈祥"})
for i in range(18):
    days_ago = 17 - i
    if i < 4:
        emo, sc = "难过", 40
    elif i % 2 == 0:
        emo, sc = "平静", 62
    else:
        emo, sc = "释怀", 68
    add_mem("R-HEALED", f"第{i}天,关于奶奶", sc, emo, days_ago=days_ago,
            time_tag="下午", place_tag="家里")
r_healed = report.build_report(db, "R-HEALED", client)
check("和解用户 stage >= 3", r_healed["state"]["stage"] >= 3)
check("和解看到第一次释怀卡", any(c["key"] == "first_reconcile" for c in r_healed["cards"]))
check("和解看到情绪转变卡", any(c["key"] == "emotion_shift" for c in r_healed["cards"]))
check("卡片数量 <= report_max_cards", len(r_healed["cards"]) <= Settings().report_max_cards)

# 数据不足 → build_report 不生成
r_short = report.build_report(db, "R-FRESH", client)
check("数据不足 → eligible=False 且 cards=[]",
      r_short["eligible"] is False and r_short["cards"] == [])

# 对比上次(有历史快照)
store.get_or_create_user(db, "R-CMP", loss_type="breakup")
for i in range(15):
    add_mem("R-CMP", f"第{i}天", 50, "平静", days_ago=i)
store.upsert_portrait(db, "R-CMP", "object", {"称呼": "小夏"})
now = datetime.now().replace(microsecond=0)
prev_key = (now + timedelta(hours=8) - timedelta(days=20)).strftime("%Y-%m-%d")
store.upsert_state_snapshot(db, "R-CMP", prev_key, {
    "stage": 0, "stage_label": "低谷期", "baseline": 35.0, "trend": 0.0,
    "volatility": 5.0, "acute_ratio": 0.6, "calm_ratio": 0.0, "reconcile": 10.0,
    "risk": 0.6, "n_days": 15, "n_memories": 15,
})
r_cmp = report.build_report(db, "R-CMP", client, now=now)
check("对比上次 → compared 非空且 prev_stage 正确",
      r_cmp["compared"] is not None and r_cmp["compared"]["prev_stage"] == "低谷期")
check("阶段上升判定", r_cmp["compared"]["stage_up"] is True)

# =====================================================================
section("9b. 周报(week_key / due 弹窗 / 异步生成 / 已看去重)")
# =====================================================================
check("week_key 为 ISO 周格式", "-W" in weekly_mod.week_key() and weekly_mod.week_key().startswith("20"))

# 引导未完成(非 main)不弹
store.get_or_create_user(db, "W-NEW", loss_type="breakup")
w_new = weekly_mod.due(db, "W-NEW")
check("引导未完成 → 不弹", w_new["due"] is False and "引导" in w_new["reason"])

# main 阶段但数据不足 → 不弹
store.get_or_create_user(db, "W-THIN", loss_type="breakup")
onboarding.set_phase(db, "W-THIN", "main")
check("main 但数据不足 → 不弹", weekly_mod.due(db, "W-THIN")["due"] is False)

# main 阶段 + 数据充足 → 后台生成,生成后未看即弹完整报告
store.get_or_create_user(db, "W-RICH", loss_type="breakup")
store.upsert_portrait(db, "W-RICH", "object", {"称呼": "小夏"})
for i in range(20):
    add_mem("W-RICH", f"第{i}天,又想起小夏", 50, "难过", days_ago=i)
onboarding.set_phase(db, "W-RICH", "main")
w_rich = weekly_mod.due(db, "W-RICH")
check("main + 数据充足 → 触发生成", w_rich["due"] is True and w_rich["generating"] is True)
wk = w_rich["week_key"]
weekly_mod.flush()
w_get = weekly_mod.get(db, "W-RICH", wk)
check("生成后 get 返回完整报告", w_get is not None and bool(w_get["report"]["cards"]))
check("生成后 due 未看 → 直接返回报告", weekly_mod.due(db, "W-RICH")["report"] is not None)
check("看板列表含本周", any(r["week_key"] == wk for r in weekly_mod.list_reports(db, "W-RICH")))
check("mark_seen → ok", weekly_mod.mark_seen(db, "W-RICH", wk)["ok"] is True)
check("已看后 due → 不弹", weekly_mod.due(db, "W-RICH")["due"] is False)

# =====================================================================
section("10. 树洞信箱(写信/回信/匹配/审核)")
# =====================================================================
check("新用户写信无资格", treehole.write_eligibility(db, "T-FRESH")["eligible"] is False)

store.get_or_create_user(db, "T-A", loss_type="loved_one")
for i in range(3):
    add_mem("T-A", f"第{i}天的倾诉", 50, "难过", days_ago=i)
store.save_chat_turn(db, "T-A", "sess-a", "第一句倾诉", "回复一")
store.save_chat_turn(db, "T-A", "sess-b", "第二句倾诉", "回复二")
elig_a = treehole.write_eligibility(db, "T-A")
check("参与 2 次聊天 → 可写信", elig_a["eligible"] is True and elig_a["chat_sessions"] >= 2)

r_w = treehole.write_letter(db, "T-A", "奶奶走后,我总觉得心里空了一块。", client)
check("写信成功", r_w["ok"] is True and r_w["letter_id"])
letter_a = r_w["letter_id"]

r_w2 = treehole.write_letter(db, "T-A", "又想说点什么", client)
check("二次写信被拒", r_w2["ok"] is False and "已经写过" in r_w2["reason"])

store.get_or_create_user(db, "T-PII", loss_type="breakup")
for i in range(3):
    add_mem("T-PII", f"第{i}天", 50, "难过", days_ago=i)
store.save_chat_turn(db, "T-PII", "sess-a", "第一句倾诉", "回复一")
store.save_chat_turn(db, "T-PII", "sess-b", "第二句倾诉", "回复二")
r_pii = treehole.write_letter(db, "T-PII", "我电话是13800138000,想找人聊聊", client)
check("写信含手机号被 PII 拦截", r_pii["ok"] is False and "敏感" in r_pii["reason"])

# 回信资格:稳定用户(已结束陪伴 + 情绪平稳 → 可回信)
store.get_or_create_user(db, "T-B", loss_type="loved_one")
for i in range(8):
    add_mem("T-B", f"第{i}天,慢慢接受", 60, "平静", days_ago=i)
archive_companion_of("T-B")
elig_b = treehole.reply_eligibility(db, "T-B")
check("稳定用户可回信", elig_b["eligible"] is True and elig_b["avg_score_7d"] >= 55)

# 回信资格:近期急性情绪(已结束陪伴但仍难受 → 暂不可回信)
store.get_or_create_user(db, "T-ACUTE", loss_type="loved_one")
for i in range(8):
    emo, sc = ("难过", 40) if i == 0 else ("平静", 60)
    add_mem("T-ACUTE", f"第{i}天", sc, emo, days_ago=i)
archive_companion_of("T-ACUTE")
elig_ac = treehole.reply_eligibility(db, "T-ACUTE")
check("近期急性情绪 → 暂不可回信", elig_ac["eligible"] is False and elig_ac["acute_recent"])

# 回信资格:近 7 天倾诉不足(已结束陪伴)
store.get_or_create_user(db, "T-SPARSE", loss_type="loved_one")
for i in range(8):
    add_mem("T-SPARSE", f"第{i}天", 60, "平静", days_ago=8 + i)
archive_companion_of("T-SPARSE")
check("近 7 天倾诉不足 → 不可回信", treehole.reply_eligibility(db, "T-SPARSE")["eligible"] is False)

# 回信资格:累积天数不足(已结束陪伴)
store.get_or_create_user(db, "T-NEW", loss_type="loved_one")
for i in range(2):
    add_mem("T-NEW", f"第{i}天", 60, "平静", days_ago=i)
archive_companion_of("T-NEW")
check("累积天数不足 → 不可回信", treehole.reply_eligibility(db, "T-NEW")["eligible"] is False)

# 回信资格:情绪平稳但尚未结束陪伴 → 不邀请(回信只在「已结束陪伴」后开放)
store.get_or_create_user(db, "T-ACTIVE", loss_type="loved_one")
for i in range(8):
    add_mem("T-ACTIVE", f"第{i}天", 60, "平静", days_ago=i)
elig_act = treehole.reply_eligibility(db, "T-ACTIVE")
check("未结束陪伴 → 不邀请回信", elig_act["eligible"] is False and elig_act["ended"] is False)

# 匹配:同 loss_type
m = treehole.get_matches(db, "T-B", client)
check("匹配到同经历的信", len(m["matches"]) >= 1 and m["matches"][0]["letter_id"] == letter_a)

# 无匹配
store.get_or_create_user(db, "T-PET", loss_type="pet")
check("宠物用户暂无匹配", treehole.get_matches(db, "T-PET", client)["matches"] == [])

# 回信
rp = treehole.submit_reply(db, "T-B", letter_a, "我也失去过很重要的人,慢慢来,你没有忘记。", client)
check("回信成功且待审", rp["ok"] is True and rp["status"] == "pending_review")
rid = rp["reply_id"]

rp_self = treehole.submit_reply(db, "T-A", letter_a, "给自己的回信", client)
check("不能回复自己的信", rp_self["ok"] is False)

rp_pii = treehole.submit_reply(db, "T-B", letter_a, "加我微信 abc12345 详聊", client)
check("回信含联系方式被拦截", rp_pii["ok"] is False)

pending = treehole.review_pending(db)
check("待审队列含该回信", any(p["reply_id"] == rid for p in pending))

check("审批通过 → delivered", treehole.approve_reply(db, rid)["status"] == "delivered")
check("通过后待审队列清空", all(p["reply_id"] != rid for p in treehole.review_pending(db)))

rp2 = treehole.submit_reply(db, "T-B", letter_a, "第二条回信", client)
check("审批拒绝 → rejected", treehole.reject_reply(db, rp2["reply_id"])["status"] == "rejected")

# =====================================================================
section("10b. 树洞弹窗(写信/回信邀请/收到回信) + 看板")
# =====================================================================
# 写信弹窗:有写信资格且尚未看过 → 弹 write;标记后不再弹
store.get_or_create_user(db, "P-WRITE", loss_type="loved_one")
for i in range(3):
    add_mem("P-WRITE", f"第{i}天", 50, "难过", days_ago=i)
store.save_chat_turn(db, "P-WRITE", "sess-a", "第一句倾诉", "回复一")
store.save_chat_turn(db, "P-WRITE", "sess-b", "第二句倾诉", "回复二")
pop_w = treehole.popups(db, "P-WRITE", client)
check("写信资格 → 弹 write", any(p["kind"] == "write" for p in pop_w["popups"]))
treehole.mark_popup_seen(db, "P-WRITE", "write")
check("已看 write → 不再弹 write",
      all(p["kind"] != "write" for p in treehole.popups(db, "P-WRITE", client)["popups"]))

# 回信邀请弹窗:回信资格 + 有可回的来信 → 弹 reply_invite
store.get_or_create_user(db, "P-REPLY", loss_type="loved_one")
for i in range(8):
    add_mem("P-REPLY", f"第{i}天,慢慢接受", 60, "平静", days_ago=i)
pop_r = treehole.popups(db, "P-REPLY", client)
check("回信资格 + 有来信 → 弹 reply_invite",
      any(p["kind"] == "reply_invite" for p in pop_r["popups"]))
treehole.mark_popup_seen(db, "P-REPLY", "reply_invite")
check("已看 reply_invite → 不再弹 reply_invite",
      all(p["kind"] != "reply_invite" for p in treehole.popups(db, "P-REPLY", client)["popups"]))

# 收到回信弹窗:T-A 写的信被 T-B 回信并已送达(section 10 已 approve)
pop_a = treehole.popups(db, "T-A", client)
recv = [p for p in pop_a["popups"] if p["kind"] == "reply_received"]
check("原用户收到已送达回信 → 弹 reply_received", len(recv) >= 1)
check("回信弹窗带 reply_id + 内容", recv[0]["data"]["reply_id"] and recv[0]["data"]["content"])
treehole.mark_popup_seen(db, "T-A", "reply_received", recv[0]["data"]["reply_id"])
check("已看回信 → 不再弹 reply_received",
      all(p["kind"] != "reply_received" for p in treehole.popups(db, "T-A", client)["popups"]))

# 看板:我的信 + 收到的回信
letters = treehole.my_letters(db, "T-A")
check("看板返回我的信", len(letters) == 1 and letters[0]["letter_id"] == letter_a)
check("看板信含已送达回信", len(letters[0]["replies"]) >= 1)
# 看板:我写给他人的回信
replies = treehole.my_replies(db, "T-B")
check("看板返回我写的回信", any(r["reply_id"] == rid for r in replies))

# 运营后台:看所有来信 + 官方回信(直达,免审核)
admin_letters = treehole.admin_letters(db)
check("运营看到所有来信", any(L["letter_id"] == letter_a for L in admin_letters))
check("来信含 reply_count", any(L["letter_id"] == letter_a and L["reply_count"] >= 1 for L in admin_letters))
a_op = treehole.admin_reply(db, letter_a, "我是树洞的陪伴者,收到你的信了。", client)
check("运营回信直达 delivered", a_op["ok"] is True and a_op["status"] == "delivered")
check("运营回信 source=operator", a_op["source"] == "operator")
a_pii = treehole.admin_reply(db, letter_a, "加我微信 abc12345 详聊", client)
check("运营回信含联系方式被拦截", a_pii["ok"] is False)
check("看板含官方回信 source=operator",
      any(r["source"] == "operator" for r in treehole.my_letters(db, "T-A")[0]["replies"]))

# =====================================================================
section("10c. 有效 loss_type:关系类型(真源)映射 → 树洞匹配")
# =====================================================================
# 前端只写 user_states.relationship_type,遗留 users.loss_type 长期为 None;消费方须从真源映射取 loss_type。
check("effective_loss_type:遗留 loss_type 回落(T-A=loved_one)",
      store.effective_loss_type(db, "T-A") == "loved_one")
store.get_or_create_user(db, "RT-REL")
store.update_user_state(db, "RT-REL", relationship_type="relative")
check("effective_loss_type:relative → loved_one(无遗留)",
      store.effective_loss_type(db, "RT-REL") == "loved_one")
store.get_or_create_user(db, "RT-PET")
store.update_user_state(db, "RT-PET", relationship_type="pet")
check("effective_loss_type:pet → pet(无遗留)",
      store.effective_loss_type(db, "RT-PET") == "pet")
# 树洞匹配按真源关系类型过滤:宠物用户(关系类型=pet,无遗留 loss_type)不应匹配到分手/亲友来信
m_pet_rt = treehole.get_matches(db, "RT-PET", client)
check("关系类型=pet 不匹配分手/亲友来信", m_pet_rt["matches"] == [])

# =====================================================================
section("11. 内容安全 scan_pii(正则硬匹配)")
# =====================================================================
check("手机号", not moderation.scan_pii("我电话13800138000", client)["clean"])
check("座机号", not moderation.scan_pii("打010-12345678", client)["clean"])
check("邮箱", not moderation.scan_pii("联系 a@b.com", client)["clean"])
check("身份证", not moderation.scan_pii("号 110101199001011234", client)["clean"])
check("社交账号", not moderation.scan_pii("微信 abc12345", client)["clean"])
check("URL", not moderation.scan_pii("看 https://example.com/x", client)["clean"])
check("地址", not moderation.scan_pii("我住北京市朝阳区建国路88号", client)["clean"])
check("干净文本通过", moderation.scan_pii("奶奶走后我心里空空的", client)["clean"])

# =====================================================================
section("12. 记忆层(状态 / 记忆点 / 反思 / 召回 / 日历 / 抽取)")
# =====================================================================
store.get_or_create_user(db, "M1", loss_type="breakup")
for i in range(4):
    add_mem("M1", f"第{i}天的倾诉", 50, "平静", days_ago=i, time_tag="晚上", place_tag="家里")
st = state_mod.compute_state(db, "M1")
check("compute_state 字段齐全", st["baseline"] is not None and st["stage_label"] in state_mod.STAGES)
check("active_days", state_mod.active_days(db, "M1") == 4)
check("longest_streak", state_mod.longest_streak(db, "M1") == 4)
check("night_count", state_mod.night_count(db, "M1") == 4)
check("top_tags(晚上/家里)", state_mod.top_tags(db, "M1") == ("晚上", "家里"))

store.get_or_create_user(db, "M2", loss_type="breakup")
store.upsert_portrait(db, "M2", "object", {"称呼": "小夏"})
add_mem("M2", "我又想起小夏了", 40, "想念", days_ago=1)
add_mem("M2", "我释怀了,小夏", 66, "释怀", days_ago=0)
check("first_reconcile 命中", state_mod.first_reconcile(db, "M2") is not None)
check("mention_count 统计称呼", state_mod.mention_count(db, "M2") == 4)
check("saddest_day 有值", state_mod.saddest_day(db, "M2") is not None)

re = reflect_mod.reflect(db, "M1")
check("reflect 重建情绪节点", len(re["nodes"]) >= 1 and re["count"] == 4)

rec = recall_mod.recall(db, "M1", "晚上 家里")
check("recall 返回结果", len(rec) >= 1)

cal = calendar_mod.get_calendar(db, "M2", month="2026-08")
check("日历覆盖整月", len(cal["days"]) == 31)
check("日历有数据日", any(d["score"] is not None for d in cal["days"]))

p, t = extract_mod.extract_tags("我在咖啡店,晚上很难过")
check("extract_tags(咖啡店/晚上)", p == "咖啡店" and t == "晚上")
turn = extract_mod.extract_turn(client, "我很难过")
check("extract_turn mock 抽取", turn["emotion"]["emotion"] == "难过" and turn["summary"])

# =====================================================================
section("13. 网关(parse_json / speech mock / LLM mock)")
# =====================================================================
check("parse_json 代码块", parse_json("```json\n{\"a\":1}\n```")[0] == {"a": 1})
check("parse_json 裸 JSON", parse_json('{"a": 2}')[0] == {"a": 2})
check("parse_json 非 JSON", parse_json("hello")[0] is None)
check("parse_json 空串", parse_json("") == (None, ""))

sp = SpeechClient(settings=Settings(mock_speech=True))
check("TTS mock", sp.tts("你好")["mock"] is True)
check("ASR mock", sp.transcribe("http://x")["mock"] is True)

llm_mock = LLMClient(settings=Settings(mock_llm=True))
check("LLM mock 返回占位", llm_mock.chat([{"role": "user", "content": "hi"}])["tool_calls"] == [])
check("LLM 无 key 自动 mock",
      LLMClient(settings=Settings(mock_llm=False, llm_api_key="")).mock is True)

# =====================================================================
section("14. 聊天历史(落库 / 游标分页 / 跳转某天 / 批量删除)")
# =====================================================================
# 落库:普通对话写 user + assistant 两条
companion.chat(db, "H1", "今天有点想他", client)
p_h1 = history_mod.page(db, "H1")
check("chat 落库 user+assistant 两条",
      len(p_h1["messages"]) == 2
      and p_h1["messages"][0]["role"] == "user"
      and p_h1["messages"][1]["role"] == "assistant")
check("消息 ts 精确到分钟(无秒)", p_h1["messages"][0]["ts"].count(":") == 1)

# 危机短路路径也落库
companion.chat(db, "H1-CRISIS", "我想自杀", client)
p_c = history_mod.page(db, "H1-CRISIS")
check("危机短路也落库两条", len(p_c["messages"]) == 2
      and p_c["messages"][1]["content"] == crisis_mod.CRISIS_MESSAGE)

# 游标分页:造 6 轮 = 12 条
for i in range(6):
    store.save_chat_turn(db, "H2", None, f"问{i}", f"答{i}")
p_latest = history_mod.page(db, "H2", limit=5)
ids_latest = [m["id"] for m in p_latest["messages"]]
check("最新一页 5 条 + has_older=True + has_newer=False",
      len(ids_latest) == 5 and p_latest["has_older"] is True and p_latest["has_newer"] is False)
check("最新一页为最近 5 条且升序",
      ids_latest == sorted(ids_latest) and p_latest["messages"][-1]["content"] == "答5")

p_older = history_mod.page(db, "H2", before_id=p_latest["cursor_oldest_id"], limit=5)
check("上滑加载更早 5 条 + has_newer=True",
      len(p_older["messages"]) == 5 and p_older["has_newer"] is True)
check("上滑结果全在游标之前", all(m["id"] < p_latest["cursor_oldest_id"] for m in p_older["messages"]))

p_newer = history_mod.page(db, "H2", after_id=p_older["cursor_newest_id"], limit=3)
check("下滑加载更新 3 条", len(p_newer["messages"]) == 3
      and all(m["id"] > p_older["cursor_newest_id"] for m in p_newer["messages"]))


def _seed_chat(uid, role, content, ts):
    db.add(ChatMessage(user_id=uid, session_id="", role=role, content=content, ts=ts))
    db.commit()


# 跳转到某天第一条(本地日 = UTC + 8h)
_seed_chat("H3", "user", "25号的消息", datetime(2026, 8, 25, 10, 0))
_seed_chat("H3", "user", "26号的问候", datetime(2026, 8, 26, 10, 0))
_seed_chat("H3", "assistant", "26号的回应", datetime(2026, 8, 26, 10, 0))
_seed_chat("H3", "user", "27号的消息", datetime(2026, 8, 27, 10, 0))
_seed_chat("H3", "assistant", "27号的回应", datetime(2026, 8, 27, 10, 0))

p_jump = history_mod.page(db, "H3", date="2026-08-26")
check("跳转某天 → anchor 为当天第一条",
      p_jump["anchor"] is not None and p_jump["anchor"]["date"] == "2026-08-26"
      and p_jump["anchor"]["content"] == "26号的问候")
check("跳转后从当天第一条向下返回",
      p_jump["messages"][0]["content"] == "26号的问候"
      and p_jump["has_older"] is True and p_jump["has_newer"] is False)

p_gap = history_mod.page(db, "H3", date="2026-08-24")
check("无消息日期回落最近一天", p_gap["anchor"] is not None
      and p_gap["anchor"]["date"] == "2026-08-25")

p_after = history_mod.page(db, "H3", date="2026-08-30")
check("跳转超过最后一天 → 回落最新一页", p_after["anchor"] is None
      and len(p_after["messages"]) >= 1)

# 批量删除 + 越权保护
all_h2 = history_mod.page(db, "H2", limit=100)["messages"]
ids_h2 = [m["id"] for m in all_h2]
check("H2 共 12 条", len(ids_h2) == 12)
check("批量删除 3 条", history_mod.delete_many(db, "H2", ids_h2[:3])["deleted"] == 3)
check("删除后剩余 9 条", len(history_mod.page(db, "H2", limit=100)["messages"]) == 9)
check("越权删除他人消息 → 0", history_mod.delete_many(db, "H-OTHER", ids_h2[3:5])["deleted"] == 0)

# =====================================================================
print()
print("=" * 64)
total = len(_PASS) + len(_FAIL)
print(f"白盒测试完成:通过 {len(_PASS)} / 共 {total}")
if _FAIL:
    print("失败项:")
    for name in _FAIL:
        print(f"  - {name}")
print("=" * 64)

db.close()
engine.dispose()
try:
    os.remove(_tmp.name)
except OSError:
    pass
sys.exit(0 if not _FAIL else 1)
