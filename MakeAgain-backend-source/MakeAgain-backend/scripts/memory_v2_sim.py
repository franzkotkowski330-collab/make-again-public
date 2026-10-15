"""记忆系统 v2 改造的离线白盒验证(确定性,全 mock)。

覆盖本批次改动:
1. facts 消费(facts_text / facts_for_context / recall 命中 facts / context_block 渲染)
2. 临时会话上下文(append/get 顺序、截断、轮数、TTL 过期、clear)
3. reflect 增量(upsert_emotion_node 累加 + reflect 全量幂等)
4. 时间要素(extract_turn 提取 kind="time" 事实)
5. 画像增量合并(mock 跳过 / 触发合并填充占位 / 保护已确认字段)

运行(在 backend 目录下):
    python scripts/memory_v2_sim.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from agent import companion  # noqa: E402
from config import Settings  # noqa: E402
from gateway.client import LLMClient  # noqa: E402
from memory import extract, facts, portrait, recall, reflect, session_context as _sc, store  # noqa: E402
from memory.db import Base  # noqa: E402
from memory.models import utcnow  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
engine = create_engine(f"sqlite:///{_tmp.name.replace(chr(92), '/')}",
                       connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

import memory.async_write as _aw  # noqa: E402
_aw.SessionLocal = S
_aw.LLMClient = lambda: LLMClient(settings=Settings(mock_llm=True))

db = S()

_results: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    _results.append((name, bool(cond)))
    print(f"[{'OK' if cond else 'FAIL'}] {name}")


class FakeClient:
    """按脚本顺序返回 chat_json 结果(每次 pop 一条),其余走空实现。"""

    def __init__(self, json_script=None, mock: bool = False):
        self.settings = Settings(mock_llm=True)
        self.mock = mock
        self.json_script = list(json_script or [])

    def chat(self, messages, temperature=0.7, max_tokens=None, model=None, tools=None):
        return {"content": "", "usage": {}, "tool_calls": []}

    def chat_json(self, messages, temperature=0.2, max_tokens=None, model=None):
        if self.json_script:
            return self.json_script.pop(0), {"usage": {}, "raw": ""}
        return None, {"usage": {}, "raw": ""}


# =====================================================================
print("== 1. facts 消费 ==")
# =====================================================================
f = [
    {"kind": "user", "fact": "她叫小夏", "confidence": 0.9},
    {"kind": "time", "fact": "去年生日", "confidence": 0.8},
    {"kind": "memory", "fact": "她一路小跑过来", "confidence": 0.5},
    {"kind": "object", "fact": "小夏", "confidence": 0.9},
]
check("facts_text 拼接", facts.facts_text(f) == "她叫小夏 去年生日 她一路小跑过来 小夏")
check("facts_for_context 高置信+截断(默认 min_conf=0.6, max_n=2)",
      facts.facts_for_context(f) == ["她叫小夏", "去年生日"])
check("facts_for_context kinds 过滤", facts.facts_for_context(f, kinds={"time"}) == ["去年生日"])
check("facts_for_context 去重",
      facts.facts_for_context([{"kind": "user", "fact": "小夏", "confidence": 0.9},
                               {"kind": "object", "fact": "小夏", "confidence": 0.9}], min_conf=0.0) == ["小夏"])

store.get_or_create_user(db, "RECALL-U")
T = utcnow() - timedelta(days=1)
a = store.add_memory(db, "RECALL-U", content="今天天气不错", summary="天气不错",
                     facts=[{"kind": "object", "fact": "汤圆是一只猫", "confidence": 0.9}],
                     emotion={"score": 50, "emotion": "平静"}, importance=5.0)
b = store.add_memory(db, "RECALL-U", content="随便聊聊", summary="随便聊",
                     facts=[], emotion={"score": 50, "emotion": "平静"}, importance=5.0)
a.ts = T
b.ts = T
db.commit()
res = recall.recall(db, "RECALL-U", "汤圆")
check("recall 仅凭 facts 命中关键词", bool(res) and res[0]["entry"].content == "今天天气不错")

# =====================================================================
print("\n== 2. 临时会话上下文 ==")
# =====================================================================
s = Settings()
_sc.clear_turns("CTX-U")
_sc.append_turn("CTX-U", "user", "第一句")
_sc.append_turn("CTX-U", "assistant", "第二句")
turns = _sc.get_turns("CTX-U")
check("append/get 顺序正确", [t["role"] for t in turns] == ["user", "assistant"]
      and turns[0]["content"] == "第一句")

_sc.append_turn("CTX-U", "user", "x" * (s.session_context_max_chars + 500))
turns = _sc.get_turns("CTX-U")
check("单条消息截断到 max_chars", len(turns[-1]["content"]) == s.session_context_max_chars)

for i in range(s.session_context_max_turns * 2 + 4):
    _sc.append_turn("CTX-U", "assistant", f"m{i}")
turns = _sc.get_turns("CTX-U")
check("轮数截断到 max_turns 对", len(turns) == s.session_context_max_turns * 2)

_sc.append_turn("TTL-U", "user", "hi")
_sc._store._data[("TTL-U", "")]["last_seen"] = time.time() - 9999
check("TTL 过期后 get 返回空", _sc.get_turns("TTL-U") == [])

_sc.append_turn("CLR-U", "user", "hi")
_sc.clear_turns("CLR-U")
check("clear 清空", _sc.get_turns("CLR-U") == [])

# 多会话隔离(user_id + session_id)
_sc.append_turn("MS-U", "user", "会话1的话", "s1")
_sc.append_turn("MS-U", "user", "会话2的话", "s2")
check("多会话按 session_id 隔离",
      [t["content"] for t in _sc.get_turns("MS-U", "s1")] == ["会话1的话"]
      and [t["content"] for t in _sc.get_turns("MS-U", "s2")] == ["会话2的话"])
_sc.clear_turns("MS-U", "s1")
check("clear 只清指定会话",
      _sc.get_turns("MS-U", "s1") == []
      and [t["content"] for t in _sc.get_turns("MS-U", "s2")] == ["会话2的话"])

# =====================================================================
print("\n== 3. reflect 增量 ==")
# =====================================================================
store.get_or_create_user(db, "REF-U")
for i in range(3):
    e = store.add_memory(db, "REF-U", content=f"晚上难受{i}",
                         emotion={"score": 40, "emotion": "难过"}, time_tag="晚上")
    reflect.upsert_emotion_node(db, e)
e2 = store.add_memory(db, "REF-U", content="晚上想她",
                      emotion={"score": 40, "emotion": "想念"}, time_tag="晚上")
reflect.upsert_emotion_node(db, e2)
by = {(n.trigger, n.emotion): n.frequency for n in store.list_emotion_nodes(db, "REF-U")}
check("同 trigger+emotion 累加 frequency", by.get(("晚上", "难过")) == 3)
check("不同 emotion 各自成节点", by.get(("晚上", "想念")) == 1 and len(by) == 2)

reflect.reflect(db, "REF-U")
by2 = {(n.trigger, n.emotion): n.frequency for n in store.list_emotion_nodes(db, "REF-U")}
check("reflect 全量重建仍幂等", by2 == by)

# =====================================================================
print("\n== 4. 时间要素 ==")
# =====================================================================
emo_json = {"emotion": "想念", "valence": -0.4, "arousal": 0.5, "score": 40, "reason": "提到过去"}
extract_json = {
    "facts": [{"kind": "time", "fact": "去年生日", "confidence": 0.9},
              {"kind": "object", "fact": "他送的手表", "confidence": 0.8}],
    "summary": "去年生日他送手表", "importance": 7.0, "place_tag": None, "time_tag": "晚上",
}
fc = FakeClient(json_script=[emo_json, extract_json])
turn = extract.extract_turn(fc, "去年生日他送我的手表,我一直戴着")
check("提取出 kind=time 事实",
      any(f.get("kind") == "time" and f.get("fact") == "去年生日" for f in turn["facts"]))

# =====================================================================
print("\n== 5. 画像增量合并 ==")
# =====================================================================
store.get_or_create_user(db, "PT-U")
store.upsert_portrait(db, "PT-U", "user", {"昵称": "林薇", "关系与背景": "暂未提及", "困惑点": "暂未提及"})
check("mock 下跳过合并", portrait.merge_if_due(db, "PT-U", LLMClient(settings=Settings(mock_llm=True))) is False)

for i in range(Settings().portrait_merge_every):
    store.add_memory(db, "PT-U", content=f"第{i}天", summary=f"第{i}天",
                     facts=[{"kind": "user", "fact": "初恋四年", "confidence": 0.9}],
                     emotion=None, importance=5.0)
# LLM 试图把「昵称」改写成「小薇」——应被保护;「关系与背景/困惑点」是占位,应被填充
merged_json = {"昵称": "小薇", "关系与背景": "初恋四年", "困惑点": "是不是自己的问题"}
fc2 = FakeClient(json_script=[merged_json])
ok = portrait.merge_if_due(db, "PT-U", fc2)
p = store.get_portrait(db, "PT-U", "user")
check("触发合并并填充占位字段", ok is True and p.get("关系与背景") == "初恋四年")
check("保护已确认字段不被覆盖", p.get("昵称") == "林薇")

# =====================================================================
print("\n== 6. context_block 渲染 facts ==")
# =====================================================================
e = store.add_memory(db, "CTX-U", content="", summary="去年生日他送手表",
                     facts=[{"kind": "time", "fact": "去年生日", "confidence": 0.9}], emotion=None)
blk = companion._context_block(db, "CTX-U", "", {"user": {}, "object": {}}, [{"entry": e}])
check("渲染【要点:…】", "【要点:去年生日】" in blk)

# =====================================================================
print("\n== 7. 引用克制(刚聊过的不再重复注入) ==")
# =====================================================================
store.get_or_create_user(db, "RS-U")
store.add_memory(db, "RS-U", content="外婆做的桂花糕很好吃", summary="外婆做的桂花糕",
                 facts=[{"kind": "memory", "fact": "外婆做的桂花糕", "confidence": 0.9}],
                 emotion={"score": 50, "emotion": "想念"}, importance=7.0)
got = [m["entry"].content for m in recall.recall(db, "RS-U", "桂花糕")]
check("无 recent_text 时正常召回", "外婆做的桂花糕很好吃" in got)
got2 = [m["entry"].content for m in recall.recall(db, "RS-U", "桂花糕",
                                                  recent_text="外婆做的桂花糕我特别想念")]
check("已在最近对话里的记忆不再注入", "外婆做的桂花糕很好吃" not in got2)

# =====================================================================
print("\n" + "=" * 60)
failed = [n for n, ok in _results if not ok]
print(f"共 {len(_results)} 项,失败 {len(failed)} 项")
for n in failed:
    print(f"  FAIL: {n}")
print("=" * 60)

db.close()
engine.dispose()
try:
    os.remove(_tmp.name)
except OSError:
    pass

sys.exit(1 if failed else 0)
