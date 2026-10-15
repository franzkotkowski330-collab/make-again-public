"""时间依据的按需召回:摘要按状态触发、事件按时间参照,零 LLM、纯本地。

解决引擎说「你比一个月前的自己强多了——那时候你连白天都熬不住」这类**没有依据的时间对比**:
只在用户「回想过去状态 / 讲现在状态·变化 / 带了时间参照」时,才召回
- 最近的每日摘要(`DailyChatSummary`,作为「近期状态轨迹」),
- 或按时间匹配的事件记忆(`MemoryEntry` 的 `time` fact / 写入时刻 `ts`),
注入上下文;其余轮次不注入、不打扰、不灌水。

时间口径(现状):
- `MemoryEntry.ts` = 写入时刻(用户告诉我们的时刻),用它做相对时间窗口(如「两个月前」)的近似锚点。
- `MemoryEntry.facts` 里 `kind="time"` 的 fact = 事件发生的真实时间(自由文本,如「去年生日」「三年前」),用它做文本时间匹配。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from config import get_settings
from memory import store
from memory.facts import facts_text
from memory.recall import _relevance, _tokens


# ---- 触发信号:回想过去状态 / 讲现在状态·变化(宁缺毋滥,只为一个紧凑块) ----

_REFLECT_HINTS = (
    "那时候", "刚开始", "刚分手", "刚失去", "从前", "当初", "原来我", "那阵子", "前阵子",
    "好多了", "比以前", "比之前", "进步", "走出来", "好转", "恢复", "不一样了", "变了",
    "好一些", "越来越好", "没那么难受", "平静多了",
)


def _temporal_reflect(message: str) -> bool:
    """是否在回想过去状态 / 讲现在状态·变化(决定要不要召回「近期状态轨迹」)。"""
    text = message or ""
    if any(k in text for k in _REFLECT_HINTS):
        return True
    return parse_time_ref(text) is not None


# ---- 时间参照解析(零 LLM) ----

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}

# 「X 天/周/个月/年 前」→ 相对时间(约数)
_TIME_REL_RE = re.compile(
    r"(?P<num>[\d一二两三四五六七八九十]{1,3})\s*个?\s*(?P<unit>星期|周|天|日|月|年)前"
)
_UNIT_DAYS = {"星期": 7, "周": 7, "天": 1, "日": 1, "月": 30, "年": 365}

# 文本时间词:有粗略时长的给窗口(用于 ts 兜底);纯事件词只做 time fact 文本匹配。
_TEXT_WORD_WINDOW = {
    "去年": (270, 460),
    "前年": (600, 820),
    "上个月": (15, 50),
    "上上个月": (40, 80),
    "上周": (4, 12),
    "上个星期": (4, 12),
    "上上周": (10, 21),
    "前几天": (1, 10),
    "前些天": (1, 15),
}
_TEXT_TIME_WORDS = tuple(_TEXT_WORD_WINDOW) + ("生日", "纪念日", "过年", "春节", "元旦")


def _cn_to_int(s: str) -> int | None:
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    if s == "十":
        return 10
    if s.endswith("十") and s[:-1] in _CN_DIGIT:
        return _CN_DIGIT[s[:-1]] * 10
    if s.startswith("十") and s[1:] in _CN_DIGIT:
        return 10 + _CN_DIGIT[s[1:]]
    if "十" in s:
        h, t = s.split("十", 1)
        if h in _CN_DIGIT and t in _CN_DIGIT:
            return _CN_DIGIT[h] * 10 + _CN_DIGIT[t]
    return None


def parse_time_ref(query: str) -> dict | None:
    """解析用户话里的时间参照。

    返回 {lo, hi, text}:lo/hi 为相对天数窗口(ts 兜底,可 None);text 为要匹配 time fact 的词。
    无时间参照返回 None。
    """
    text = query or ""
    m = _TIME_REL_RE.search(text)
    if m:
        n = _cn_to_int(m.group("num"))
        if n is not None:
            center = n * _UNIT_DAYS[m.group("unit")]
            return {"lo": center * 0.7, "hi": center * 1.5, "text": []}
    words = [w for w in _TEXT_TIME_WORDS if w in text]
    if words:
        lo = hi = None
        for w in words:
            if w in _TEXT_WORD_WINDOW:
                a, b = _TEXT_WORD_WINDOW[w]
                lo = a if lo is None else min(lo, a)
                hi = b if hi is None else max(hi, b)
        return {"lo": lo, "hi": hi, "text": words}
    return None


# ---- 召回 ----

def _today_key() -> str:
    offset = get_settings().timezone_offset_hours
    return (datetime.now() + timedelta(hours=offset)).strftime("%Y-%m-%d")


def _fmt_md(date_key: str) -> str:
    try:
        y, m, d = date_key.split("-")
        return f"{int(m)}月{int(d)}日"
    except (ValueError, AttributeError):
        return date_key or ""


def recall_daily_summaries(db, user_id: str, message: str, top_k: int = 5) -> str:
    """触发式召回最近每日摘要,作为「近期状态轨迹」。未触发或无摘要返回空串。"""
    if not _temporal_reflect(message):
        return ""
    today = _today_key()
    recent = [s for s in store.list_daily_chat_summaries(db, user_id) if s.date_key < today][:7]
    if not recent:
        return ""
    q = _tokens(message)
    # 按与当前消息的相关度(「情绪/状态」的零 LLM 代理)排序,全 0 时稳定保留最近几天
    ranked = sorted(recent, key=lambda s: _relevance(q, _tokens(s.summary or "")), reverse=True)
    top = ranked[:top_k]
    top.sort(key=lambda s: s.date_key)  # 最旧→最新,让模型看到进展
    lines = [f"- {_fmt_md(s.date_key)}: {s.summary}" for s in top]
    return "[近期状态轨迹(按日期,只作时间对比依据,不要编造轨迹之外的日期)]\n" + "\n".join(lines)


def recall_by_time(db, user_id: str, query: str, top_k: int = 2, skip_ids=None) -> str:
    """按时间参照召回事件记忆。用户说「两个月前的那件事」时,把当时那条记忆捞回来。"""
    ref = parse_time_ref(query)
    if not ref:
        return ""
    skip = skip_ids or set()
    entries = store.list_memories(db, user_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    scored: list[tuple[float, object]] = []
    for e in entries:
        if e.id in skip:
            continue
        s = 0.0
        if ref["lo"] is not None:
            age = (now - e.ts).total_seconds() / 86400.0
            if ref["lo"] <= age <= ref["hi"]:
                s += 3.0
        if ref["text"]:
            ft = facts_text(e.facts)
            for w in ref["text"]:
                if w in ft:
                    s += 2.0
        if s > 0:
            scored.append((s, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for _s, e in scored[:top_k]:
        text = (e.summary or e.content or "").strip()[:80]
        if text:
            out.append(f"- {e.ts.month}月{e.ts.day}日: {text}")
    if not out:
        return ""
    return "[相关往事(你当时说过,按时间召回)]\n" + "\n".join(out)
