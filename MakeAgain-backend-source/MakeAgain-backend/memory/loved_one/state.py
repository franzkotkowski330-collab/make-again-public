"""亲友离世疗愈状态:六维连续信号聚合 + 每次会话开场的定位重估。

设计(对齐「持久化证据而非结论」,与失恋 state.py 同构):
- 不持久化硬阶段标签;持久化的是记忆流(每条记忆带 emotion + cbt)。
- 每次会话开始时,从记忆流现算六维信号,结合开场白定位当前状态。
- 阶段可回退;跨天不沿用上次结论。

六维信号(理论对齐):
- baseline(情绪基线):avg emotion["score"],越高=心情越好(复用失恋口径)。
- yearning(渴望思念):PGD 核心症状,替代失恋的「反刍」。
- meaning_reconstruction(意义重建):Neimeyer,能否给死亡一个说法。
- continuing_bond(持续联结质量):Klass,健康联结 vs 冻结。
- death_acceptance(死亡现实接受):Worden 任务1。
- functional_signal(功能恢复):Track I / DPM 恢复导向。
"""
from __future__ import annotations

from datetime import timedelta

from memory import store
from memory.loved_one.extract import lexicon_grief
from memory.models import utcnow

STAGES = ("acute", "yearning", "oscillating", "restoration", "integrating")

# 急性/危险词:崩溃 + 想随逝者而去(危机门控已拦自伤,这里补丧亲特有的「想去找TA」)
_ACUTE_OPENING = (
    "崩溃", "哭", "撑不住", "熬不过", "太痛", "受不了", "难过死",
    "去陪他", "去陪她", "跟他走", "跟她走", "去那边", "一起走", "去天堂",
)

# 意义重建 + 持续联结(Neimeyer / Klass):在求意义 / 已找到意义 / 用纪念维系联结。
# 注:否认死亡、渴望思念、内疚这三类当下信号不在此重复定义,统一走 extract.lexicon_grief(text)。
_MEANING_HINTS = (
    "为什么偏偏", "他教会了我", "她教会了我", "让我懂得", "让我明白",
    "想通了", "活在我心里", "在天上", "他一定希望", "她一定希望",
    "就当是", "纪念", "她最爱的", "他最爱的",
)

_GUIDANCE = {
    "acute": "当前疗愈阶段定位【急性哀伤期】:先承接情绪、给予陪伴与安全感;不急着讲道理、不催促接受死亡、不追问细节;若对方流露出想随逝者而去,立即重视并引导寻求专业帮助。",
    "yearning": "当前疗愈阶段定位【渴望思念期】:思念与反复想起是正常的哀伤反应,温柔陪伴、允许思念;识别并温和回应内疚(如「都怪我」「如果那天我…」)与未说出口的话;不把「还想 TA」当病态,也不推着「放下」。",
    "oscillating": "当前疗愈阶段定位【振荡整合期】:哀伤本来就会反复——有时很难过,有时又能正常生活,这都是健康的过程;陪伴 TA 在难过与恢复之间慢慢整合,肯定这种反复是正常的。",
    "restoration": "当前疗愈阶段定位【恢复重建期】:功能在恢复、开始重新投入生活;把注意力引向已经走过来的进步、当下能做的小事;同时尊重 TA 仍会想念。",
    "integrating": "当前疗愈阶段定位【持续联结期】:已接受死亡、找到与逝者健康的持续联结方式(纪念/仪式/TA 活在心里),同时展开新生活;支持 TA 用自己喜欢的方式纪念,不必「放下」,而是「带着继续走」。",
}


