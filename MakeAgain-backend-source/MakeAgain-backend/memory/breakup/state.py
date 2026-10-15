"""失恋疗愈状态:四维连续信号聚合 + 每次会话开场的定位重估。

设计(对齐「持久化证据而非结论」):
- 不持久化硬阶段标签;持久化的是记忆流(每条记忆带 emotion + cbt)。
- 每次会话开始时,从记忆流现算四维信号,结合开场白定位当前状态。
- 阶段回退是健康的;急性情绪永远优先安抚;跨天不沿用上次结论。
"""
from __future__ import annotations

from datetime import timedelta

from config import get_settings
from memory import store
from memory.breakup.extract import lexicon_cbt
from memory.models import utcnow

STAGES = ("acute", "rumination", "reflection", "rebuilding")

# 开场白急性情绪词:命中即先安抚,无论历史信号
_ACUTE_OPENING = ("崩溃", "哭", "撑不住", "熬不过", "活不下去", "太痛", "受不了", "难过死", "心疼")

# 复盘/求因意图词(零 LLM,口径与 tone.detect_analyze_intent 一致)
_ANALYZE_HINTS = (
    "为什么", "是不是我的错", "我的问题", "谁的问题", "分析", "复盘", "归因",
    "依恋", "回避型", "焦虑型", "分手原因", "这段关系", "帮我看看",
)

# 各阶段给 prompt 的指引(注入失恋 SYSTEM_PROMPT)
_GUIDANCE = {
    "acute": "当前疗愈阶段定位【急性期】:先承接情绪、给予陪伴与安全感,不推进任何道理或建议,不追问细节。",
    "rumination": "当前疗愈阶段定位【反复期】:帮助用户理解失恋反应(失眠/反刍/情绪反复)是正常的,温柔引入认知视角;"
                  "识别并温和挑战自动思维(如「都是我不好」「我不值得被爱」),但不强行说教。先共情一句,然后直接把新的视角说出来,不要只反问。",
    "reflection": "当前疗愈阶段定位【复盘期】:用户愿意复盘,做平衡归因——既看到对方的部分,也温柔点出用户可成长的部分;"
                  "识别认知扭曲,不下诊断;受害者场景不归咎。直接给出平衡归因的观察,不要只追问。",
    "rebuilding": "当前疗愈阶段定位【重建期】:把注意力引向已经走过来的进步、当下能做的小事、继续往前走的方向;"
                  "可给可操作的小建议。",
}


def gather_signals(db, user_id: str, days: int = 7) -> dict:
    """从记忆流聚合四维连续信号。"""
    since = utcnow() - timedelta(days=days)
    mems = [m for m in store.list_memories(db, user_id) if m.ts >= since]

    scores = [m.emotion["score"] for m in mems if isinstance(m.emotion, dict) and "score" in m.emotion]
    baseline = sum(scores) / len(scores) if scores else 50.0
    trend = 0.0
    if len(scores) >= 4:
        half = len(scores) // 2
        trend = (sum(scores[-half:]) / half) - (sum(scores[:half]) / (len(scores) - half))

    cbt_list = [m.cbt for m in mems if isinstance(m.cbt, dict)]
    rumination = sum(1 for c in cbt_list if c.get("rumination")) / len(cbt_list) if cbt_list else 0.0
    reflective = (sum(float(c.get("reflective_readiness", 0.0)) for c in cbt_list) / len(cbt_list)
                  if cbt_list else 0.0)
    functional = (sum(float(c.get("functional_signal", 0.0)) for c in cbt_list) / len(cbt_list)
                  if cbt_list else 0.0)

    return {
        "n_memories": len(mems),
        "baseline": round(baseline, 1),
        "trend": round(trend, 1),
        "rumination": round(rumination, 2),
        "reflective_readiness": round(reflective, 2),
        "functional_signal": round(functional, 2),
    }


def locate_stage(db, user_id: str, message: str) -> dict:
    """每次会话开场定位当前疗愈状态。返回 {stage, reason, guidance, signals}。"""
    sig = gather_signals(db, user_id)
    text = message or ""

    cbt_now = lexicon_cbt(text)

    # 1. 开场白急性情绪(崩溃/哭/撑不住…)→ 立即安抚,最高优先(安全,不受其它信号影响)
    if any(k in text for k in _ACUTE_OPENING):
        return _locate("acute", "开场白出现急性情绪词,先承接陪伴", sig)

    # 2. 用户主动求因 → 复盘(反思期)
    if any(k in text for k in _ANALYZE_HINTS):
        return _locate("reflection", "用户主动寻求归因复盘", sig)

    # 3. 当前消息本身有功能恢复/重建迹象 → 重建期(消息是最强的当下信号)
    if cbt_now["functional_signal"] >= 0.5:
        return _locate("rebuilding", "当前消息有明显功能恢复/重建迹象", sig)

    # 4. 当前消息本身在反刍(翻朋友圈/控制不住/忍不住想) → 反复期,别让低基线把它盖成急性
    if cbt_now["rumination"]:
        return _locate("rumination", "当前消息在反刍,给予正常化 + 温和认知视角", sig)

    # 5. 情绪基线极低(<25)作为急性兜底:历史持续严重痛苦、且当前消息没有更强的正向/求因/反刍信号时才生效。
    #    注:阈值从 40 降到 25,且从「第一优先」移到「当下信号之后」——否则 score 里 50 才是中性、<40 只是
    #    「轻微负面」,会把「还在难受但已进入反刍期」的用户长期误判成急性期,还会盖过「去健身」这类重建信号。
    if sig["baseline"] < 25:
        return _locate("acute", "情绪基线极低,仍处于严重痛苦,先承接陪伴", sig)

    # 6. 反刍强且反思就绪低 → 反复期(教育 + 认知重构)
    if sig["rumination"] >= 0.4 and sig["reflective_readiness"] < 0.5:
        return _locate("rumination", "反刍明显、尚未能理性复盘", sig)

    # 7. 功能恢复明显且情绪稳定 → 重建期
    if sig["functional_signal"] >= 0.4 and sig["baseline"] >= 50:
        return _locate("rebuilding", "功能在恢复、情绪趋稳,适合引导向前", sig)

    # 8. 默认:情绪相对平稳但仍在消化 → 反复期(最保守的中间态)
    return _locate("rumination", "情绪相对平稳,仍处于消化期", sig)


def _locate(stage: str, reason: str, sig: dict) -> dict:
    return {"stage": stage, "reason": reason, "guidance": _GUIDANCE[stage], "signals": sig}
