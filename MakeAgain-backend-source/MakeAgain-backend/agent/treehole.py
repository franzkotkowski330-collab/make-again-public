"""树洞信箱(F3):用户写下痛苦 → 相似经历者回信 → 审核后送达。

事件触发(均在用户打开 App 时由前端调相应接口判断是否弹窗):
- 写信:完成 treehole_write_sessions 轮真实对话(用户消息和回复均已保存),且尚未写过信。
- 回信:归档后的最后一个流程(已归档且存在可回的来信即邀),不再看情绪是否稳定。

匹配(用户确认):规则初筛(loss_type 一致 + 关键词/情绪重叠)→ LLM 对 top-k 复排相似度。
审核(用户确认):自动 PII 扫描(正则 + LLM)→ 通过后进入待审队列,后台审批后送达。
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from agent.moderation import scan_pii
from config import get_settings
from emotion.classifier import classify
from gateway.client import LLMClient
from memory import store

_EXTRACT_SYSTEM = "你是记忆结构化助手,只输出 JSON,不要多余文字。"
_RERANK_SYSTEM = "你是经历匹配助手,只输出 JSON,不要多余文字。"


def _local(ts) -> str:
    """naive UTC → 东八区本地时间,统一对外展示口径(与聊天历史 serialize 一致)。"""
    return (ts + timedelta(hours=get_settings().timezone_offset_hours)).isoformat()


# ---- 写信 ----

def write_eligibility(db, user_id: str) -> dict:
    minimum=get_settings().treehole_write_sessions  # Existing configuration name retained.
    turns=store.count_completed_chat_turns(db,user_id)
    sessions=store.count_chat_sessions(db,user_id)
    wrote=bool(store.list_letters_by_author(db,user_id))
    companion=store.get_current_companion(db,user_id)
    archived=companion is not None and companion.archived_at is not None
    eligible=turns>=minimum and not wrote and not archived
    if archived:
        reason="这段陪伴已归档，不再写树洞信"
    elif wrote:
        reason="你已经写过树洞信了"
    elif turns<minimum:
        reason=f"完成 {minimum} 轮对话后开放（当前 {turns} 轮）"
    else:
        reason="可以写一封树洞信"
    return {"eligible":eligible,"completed_turns":turns,"min_turns":minimum,
            "eligibility_basis":"completed_turns","chat_sessions":sessions,"min_sessions":minimum,
            "already_wrote":wrote,"archived":archived,"reason":reason}


def _extract_experience(client: LLMClient, text: str) -> tuple[list[str], str]:
    parsed, _ = client.chat_json(
        [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": (
                "从下面这段倾诉/背景中提炼用于「经历匹配」的结构化信息,输出 JSON:\n"
                "{\"tags\":[\"经历关键词\"], \"summary\":\"一行经历摘要(匿名,不含真实人名/地名/联系方式)\"}\n"
                "tags 是 3-6 个能代表这段经历特点的关键词(如:异地、父母反对、突然病逝、癌症、被出轨、年龄差)。\n"
                "summary 一句话概括经历,保持匿名。\n\n文本:\n" + text
            )},
        ],
        temperature=0.2, model=client.settings.llm_fast_model,
    )
    if not isinstance(parsed, dict):
        return [], text[:80]
    tags = parsed.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags = [str(t) for t in tags if isinstance(t, str)][:6]
    summary = str(parsed.get("summary") or text[:80])
    return tags, summary


def write_letter(db, user_id: str, content: str, client: LLMClient) -> dict:
    store.get_or_create_user(db, user_id)
    elig = write_eligibility(db, user_id)
    if not elig["eligible"]:
        return {"ok": False, "reason": elig["reason"]}

    scan = scan_pii(content, client)
    if not scan["clean"]:
        return {"ok": False, "reason": "检测到真实定位/联系方式等敏感信息,请去掉后再发",
                "flags": scan["flags"]}

    emo = classify(content, client)
    tags, summary = ([], content[:80]) if client.mock else _extract_experience(client, content)
    letter = store.add_letter(
        db, user_id, content=content, summary=summary,
        loss_type=store.effective_loss_type(db, user_id), emotion=emo["emotion"], tags=tags,
    )
    letter.status="pending_review"
    return {"ok": True, "status":letter.status, "letter_id": letter.id, "emotion": emo["emotion"],
            "tags": tags, "summary": summary}


# ---- 回信资格(暂定代理,后续随情绪数据化细化) ----

def reply_eligibility(db, user_id: str) -> dict:
    # 回信邀请 = 归档后的最后一个流程:已归档即邀,不再看情绪是否稳定(先照顾好自己 → 回身陪伴他人)。
    companion = store.get_current_companion(db, user_id)
    if companion is None or companion.archived_at is None:
        return {"eligible": False, "ended": False,
                "reason": "结束这段陪伴后，才会收到回信邀请"}
    return {"eligible": True, "ended": True, "reason": "可以试着回信陪伴他人"}


# ---- 匹配 ----

def _user_profile(db, user_id: str, client: LLMClient) -> dict:
    store.get_or_create_user(db, user_id)
    mems = store.list_memories(db, user_id)[:5]
    emotions = [m.emotion.get("emotion") for m in mems if m.emotion and m.emotion.get("emotion")]
    emotion = Counter(emotions).most_common(1)[0][0] if emotions else None

    parts = [str(v) for v in (store.get_portrait(db, user_id, "user") or {}).values() if v]
    parts += [m.summary or m.content for m in mems]
    text = " ".join(p for p in parts if p)
    tags, summary = ([], text[:80]) if (client.mock or not text) else _extract_experience(client, text[:2000])
    return {"loss_type": store.effective_loss_type(db, user_id), "emotion": emotion, "tags": tags, "summary": summary}


def _structural_score(letter, profile: dict) -> float:
    s = 0.0
    if profile["loss_type"] and letter.loss_type == profile["loss_type"]:
        s += 3.0
    if profile["emotion"] and letter.emotion == profile["emotion"]:
        s += 1.0
    s += len(set(letter.tags or []) & set(profile["tags"] or [])) * 1.5
    return s


def _llm_rerank(client: LLMClient, profile: dict, letters: list) -> list:
    listing = "\n".join(f"- id={L.id} | {L.summary or L.content[:60]}" for L in letters)
    parsed, _ = client.chat_json(
        [
            {"role": "system", "content": _RERANK_SYSTEM},
            {"role": "user", "content": (
                "根据「回信者」的经历,判断下面各封来信与 TA 的经历相似度(0-1,越高越相似)。\n"
                f"回信者经历:{profile['summary'] or '(信息不足)'}\n"
                f"回信者关键词:{profile['tags']}\n\n"
                f"来信列表:\n{listing}\n\n"
                "只输出 JSON:{\"ranking\":[{\"id\":\"信id\",\"score\":0.8}]},按相似度从高到低,包含所有给出的来信 id。"
            )},
        ],
        temperature=0.2, model=client.settings.llm_fast_model,
    )
    order: dict[str, float] = {}
    if isinstance(parsed, dict) and isinstance(parsed.get("ranking"), list):
        for it in parsed["ranking"]:
            if isinstance(it, dict) and it.get("id"):
                try:
                    order[str(it["id"])] = float(it.get("score", 0))
                except (TypeError, ValueError):
                    order[str(it["id"])] = 0.0
    return sorted(letters, key=lambda L: order.get(L.id, _structural_score(L, profile) / 10.0), reverse=True)


def get_matches(db, user_id: str, client: LLMClient) -> dict:
    s = get_settings()
    profile = _user_profile(db, user_id, client)
    candidates = [
        L for L in store.list_published_letters(db)
        if L.author_user_id != user_id
        and (not profile["loss_type"] or L.loss_type == profile["loss_type"])
    ]
    if not candidates:
        return {"matches": [], "reason": "暂时没有和你经历相似的信"}

    ranked = sorted(candidates, key=lambda L: _structural_score(L, profile), reverse=True)
    top_k = ranked[:s.treehole_match_top_k]
    if len(top_k) > 1 and not client.mock:
        top_k = _llm_rerank(client, profile, top_k)

    matches = [{
        "letter_id": L.id, "loss_type": L.loss_type, "emotion": L.emotion,
        "tags": L.tags or [], "summary": L.summary or "", "content": L.content,
    } for L in top_k[:s.treehole_match_top_n]]
    return {"matches": matches, "reason": f"为你找到 {len(matches)} 封经历相近的信"}


# ---- 回信 + 审核 ----

def submit_reply(db, user_id: str, letter_id: str, content: str, client: LLMClient) -> dict:
    letter = store.get_letter(db, letter_id)
    if letter is None or letter.status!="published":
        raise ValueError("信不存在")
    from api.community import blocked
    if blocked(db,user_id,letter.author_user_id): raise ValueError("信不存在")
    if not reply_eligibility(db,user_id)["eligible"]: return {"ok":False,"reason":"暂未满足回信条件"}
    if letter.author_user_id == user_id:
        return {"ok": False, "reason": "不能回复自己写的信"}

    scan = scan_pii(content, client)
    if not scan["clean"]:
        return {"ok": False, "reason": "检测到真实定位/联系方式等敏感信息,请去掉后再发",
                "flags": scan["flags"]}

    reply = store.add_reply(db, letter_id, user_id, content)
    return {"ok": True, "reply_id": reply.id, "status": reply.status}


def review_pending(db) -> list[dict]:
    out = []
    for r in store.list_pending_replies(db):
        letter = store.get_letter(db, r.letter_id)
        out.append({
            "reply_id": r.id, "letter_id": r.letter_id, "author_user_id": r.author_user_id,
            "content": r.content, "created_at": _local(r.created_at),
            "letter": {
                "letter_id": letter.id, "content": letter.content, "summary": letter.summary,
            } if letter is not None else None,
        })
    return out


def approve_reply(db, reply_id: str) -> dict:
    store.update_reply_status(db, reply_id, "delivered")
    return {"ok": True, "status": "delivered"}


def reject_reply(db, reply_id: str) -> dict:
    store.update_reply_status(db, reply_id, "rejected")
    return {"ok": True, "status": "rejected"}


# ---- 打开 App 时的一次性弹窗 + 看板 ----

def popups(db, user_id: str, client: LLMClient) -> dict:
    """打开 App 时聚合树洞弹窗:写信邀请 / 回信邀请 / 收到回信(各只弹一次)。

    返回 {popups:[{kind, data}]},前端按顺序逐个展示,每个展示/关闭后调 /treehole/popup/seen。
    """
    store.get_or_create_user(db, user_id)
    out: list[dict] = []

    # 1. 写信邀请:满足写信资格且尚未看过(写过信后 write_eligibility 自然不再 eligible)
    if not store.treehole_seen(db, user_id, "write"):
        w = write_eligibility(db, user_id)
        if w["eligible"]:
            out.append({"kind": "write", "data": w})

    # 2. 回信邀请:满足回信资格且有可回的来信
    if not store.treehole_seen(db, user_id, "reply_invite"):
        r = reply_eligibility(db, user_id)
        if r["eligible"]:
            matches = get_matches(db, user_id, client)
            if matches["matches"]:
                out.append({"kind": "reply_invite", "data": {"eligibility": r, **matches}})

    # 3. 收到回信:有已送达、尚未看过的回信
    for item in _unseen_received(db, user_id):
        out.append({"kind": "reply_received", "data": item})

    return {"popups": out}


def mark_popup_seen(db, user_id: str, kind: str, ref_id: str | None = None) -> dict:
    """标记某个弹窗已看/关闭,之后不再弹。kind 非法时忽略。"""
    if kind not in ("write", "reply_invite", "reply_received"):
        return {"ok": False}
    if kind=="reply_received":
        from api.security import fail
        reply=store.get_reply(db,ref_id) if ref_id else None
        letter=store.get_letter(db,reply.letter_id) if reply else None
        if not reply or reply.status!="delivered" or not letter or letter.author_user_id!=user_id or letter.companion_id!=db.info.get("companion_id"):
            fail(404,"NOT_FOUND","回信不存在")
    store.mark_treehole_seen(db, user_id, kind, ref_id)
    return {"ok": True}


def my_letters(db, user_id: str, rows=None) -> list[dict]:
    """看板:我写的树洞信 + 各自收到的回信(匿名,不暴露回信者身份)。"""
    out = []
    from api.community import blocked
    for L in rows if rows is not None else store.list_letters_by_author(db, user_id):
        out.append({
            "letter_id": L.id, "id": L.id, "companion_id": L.companion_id, "status": L.status, "content": L.content, "summary": L.summary,
            "loss_type": L.loss_type, "emotion": L.emotion, "tags": L.tags or [],
            "created_at": _local(L.created_at),
            "replies": [{
                "reply_id": r.id, "content": r.content,
                "created_at": _local(r.reviewed_at or r.created_at),
                "source": _reply_source(r),
            } for r in store.list_replies_by_letter(db, L.id) if not blocked(db,user_id,r.author_user_id)],
        })
    return out


def my_replies(db, user_id: str) -> list[dict]:
    """看板:我写给相似经历者的回信(含审核状态 + 对应来信摘要)。"""
    out = []
    for r in store.list_replies_by_author(db, user_id):
        letter = store.get_letter(db, r.letter_id)
        out.append({
            "reply_id": r.id, "content": r.content, "status": r.status,
            "created_at": _local(r.created_at),
            "letter": {
                "letter_id": letter.id, "summary": letter.summary, "content": letter.content,
            } if letter is not None else None,
        })
    return out


def _unseen_received(db, user_id: str) -> list[dict]:
    """送达给该用户、尚未看过的回信(供弹窗)。"""
    out = []
    from api.community import blocked
    for r in store.list_delivered_replies_to_author(db, user_id):
        if blocked(db,user_id,r.author_user_id):continue
        if store.treehole_seen(db, user_id, "reply_received", r.id):
            continue
        letter = store.get_letter(db, r.letter_id)
        out.append({
            "reply_id": r.id, "content": r.content,
            "created_at": _local(r.reviewed_at or r.created_at),
            "letter_id":letter.id if letter else None,
            "letter_summary": letter.summary if letter is not None else "",
            "source": _reply_source(r),
        })
    return out


def _reply_source(reply) -> str:
    """回信来源:运营人员回信标为 operator,便于前端区分「官方回信」与「用户回信」。"""
    return "operator" if reply.author_user_id == get_settings().treehole_operator_id else "user"


# ---- 运营后台:查看所有来信 + 官方回信(直达,无需审核) ----

def admin_letters(db) -> list[dict]:
    """运营后台:列出所有来信(含各自待审核+已送达回信数),按时间倒序。"""
    out = []
    for L in store.list_all_letters(db):
        out.append({
            "letter_id": L.id, "author_user_id": L.author_user_id,
            "content": L.content, "summary": L.summary,
            "loss_type": L.loss_type, "emotion": L.emotion, "tags": L.tags or [],
            "status": L.status, "created_at": _local(L.created_at),
            "reply_count": store.count_active_replies(db, L.id),
        })
    return out


def admin_reply(db, letter_id: str, content: str, client: LLMClient) -> dict:
    """运营人员给来信回信:直接送达(免审核),author_user_id 记为运营占位 id。"""
    letter = store.get_letter(db, letter_id)
    if letter is None:
        raise ValueError("信不存在")

    scan = scan_pii(content, client)
    if not scan["clean"]:
        return {"ok": False, "reason": "检测到真实定位/联系方式等敏感信息,请去掉后再发",
                "flags": scan["flags"]}

    reply = store.add_reply(db, letter_id, get_settings().treehole_operator_id, content)
    store.update_reply_status(db, reply.id, "delivered")
    return {"ok": True, "reply_id": reply.id, "status": "delivered", "source": "operator"}
