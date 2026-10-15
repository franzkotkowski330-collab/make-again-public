"""HTTP 路由。"""
from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, model_validator
from api.security import SecureRoute, fail, membership, iso
from api import b07
from api.pagination import page as collection_page
from api.media import read_upload, photo_view, item_view
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent import companion, daily as daily_mod, echo as echo_mod, ending as ending_mod, history as history_mod, history_summary, home as home_mod, interview as interview_mod, item as item_mod, note as note_mod, onboarding as onboarding_mod, photo as photo_mod, relationship as relationship_mod, todo as todo_mod, treehole as treehole_mod, weekly as weekly_mod
from config import get_settings
from gateway.client import LLMClient
from gateway.speech import SpeechClient, SpeechError
from gateway.storage import ObjectStore, StorageError
from gateway.wechat import WeChatClient, WeChatError
from gateway.vision import VisionClient
from memory import session_context
from memory import store
from memory.db import get_session
from memory.models import User, UserState
from auth import get_current_user, hash_password, new_expiry, new_token, verify_password

router = APIRouter(prefix="/api", route_class=SecureRoute)

class RequestBase(BaseModel):
    @model_validator(mode="after")
    def fill_owner(self):
        from memory.scope import current_db
        db=current_db.get()
        if hasattr(self,"user_id") and self.user_id is None and db is not None:
            self.user_id=db.info.get("user_id")
        return self

    request_id: str | None = None
    client_request_id: str | None = None
    companion_id: str | None = None



class ChatReq(RequestBase):
    user_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None  # 会话标识;缺省回落默认会话,多端/多会话时由客户端生成并传入
    no_tools: bool = False  # 工具动作后的收尾句:本轮不再触发工具卡片(前端「确定/取消」后的 AI 一句收尾用)


class ChatResp(BaseModel):
    reply: str
    crisis: dict
    emotion: dict | None
    recalled: int
    tone: str | None = None  # soothe | guide | analyze(仅通用引擎)
    stage: str | None = None  # 疗愈阶段标签(失恋/亲友/宠物引擎返回,如 acute/rumination/oscillating)
    tool: dict | None = None  # 物品纪念·照片·待办·写给自己 四类卡片动作(无则 None),前端据此展示卡片


@router.post("/chat")
def chat(req: ChatReq, db: Session = Depends(get_session)):
    c = store.get_current_companion(db, req.user_id)
    if not c or c.archived_at: fail(409, "COMPANION_REQUIRED", "请先开始陪伴")
    if req.session_id and req.session_id != c.chat_session_id:
        fail(409, "SESSION_MISMATCH", "请使用服务器会话", session_id=c.chat_session_id)
    result = companion.chat(db, req.user_id, req.message, LLMClient(), session_id=c.chat_session_id, no_tools=req.no_tools)
    from memory.models import ChatMessage
    rows = db.scalars(select(ChatMessage).where(ChatMessage.user_id==req.user_id, ChatMessage.session_id==c.chat_session_id).order_by(ChatMessage.id.desc()).limit(2)).all()
    result.update(session_id=c.chat_session_id, companion_id=c.id, status="completed",
                  user_message_id=str(rows[-1].id) if rows else None,
                  assistant_message_id=str(rows[0].id) if rows else None,
                  created_at=iso(rows[0].ts) if rows else None)
    if result.get("tool"):
        result["tool"].update(suggestion_id=db.info.get("request_id"), status="awaiting_confirmation")
    return result


class ChatClearReq(RequestBase):
    user_id: str | None = None
    session_id: str | None = None  # 缺省回落默认会话


@router.post("/chat/session/clear")
def chat_session_clear(req: ChatClearReq, db: Session = Depends(get_session)):
    """用户退出 / 关闭某段对话时调用:清空临时上下文 + 后台写今天的总结草稿(无感)。"""
    # Leaving a page does not erase persistent context.
    history_summary.schedule(req.user_id, history_summary.today_key(), "draft")
    return {"ok": True}


# ---- 聊天历史(精确到分钟,游标分页) ----