def gather_signals(db, user_id: str, days: int = 7) -> dict:
    """从记忆流聚合六维连续信号。"""
    since = utcnow() - timedelta(days=days)
    mems = [m for m in store.list_memories(db, user_id) if m.ts >= since]

    scores = [m.emotion["score"] for m in mems if isinstance(m.emotion, dict) and "score" in m.emotion]
    baseline = sum(scores) / len(scores) if scores else 50.0
    trend = 0.0
    if len(scores) >= 4:
        half = len(scores) // 2
        trend = (sum(scores[-half:]) / half) - (sum(scores[:half]) / (len(scores) - half))

    cbt_list = [m.cbt for m in mems if isinstance(m.cbt, dict)]

    def _avg(key: str, default: float) -> float:
        vals = [float(c[key]) for c in cbt_list if isinstance(c.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else default

    # 词典布尔字段 → 占比(0~1)
    def _ratio(key: str) -> float:
        return sum(1 for c in cbt_list if c.get(key)) / len(cbt_list) if cbt_list else 0.0

    return {
        "n_memories": len(mems),
        "baseline": round(baseline, 1),
        "trend": round(trend, 1),
        "yearning": round(_ratio("yearning"), 2),
        "guilt": round(_ratio("guilt"), 2),
        "meaning_reconstruction": round(_avg("meaning_reconstruction", 0.5), 2),
        "continuing_bond": round(_avg("continuing_bond", 0.5), 2),
        "death_acceptance": round(_avg("death_acceptance", 0.5), 2),
        "functional_signal": round(_avg("functional_signal", 0.0), 2),
    }


def locate_stage(db, user_id: str, message: str) -> dict:
    """每次会话开场定位当前哀伤状态。返回 {stage, reason, guidance, signals}。"""
    sig = gather_signals(db, user_id)
    text = message or ""
    cbt_now = lexicon_grief(text)

    # 1. 急性/危险词(崩溃 / 想随逝者而去) → acute,最高优先(安全)
    if any(k in text for k in _ACUTE_OPENING):
        return _locate("acute", "出现急性/危险情绪词,先承接陪伴", sig)

    # 2. 否认死亡 → acute(死亡现实未接受,Worden 任务1)
    if cbt_now["denial"]:
        return _locate("acute", "仍难以接受死亡现实,先陪伴不推进", sig)

    # 3. 当下内疚/未竟之事 → yearning(内疚是丧亲特有的反刍,陪伴 + 温和回应)
    if cbt_now["guilt"]:
        return _locate("yearning", "正在自责/遗憾,陪伴并温和回应内疚", sig)

    # 4. 当下渴望/思念 → yearning
    if cbt_now["yearning"]:
        return _locate("yearning", "正在思念逝者,陪伴这份思念", sig)

    # 5. 当下功能恢复 → restoration(消息是最强的当下信号)
    if cbt_now["functional_signal"] >= 0.5:
        return _locate("restoration", "当前消息有明显功能恢复迹象", sig)

    # 6. 当下意义重建/持续联结 → integrating
    if any(k in text for k in _MEANING_HINTS):
        return _locate("integrating", "在寻找/已找到意义与联结,支持整合", sig)

    # 7. 死亡接受度极低 → acute(兜底:历史持续否认、且当下没有更强的正向/意义信号)
    if sig["death_acceptance"] < 0.3:
        return _locate("acute", "死亡接受度仍很低,先陪伴", sig)

    # 8. 渴望强 + 意义重建低 → yearning(PGD 风险兜底:持续强烈思念、尚未找到意义)
    if sig["yearning"] >= 0.5 and sig["meaning_reconstruction"] < 0.5:
        return _locate("yearning", "持续强烈思念、尚未找到意义,陪伴思念", sig)

    # 9. 功能恢复高 + 基线稳定 → restoration
    if sig["functional_signal"] >= 0.4 and sig["baseline"] >= 50:
        return _locate("restoration", "功能恢复、情绪趋稳,适合引导向前", sig)

    # 10. 意义重建高 + 持续联结健康 → integrating
    if sig["meaning_reconstruction"] >= 0.6 and sig["continuing_bond"] >= 0.5:
        return _locate("integrating", "已找到意义与持续联结,支持整合", sig)

    # 11. 默认 → oscillating(健康哀伤的主态)
    return _locate("oscillating", "在难过与恢复之间反复,是健康哀伤的过程", sig)


def _locate(stage: str, reason: str, sig: dict) -> dict:
    return {"stage": stage, "reason": reason, "guidance": _GUIDANCE[stage], "signals": sig}