@router.get("/chat/history/{user_id}/days")
def chat_history_days(user_id: str, before_date: str | None = None,
                      limit: int | None = None, db: Session = Depends(get_session)):
    """聊天记录一级页:每天的总结列表(按日期倒序,before_date 游标分页)。"""
    store.get_or_create_user(db, user_id)
    try:
        return history_mod.day_list(db, user_id, before_date=before_date, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/chat/history/{user_id}/day/{date}")
def chat_history_day(user_id: str, date: str, before_id: int | None = None,
                     after_id: int | None = None, limit: int | None = None,
                     db: Session = Depends(get_session)):
    """聊天记录二级页:某天的内容分页(当天内上滑加载更早)。"""
    store.get_or_create_user(db, user_id)
    try:
        return history_mod.day_page(db, user_id, date,
                                    before_id=before_id, after_id=after_id, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---- 每日主题 + 启发文案 ----

class DailyOpeningReq(RequestBase):
    user_id: str | None = None


@router.post("/daily/theme-guide")
def daily_theme_guide(req: DailyOpeningReq, db: Session = Depends(get_session)):
    """为今日推荐主题生成个性化开场引导(最近状态 + 真实记忆,LLM;无记忆回落 desc)。"""
    return daily_mod.generate_theme_guide(db, req.user_id, LLMClient())


# ---- 语音(TTS / ASR / 上传) ----


def _external(call):
    """调用外部服务,把 StorageError / SpeechError 归一为 502。"""
    try:
        return call()
    except (SpeechError, StorageError) as e:
        fail(503,"EXTERNAL_SERVICE_UNAVAILABLE","外部服务暂时不可用")


class TTSReq(RequestBase):
    text: str = Field(min_length=1,max_length=2000)
    voice_id: str | None = None
    speed: float = 1.0
    vol: float = 1.0
    pitch: float = 0.0


@router.post("/speech/tts")
def speech_tts(req: TTSReq):
    result=_external(lambda: SpeechClient().tts(
        req.text, voice_id=req.voice_id, speed=req.speed, vol=req.vol, pitch=req.pitch))
    return {k:v for k,v in result.items() if k!="raw"}


@router.post("/speech/recognize")
def speech_recognize(file: UploadFile = File(...)):
    data, ext, mime = read_upload(file,"audio")
    key = f"{uuid4().hex}{ext}"
    store = ObjectStore()
    full_key = _external(lambda: store.upload(key, data, content_type=mime))
    url = _external(lambda: store.presigned_url(full_key))
    try:
        result = _external(lambda: SpeechClient().transcribe(url))
        return {k:v for k,v in result.items() if k not in ("raw","input_url")}
    finally:
        store.delete(full_key)


# ---- 物品纪念/寄存(聊天工具的上传落库) ----

@router.post("/item/upload")
def item_upload(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    intent: str = Form(...),
    item_name: str = Form(""),
    description: str = Form(""),
    companion_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    request_id: str | None = Form(None),
    db: Session = Depends(get_session),
):
    data, ext, mime = read_upload(file,"image")
    import io
    from PIL import Image
    image_buffer=io.BytesIO()
    with Image.open(io.BytesIO(data)) as image: image.convert("RGBA").save(image_buffer,format="PNG")
    data=image_buffer.getvalue()
    store.get_or_create_user(db, user_id)
    result = _external(lambda: item_mod.handle_upload(
        db, user_id, data, item_name.strip(), intent.strip(),
        description.strip(), LLMClient(), VisionClient(), ObjectStore(),
    ))


    from memory.models import ItemMemory
    row=db.get(ItemMemory,result["item_id"])
    row.client_request_id=db.info.get("request_id")
    return {**result,"item":item_view(row)}


@router.get("/item/{user_id}")
def item_list(user_id: str, companion_id: str | None = Query(None),
                limit: int | None = Query(None,ge=1,le=100), cursor: str | None = Query(None),
                db: Session = Depends(get_session)):
    if limit is None and cursor is None:
        return {"items": [item_view(r) for r in store.list_item_memories(db,user_id,companion_id=companion_id)]}
    from memory.models import ItemMemory
    rows,meta=collection_page(db,ItemMemory,collection="items",limit=limit,cursor=cursor)
    return {"items":[item_view(r) for r in rows],**meta}


# ---- 场景照片(拍立得,聊天工具的上传落库) ----

@router.post("/photo/upload")
def photo_upload(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    companion_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    request_id: str | None = Form(None),
    db: Session = Depends(get_session),
):
    data, ext, mime = read_upload(file,"image")
    store.get_or_create_user(db, user_id)
    result = _external(lambda: photo_mod.handle_upload(
        db, user_id, data, title.strip(), description.strip(),
        content_type=mime, obj_store=ObjectStore(),
    ))


    from memory.models import PhotoMemory
    row=db.get(PhotoMemory,result["photo_id"])
    row.client_request_id=db.info.get("request_id")
    return {**result,"photo":photo_view(row)}


@router.get("/photo/{user_id}")
def photo_list(user_id: str, companion_id: str | None = Query(None),
                limit: int | None = Query(None,ge=1,le=100), cursor: str | None = Query(None),
                db: Session = Depends(get_session)):
    if limit is None and cursor is None:
        return {"photos": [photo_view(r) for r in store.list_photo_memories(db,user_id,companion_id=companion_id)]}
    from memory.models import PhotoMemory
    rows,meta=collection_page(db,PhotoMemory,collection="photos",limit=limit,cursor=cursor)
    return {"photos":[photo_view(r) for r in rows],**meta}


# ---- 树洞信箱 ----

class TreeholeLetterReq(RequestBase):
    user_id: str | None = None
    content: str


@router.post("/treehole/letter")
def treehole_write_letter(req: TreeholeLetterReq, db: Session = Depends(get_session)):
    return treehole_mod.write_letter(db, req.user_id, req.content, LLMClient())


class TreeholeReplyReq(RequestBase):
    user_id: str | None = None
    letter_id: str
    content: str


@router.post("/treehole/reply")
def treehole_submit_reply(req: TreeholeReplyReq, db: Session = Depends(get_session)):
    try:
        return treehole_mod.submit_reply(db, req.user_id, req.letter_id, req.content, LLMClient())
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---- 树洞审核(后台,暂无鉴权) ----

@router.get("/treehole/review/pending")
def treehole_review_pending(db: Session = Depends(get_session)):
    return {"replies": treehole_mod.review_pending(db)}


@router.post("/treehole/review/{reply_id}/approve")
def treehole_review_approve(reply_id: str, db: Session = Depends(get_session)):
    try:
        return treehole_mod.approve_reply(db, reply_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/treehole/review/{reply_id}/reject")
def treehole_review_reject(reply_id: str, db: Session = Depends(get_session)):
    try:
        return treehole_mod.reject_reply(db, reply_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---- 树洞:打开 App 的一次性弹窗 + 看板 ----

@router.get("/treehole/popup/{user_id}")
def treehole_popup(user_id: str, db: Session = Depends(get_session)):
    """打开 App 时调用:返回当前该弹的树洞弹窗(写信/回信邀请/收到回信,各一次)。"""
    store.get_or_create_user(db, user_id)
    return treehole_mod.popups(db, user_id, LLMClient())


class TreeholeSeenReq(RequestBase):
    kind: str
    ref_id: str | None = None


@router.post("/treehole/popup/{user_id}/seen")
def treehole_popup_seen(user_id: str, req: TreeholeSeenReq, db: Session = Depends(get_session)):
    return treehole_mod.mark_popup_seen(db, user_id, req.kind, req.ref_id)


@router.get("/treehole/letters/{user_id}")
def treehole_letters(user_id: str, companion_id:str|None=Query(None),
                     limit:int|None=Query(None,ge=1,le=100),cursor:str|None=Query(None),db: Session = Depends(get_session)):
    if limit is None and cursor is None:return {"letters":treehole_mod.my_letters(db,user_id)}
    from memory.models import TreeholeLetter
    rows,meta=collection_page(db,TreeholeLetter,collection="letters",stamp=TreeholeLetter.created_at,limit=limit,cursor=cursor)
    return {"letters":treehole_mod.my_letters(db,user_id,rows=rows),**meta}



# ---- 树洞运营后台(暂无鉴权,同 review 系列) ----

@router.get("/treehole/admin/letters")
def treehole_admin_letters(db: Session = Depends(get_session)):
    """运营后台:查看所有来信。"""
    return {"letters": treehole_mod.admin_letters(db)}


class TreeholeAdminReplyReq(RequestBase):
    letter_id: str
    content: str


@router.post("/treehole/admin/reply")
def treehole_admin_reply(req: TreeholeAdminReplyReq, db: Session = Depends(get_session)):
    """运营后台:给来信回信(直达,免审核)。"""
    try:
        return treehole_mod.admin_reply(db, req.letter_id, req.content, LLMClient())
    except ValueError as e:
        raise HTTPException(404, str(e))


class InterviewStartReq(RequestBase):
    user_id: str | None = None
    loss_type: str = "breakup"
    question_key: str | None = None


class InterviewAnswerReq(RequestBase):
    session_id: str
    answer: str = Field(min_length=1,max_length=2000)


@router.post("/interview/start")
def interview_start(req: InterviewStartReq, db: Session = Depends(get_session)):
    active = store.get_active_interview(db, req.user_id)
    if active is not None: return interview_mod.progress(active)
    result=interview_mod.start(db, req.user_id, req.loss_type, req.question_key)
    return {**result,"companion_id":db.info.get("companion_id"),"status":"active","phase":"active","done":False,"report_ready":False}


@router.post("/interview/answer")
def interview_answer(req: InterviewAnswerReq, db: Session = Depends(get_session)):
    try:
        # 报告生成放后台(时延几十秒),先即时返回,前端轮询 /interview/{session_id} 拿 report_ready
        return interview_mod.answer(db, req.session_id, req.answer, LLMClient(), async_report=True)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/interview/{session_id}")
def interview_state(session_id: str, db: Session = Depends(get_session)):
    s = store.get_interview(db, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    return interview_mod.progress(s)


# ---- 用户引导阶段 + 初始报告看板 ----

@router.post("/onboarding/{user_id}/enter-main")
def onboarding_enter_main(user_id: str, db: Session = Depends(get_session)):
    """用户在报告页确认后进入主界面:report → main。同步映射 user_states.first_report_status=pinned。"""
    store.get_or_create_user(db, user_id)
    try:
        onboarding_mod.transition(db, user_id, "main")
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.update_user_state(db, user_id, first_report_status="pinned")
    return {"user_id": user_id, "phase": "main"}


@router.get("/initial-report/{user_id}")
def initial_report(user_id: str, companion_id: str | None = Query(None), db: Session = Depends(get_session)):
    """看板:返回初始报告的用户视图(正文 + 分手关系分析 + 生成时间)。"""
    store.get_or_create_user(db, user_id)
    row = store.get_latest_report_row(db, user_id, "initial", companion_id=companion_id)
    if row is None:
        interview = store.get_active_interview(db, user_id)
        state = (interview.state or {}) if interview else {}
        return {"status": "failed" if state.get("report_failed") else "generating" if state.get("generating") else "not_started", "report_ready": False}
    local = row.created_at + timedelta(hours=get_settings().timezone_offset_hours)
    return {**onboarding_mod.initial_report_view(row.content, created_at=local.isoformat()), "status": "ready", "report_ready": True, "id": row.id, "companion_id": row.companion_id}


# ---- 主界面聚合 ----

@router.get("/home/{user_id}")
def home(user_id: str, db: Session = Depends(get_session)):
    """主界面一次返回:情绪日历 + 软引导(无触发回落通用语录) + 今日主题。"""
    return home_mod.get_home(db, user_id, LLMClient())


# ---- 周报 ----

@router.get("/weekly-report/{user_id}/due")
def weekly_report_due(user_id: str, db: Session = Depends(get_session)):
    """打开 App 时调用:返回是否要弹本周周报(需要时后台生成)。"""
    store.get_or_create_user(db, user_id)
    return weekly_mod.due(db, user_id)


@router.get("/weekly-report/{user_id}/{week_key}")
def weekly_report_get(user_id: str, week_key: str, db: Session = Depends(get_session)):
    data = weekly_mod.get(db, user_id, week_key)
    if data is None:
        raise HTTPException(404, "本周周报不存在")
    return data


@router.get("/weekly-report/{user_id}")
def weekly_report_list(user_id: str, companion_id: str | None = Query(None),
                       limit: int | None = Query(None,ge=1,le=100),cursor: str | None = Query(None),
                       db: Session = Depends(get_session)):
    if limit is None and cursor is None:return {"reports":weekly_mod.list_reports(db,user_id)}
    from memory.models import WeeklyReport
    rows,meta=collection_page(db,WeeklyReport,collection="weekly_reports",stamp=WeeklyReport.created_at,limit=limit,cursor=cursor)
    return {"reports":[{"id":r.id,"companion_id":r.companion_id,"week_key":r.week_key,"seen":r.seen_at is not None,"created_at":iso(r.created_at),"report":r.content} for r in rows],**meta}


@router.get("/reports/{user_id}")
def reports_list(user_id:str,companion_id:str|None=Query(None),kind:str|None=Query(None),
                 limit:int|None=Query(None,ge=1,le=100),cursor:str|None=Query(None),db:Session=Depends(get_session)):
    from memory.models import Report
    if kind not in (None,"initial","tracking"):fail(422,"INVALID_REPORT_KIND","报告类型无效")
    filters=[Report.kind==kind] if kind else []
    meta={}
    if limit is None and cursor is None:
        rows=db.scalars(select(Report).where(Report.user_id==user_id,Report.companion_id==db.info.get("companion_id"),*filters).order_by(Report.created_at.desc(),Report.id.desc())).all()
    else:
        rows,meta=collection_page(db,Report,collection="reports:"+(kind or "all"),stamp=Report.created_at,filters=filters,limit=limit,cursor=cursor)
    return {"reports":[{"id":r.id,"kind":r.kind,"companion_id":r.companion_id,"created_at":iso(r.created_at),"report":onboarding_mod.initial_report_view(r.content,created_at=iso(r.created_at)) if r.kind=="initial" else r.content} for r in rows],**meta}



class WeeklySeenReq(RequestBase):
    week_key: str


@router.post("/weekly-report/{user_id}/seen")
def weekly_report_seen(user_id: str, req: WeeklySeenReq, db: Session = Depends(get_session)):
    """关闭周报弹窗时调用,标记已看,本周不再弹。"""
    return weekly_mod.mark_seen(db, user_id, req.week_key)


# ---- 鉴权:用户名 + 密码(哈希存储 + 会话 token) ----

class AuthRegisterReq(RequestBase):
    username: str
    password: str


class AuthLoginReq(RequestBase):
    username: str
    password: str


class AuthLogoutReq(RequestBase):
    token: str


class WeChatLoginReq(RequestBase):
    code: str   # wx.login() 拿到的一次性登录凭证


def _normalize_username(username: str) -> str:
    return username.strip().lower()


@router.post("/auth/register")
def auth_register(req: AuthRegisterReq, db: Session = Depends(get_session)):
    username = _normalize_username(req.username)
    password = req.password
    if len(username) < 2 or len(username) > 32:
        raise HTTPException(422, "用户名需 2–32 个字符")
    if len(password) < 6:
        raise HTTPException(422, "密码至少 6 位")
    if store.get_auth_by_username(db, username) is not None:
        raise HTTPException(409, "该用户名已被注册")
    hash_hex, salt_hex = hash_password(password)
    user_id = uuid4().hex
    auth = store.register_user(db, user_id=user_id, username=username,
                               password_hash=hash_hex, password_salt=salt_hex)
    token = new_token()
    store.create_session(db, user_id, token, new_expiry())
    return {"token": token, "user_id": user_id, "username": auth.username}


@router.post("/auth/login")
def auth_login(req: AuthLoginReq, db: Session = Depends(get_session)):
    username = _normalize_username(req.username)
    auth = store.get_auth_by_username(db, username)
    if auth is None or not verify_password(req.password, auth.password_salt, auth.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if db.get(User, auth.user_id).account_status != "active": fail(403,"ACCOUNT_DISABLED","账号已停用")
    token = new_token()
    store.create_session(db, auth.user_id, token, new_expiry())
    return {"token": token, "user_id": auth.user_id, "username": auth.username}


@router.post("/auth/wechat")
def auth_wechat(req: WeChatLoginReq, db: Session = Depends(get_session)):
    """微信小程序登录:code → code2session → openid → get_or_create 用户 → 签发 token。

    复用账号密码一致的会话机制(AuthSession + Bearer token);微信用户无 username,
    user_id 直接取 openid。mock 模式下 code 确定性派生 openid,供上线前离线联调。
    """
    client = WeChatClient()
    try:
        session = client.code2session(req.code)
    except WeChatError as e:
        fail(401,"WECHAT_LOGIN_FAILED","微信登录失败，请重试")
    openid = session["openid"]
    user = store.get_or_create_user(db, openid)
    if user.account_status != "active": fail(403,"ACCOUNT_DISABLED","账号已停用")
    token = new_token()
    store.create_session(db, openid, token, new_expiry())
    return {"token": token, "user_id": openid, "openid": openid,
            "unionid": session.get("unionid"), "mock": client.mock}


@router.post("/auth/logout")
def auth_logout(req: AuthLogoutReq, db: Session = Depends(get_session)):
    store.delete_session(db, req.token)
    return {"ok": True}


@router.get("/auth/me")
def auth_me(user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    auth = store.get_auth_by_user(db, user_id)
    user = db.get(User, user_id)
    return {
        "user_id": user_id,
        "username": auth.username if auth else None,
        "nickname": user.nickname if user else None,
        **membership(user),
        "login_type": "wechat" if auth is None else "password",
    }


class AccountPatchReq(RequestBase):
    nickname: str


class ActivationRedeemReq(RequestBase):
    code: str


@router.patch("/account")
def account_patch(req: AccountPatchReq, user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    """更新账号昵称(同步到 005 菜单抽屉问候语)。"""
    nickname = (req.nickname or "").strip()
    if not (1 <= len(nickname) <= 24):
        raise HTTPException(422, "昵称需 1–24 个字符")
    store.update_user(db, user_id, nickname=nickname)
    return {"nickname": nickname}


@router.post("/activation/redeem")
def activation_redeem(req: ActivationRedeemReq, user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    """激活码兑换会员(一码一次,暂不门禁功能)。"""
    return b07.redeem(db, user_id, req.code)


# ---- 单用户状态(N1–N5,走 token 鉴权) ----

class StatePatchReq(RequestBase):
    first_letter_status: str | None = None       # pending|opened
    first_report_status: str | None = None       # pending|pinned
    home_intro_seen: bool | None = None
    board_intro_seen: bool | None = None
    tts_enabled: bool | None = None
    tts_intro_seen: bool | None = None
    tts_speed: float | None = None
    tts_vol: float | None = None
    echo_enabled: bool | None = None
    subject_name: str | None = None
    dismissed_moods: list | None = None
    pending_events: list | None = None


class RelationshipInferReq(RequestBase):
    answer: str = Field(min_length=1,max_length=2000)


class RelationshipSetReq(RequestBase):
    relationship_type: str
    subject_name: str | None = None


class EndingCommitReq(RequestBase):
    companion_id: str
    expected_revision: int = Field(ge=1)
    ritual: str  # dissolved | buried | skipped


def _iso(dt) -> str | None:
    return iso(dt)


def _state_payload(db: Session, s: UserState, user_id: str) -> dict:
    companion = store.get_current_companion(db, user_id)
    user = db.get(User, user_id)
    active = store.get_active_interview(db,user_id)
    return {
        "capabilities": {"diary_update": True},
        "chat_session_id": companion.chat_session_id if companion else None,
        "companion_revision": companion.revision if companion else None,
        "interview": {"session_id": active.id, "phase": active.status, "report_ready": bool((active.state or {}).get("report_ready"))} if active else None,
        "user_id": user_id,
        "nickname": user.nickname if user else None,
        **membership(user),
        "current_companion_id": companion.id if companion else s.current_companion_id,
        "relationship_type": companion.relationship_type if companion else s.relationship_type,
        "relationship_type_source": companion.relationship_type_source if companion else s.relationship_type_source,
        "relationship_type_confidence": companion.relationship_type_confidence if companion else s.relationship_type_confidence,
        "subject_name": companion.subject_name if companion else s.subject_name,
        "first_letter_status": s.first_letter_status,
        "first_report_status": s.first_report_status,
        "home_intro_seen": s.home_intro_seen,
        "board_intro_seen": s.board_intro_seen,
        "tts_enabled": s.tts_enabled,
        "tts_intro_seen": s.tts_intro_seen,
        "tts_speed": s.tts_speed if s.tts_speed is not None else 1.0,
        "tts_vol": s.tts_vol if s.tts_vol is not None else 1.0,
        "echo_enabled": True if s.echo_enabled is None else s.echo_enabled,
        "dismissed_moods": s.dismissed_moods,
        "pending_events": s.pending_events,
        "ending_stage": companion.ending_stage if companion else s.ending_stage,
        "ending_started_at": _iso(companion.ending_started_at if companion else s.ending_started_at),
        "ending_ritual": companion.ending_ritual if companion else s.ending_ritual,
        "ending_committed_at": _iso(companion.ending_committed_at if companion else s.ending_committed_at),
        "archived_at": _iso(companion.archived_at if companion else s.archived_at),
        "companions": [
            {
                "id": c.id, "revision": c.revision,
                "relationship_type": c.relationship_type,
                "subject_name": c.subject_name,
                "started_at": _iso(c.started_at),
                "archived_at": _iso(c.archived_at),
                "ending_stage": c.ending_stage,
                "ending_ritual": c.ending_ritual,
            }
            for c in store.list_companions(db, user_id)
        ],
        "destination": _destination(db, s, user_id),
    }


def _destination(db: Session, s: UserState, user_id: str) -> str:
    """登录分流:018(已归档只读) / 005(活动陪伴) / 002(未完成引导)。"""
    companion = store.get_current_companion(db, user_id)
    if (companion.archived_at if companion else s.archived_at) is not None:
        return "018"
    if s.home_intro_seen or s.board_intro_seen or s.first_report_status == "pinned":
        return "005"
    active = store.get_active_interview(db,user_id)
    if active: return "004" if active.status=="completed" else "003"
    return "002"


@router.get("/state")
def get_state(user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    store.get_or_create_user(db, user_id)
    s = store.get_or_create_user_state(db, user_id)
    c = store.get_or_create_current_companion(db,user_id)
    db.info["companion_id"] = c.id
    return _state_payload(db, s, user_id)


@router.patch("/state")
def patch_state(req: StatePatchReq, user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    fields = {k: v for k, v in req.model_dump().items() if v is not None and k not in ("request_id","client_request_id","companion_id")}
    subject_name = fields.pop("subject_name", None)
    if subject_name is not None:
        companion = store.get_or_create_current_companion(db, user_id)
        store.update_companion(db, companion.id, subject_name=subject_name.strip() or None)
    s = store.update_user_state(db, user_id, **fields)
    return _state_payload(db, s, user_id)


@router.post("/relationship/infer")
def relationship_infer(req: RelationshipInferReq, user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    """003 首问「Ta 是谁」后调用:AI 三分类,高置信度才写库,否则返回 null 让前端弹窗兜底。"""
    result = relationship_mod.infer(LLMClient(), req.answer)
    rt, conf = result["relationship_type"], result["confidence"]
    adopted = rt is not None and conf is not None and conf >= relationship_mod.ADOPT_CONFIDENCE
    if adopted:
        companion = store.get_or_create_current_companion(db, user_id)
        store.update_companion(db, companion.id, relationship_type=rt,
                               relationship_type_source="inferred",
                               relationship_type_confidence=conf)
    return {"relationship_type": rt if adopted else None,
            "confidence": conf if adopted else None,
            "adopted": adopted}


@router.post("/relationship")
def relationship_set(req: RelationshipSetReq, user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    """兜底弹窗 / 手动修正:显式设置陪伴类型(source=manual)。"""
    rt = store.normalize_relationship_type(req.relationship_type)
    if rt is None:
        raise HTTPException(422, "关系类型需为 breakup / pet / relative")
    fields = {"relationship_type": rt, "relationship_type_source": "manual"}
    if req.subject_name is not None:
        fields["subject_name"] = req.subject_name.strip() or None
    companion = store.get_or_create_current_companion(db, user_id)
    store.update_companion(db, companion.id, **fields)
    s = store.get_or_create_user_state(db, user_id)
    return _state_payload(db, s, user_id)


@router.post("/ending/commit")
def ending_commit(req: EndingCommitReq, user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    """结束提交(幂等):置当前陪伴对象 ending_stage=complete + archived_at,进入只读档案态。"""
    if req.ritual not in ("dissolved", "buried", "skipped"):
        raise HTTPException(422, "ritual 需为 dissolved / buried / skipped")
    store.get_or_create_user(db, user_id)
    s = store.get_or_create_user_state(db, user_id)
    companion = store.get_current_companion(db, user_id)
    if not companion or companion.id != req.companion_id: fail(409,"COMPANION_CHANGED","当前陪伴已变化")
    if companion.revision != req.expected_revision: fail(409,"REVISION_CONFLICT","陪伴版本已变化",revision=companion.revision)
    if companion.archived_at: fail(409,"COMPANION_ARCHIVED","陪伴已归档")
    if not companion.relationship_type:
        raise HTTPException(400, "请先确认这段陪伴的类型")
    store.archive_companion(db, companion.id, req.ritual)
    return _state_payload(db, s, user_id)


# ---- 陪伴对象(单用户多对象) ----

class CompanionCreateReq(RequestBase):
    previous_companion_id: str | None = None
    expected_revision: int | None = None
    relationship_type: str | None = None
    subject_name: str | None = None


@router.get("/companions")
def companions_list(user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    """列出当前用户全部陪伴对象。"""
    store.get_or_create_user(db, user_id)
    return {"companions": [
        {
            "id": c.id, "revision": c.revision,
            "relationship_type": c.relationship_type,
            "subject_name": c.subject_name,
            "started_at": _iso(c.started_at),
            "archived_at": _iso(c.archived_at),
            "ending_stage": c.ending_stage,
            "ending_ritual": c.ending_ritual,
        }
        for c in store.list_companions(db, user_id)
    ]}


@router.post("/companions")
def companions_create(req: CompanionCreateReq, user_id: str = Depends(get_current_user), db: Session = Depends(get_session)):
    """开始新的陪伴:新建对象并切换为当前(旧对象保持只读/归档不受影响)。"""
    store.get_or_create_user(db, user_id)
    rt = store.normalize_relationship_type(req.relationship_type) if req.relationship_type else None
    subject_name = (req.subject_name or "").strip() or None
    old = store.get_current_companion(db,user_id)
    if old and not old.archived_at: fail(409,"ACTIVE_COMPANION_EXISTS","请先归档当前陪伴",companion_id=old.id)
    if old and req.previous_companion_id and old.id != req.previous_companion_id: fail(409,"COMPANION_CHANGED","当前陪伴已变化")
    if old and req.expected_revision and old.revision != req.expected_revision: fail(409,"REVISION_CONFLICT","陪伴版本已变化")
    c = store.create_companion(db, user_id, relationship_type=rt, subject_name=subject_name)
    db.info["companion_id"] = c.id
    store.update_user_state(db,user_id,first_letter_status="pending",first_report_status="pending",home_intro_seen=False,board_intro_seen=False)
    store.upsert_onboarding(db,user_id,"new")
    s = store.get_or_create_user_state(db, user_id)
    return _state_payload(db, s, user_id)


# ---- 意见反馈(帮助与反馈页,轻量落库) ----

class FeedbackReq(RequestBase):
    content: str
    contact: str | None = None
    user_id: str | None = None


@router.post("/feedback")
def submit_feedback(req: FeedbackReq, db: Session = Depends(get_session)):
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(422, "反馈内容不能为空")
    row=store.add_feedback(db, content, user_id=db.info["user_id"], contact=(req.contact or "").strip() or None)
    return {"ok": True, "id":row.id}


# ---- 意见反馈(运营后台,暂无鉴权,同树洞后台) ----

@router.get("/feedback")
def feedback_list(db: Session = Depends(get_session)):
    """运营后台:全部反馈(含处理状态/回复),按提交时间倒序。"""
    offset = get_settings().timezone_offset_hours
    items = []
    for f in store.list_feedback(db):
        items.append({
            "id": f.id, "user_id": f.user_id, "content": f.content,
            "contact": f.contact, "status": f.status or "pending", "reply": f.reply,
            "created_at": (f.created_at + timedelta(hours=offset)).isoformat(),
        })
    return {"feedback": items}


class FeedbackUpdateReq(RequestBase):
    reply: str | None = None
    status: str | None = None


@router.patch("/feedback/{feedback_id}")
def feedback_update(feedback_id: str, req: FeedbackUpdateReq, db: Session = Depends(get_session)):
    """运营后台:记录处理回复 / 更新处理状态。"""
    try:
        row = store.update_feedback(db, feedback_id, reply=req.reply, status=req.status)
    except ValueError:
        raise HTTPException(404, "反馈不存在")
    return {"ok": True, "id": row.id, "status": row.status, "reply": row.reply}


# ---- 日记便利贴(「写下今天」) ----

class DiaryCreateReq(RequestBase):
    entry_date: str | None = None
    timezone: str = "Asia/Shanghai"
    user_id: str | None = None
    content: str
    emotion: str | None = None


@router.post("/diary")
def diary_create(req: DiaryCreateReq, db: Session = Depends(get_session)):
    """日记新增(便签即日记):落 diary_notes + 记忆流,单一事实源。"""
    return b07.diary_create(db,req)


@router.get("/diary/{user_id}")
def diary_list(user_id: str, companion_id: str | None = Query(None),
                limit: int | None = Query(None,ge=1,le=100), cursor: str | None = Query(None),
                db: Session = Depends(get_session)):
    if limit is None and cursor is None:
        return {"notes": [b07.diary_view(r) for r in store.list_diary_notes(db,user_id,companion_id=companion_id)]}
    from memory.models import DiaryNote
    rows,meta=collection_page(db,DiaryNote,collection="notes",limit=limit,cursor=cursor)
    return {"notes":[b07.diary_view(r) for r in rows],**meta}


# ---- 待办清单(聊天工具「走」的落库/勾选) ----

class TodoDoneReq(RequestBase):
    todo_id: str


class TodoCreateReq(RequestBase):
    user_id: str | None = None
    content: str


@router.post("/todo")
def todo_create(req: TodoCreateReq, db: Session = Depends(get_session)):
    """007 小事页「自己写一件」:追加到待办队列尾部。"""
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(422, "内容不能为空")
    if len(content) > 80:
        raise HTTPException(422, "一句话最多 80 字")
    store.get_or_create_user(db, req.user_id)
    row = store.add_todo(db, req.user_id, content)
    return {"id": row.id, "content": row.content, "done": row.done, "created_at": row.ts.isoformat()}


@router.post("/todo/done")
def todo_done(req: TodoDoneReq, db: Session = Depends(get_session)):
    """前端勾选待办 → 置 done=True(done_at 记完成时间),并写一条 type="todo" 记忆反哺功能恢复信号。"""
    row = todo_mod.mark_todo_done(db, req.todo_id)
    if row is None:
        raise HTTPException(404, "待办不存在")
    return row


@router.get("/todo/{user_id}")
def todo_list(user_id: str, companion_id:str|None=Query(None),
              limit:int|None=Query(None,ge=1,le=100),cursor:str|None=Query(None),bucket:str=Query("queue"),
              db: Session = Depends(get_session)):
    if limit is None and cursor is None:return todo_mod.queue_view(db,user_id)
    from memory.models import TodoItem
    from sqlalchemy import func, Integer
    if bucket not in ("queue","history"):fail(422,"INVALID_BUCKET","小事列表类型无效")
    signature=db.execute(select(func.count(TodoItem.id),func.sum(TodoItem.done.cast(Integer)),func.max(TodoItem.done_at)).where(TodoItem.user_id==user_id,TodoItem.companion_id==db.info.get("companion_id"))).one()
    revision=":".join(str(v) for v in signature)
    history=bucket=="history"
    stamp=func.coalesce(TodoItem.done_at,TodoItem.ts) if history else TodoItem.ts
    rows,meta=collection_page(db,TodoItem,collection="todo:"+bucket,stamp=stamp,ascending=not history,
                             filters=[TodoItem.done==history],revision=revision,limit=limit,cursor=cursor)
    result={"queue":[],"history":[],"bucket":bucket,**meta}
    result[bucket]=[{"id":r.id,"companion_id":r.companion_id,"content":r.content,"done":r.done,
                    "created_at":iso(r.ts),"done_at":iso(r.done_at or r.ts) if r.done else None} for r in rows]
    return result



# ---- 结束仪式 AI 纪念文案(019/020 结束流程) ----

class EndingContentReq(RequestBase):
    user_id: str | None = None


@router.post("/ending/content")
def ending_content(req: EndingContentReq, db: Session = Depends(get_session)):
    store.get_or_create_user(db, req.user_id)
    return ending_mod.memorial(db, req.user_id, LLMClient())


# ---- 回声壁(随机捕获富有情感的句子,随机延时后回显) ----

class EchoSeenReq(RequestBase):
    echo_id: str
    dismiss: bool = False  # True=用户划掉,不再回显;False=已看


@router.get("/echo/popup/{user_id}")
def echo_popup(user_id: str, db: Session = Depends(get_session)):
    """拉取式推送:打开 App / 轮询时返回 reveal_at 已到期的最早一条回声;无则 {"echo": null}。"""
    return echo_mod.get_echo_popup(db, user_id)


@router.post("/echo/popup/{user_id}/seen")
def echo_popup_seen(user_id: str, req: EchoSeenReq, db: Session = Depends(get_session)):
    """展示后标记已看(或划掉),之后不再回显该条。"""
    return echo_mod.mark_seen(db, user_id, req.echo_id, dismiss=req.dismiss)


@router.get("/health")
def health():
    return {"status": "ok"}
