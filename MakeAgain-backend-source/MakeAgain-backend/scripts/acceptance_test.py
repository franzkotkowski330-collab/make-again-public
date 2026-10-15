"""联调验收:走真实 HTTP 路由,验证前端→后端整链(全量)。

运行前置:后端已启动 `python -m uvicorn main:app --host 127.0.0.1 --port 8000`(无 --reload)。
运行(在 backend 目录下):
    python scripts/acceptance_test.py

覆盖链(与前端主链一一对应):
  鉴权 → 状态 → 关系 → 访谈(start/answer 多轮 → 轮询 report_ready)
  → 初始报告 → enter-main → 聊天×2(带 session_id) → 树洞资格(2 会话)
  → 树洞写 → 第二用户来信 → 树洞 matches → 树洞回信 → 树洞看板
  → 主页/情绪日历/历史 → 物件/照片 list → 结束归档 → 登出。

说明:
- 用 urllib(Windows 下 curl 带中文会失败),请求体 json.dumps(ensure_ascii=False).encode("utf-8")。
- 每次运行注册全新用户名,可重复执行;关系类型走显式 /relationship(确定性,不依赖 LLM)。
- MOCK_LLM=false 时,访谈/聊天/报告/树洞均走真实 LLM,给足超时;报告生成放后台,需轮询。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:8000/api"

PASS = 0
FAIL = 0
FAILURES: list[str] = []
T0 = time.time()

# 真实 LLM 时延较高,给足超时;报告生成在后台,轮询上限。
LLM_TIMEOUT = 180
REPORT_POLL_MAX = 60   # 60 × 3s = 180s
REPORT_POLL_INTERVAL = 3


def _ts() -> str:
    return f"[{int(time.time() - T0):>3}s]"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  {_ts()} [PASS] {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  {_ts()} [FAIL] {name}  {detail}")


def section(title: str) -> None:
    print(f"\n{_ts()} ── {title} ──")


def req(method: str, path: str, body: dict | None = None, token: str | None = None,
        timeout: int = 20) -> tuple[int, dict]:
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw}
    except Exception as e:  # noqa: BLE001
        return -1, {"_error": repr(e)}


def register(username: str, pwd: str) -> tuple[str, str]:
    """注册新用户,返回 (uid, token)。"""
    st, d = req("POST", "/auth/register", {"username": username, "password": pwd})
    return (d.get("user_id", ""), d.get("token", "")) if st == 200 else ("", "")


def set_relationship(token: str, rt: str, name: str) -> bool:
    st, _ = req("POST", "/relationship", {"relationship_type": rt, "subject_name": name}, token=token)
    return st == 200


def do_chat(uid: str, session_id: str, message: str) -> bool:
    st, d = req("POST", "/chat", {"user_id": uid, "message": message, "session_id": session_id},
                timeout=LLM_TIMEOUT)
    return st == 200 and isinstance(d.get("reply"), str) and bool(d.get("reply"))


def treehole_write(uid: str, content: str) -> tuple[bool, dict]:
    st, d = req("POST", "/treehole/letter", {"user_id": uid, "content": content}, timeout=LLM_TIMEOUT)
    return (st == 200 and d.get("ok") is True), d


def main() -> None:
    print("=" * 64)
    print("「重逢」联调验收 · HTTP 全量整链")
    print("=" * 64)

    uname = "acc_" + str(int(time.time()))
    pwd = "secret123"

    # 0. 健康
    st, d = req("GET", "/health")
    check("健康检查 /health", st == 200 and d.get("status") == "ok", f"st={st}")

    # ================= 鉴权 =================
    section("1 · 鉴权(register/login/logout/me)")
    st, d = req("POST", "/auth/register", {"username": uname, "password": pwd})
    check("注册返回 token+user_id", st == 200 and d.get("token") and d.get("user_id"), f"st={st}")
    uid = d.get("user_id", "")
    token = d.get("token", "")

    st, _ = req("POST", "/auth/register", {"username": uname, "password": pwd})
    check("重复注册 → 409", st == 409, f"st={st}")

    st, _ = req("POST", "/auth/register", {"username": "x_" + uname, "password": "123"})
    check("密码<6 → 422", st == 422, f"st={st}")

    st, _ = req("POST", "/auth/login", {"username": uname, "password": "wrongpw"})
    check("错误密码 → 401", st == 401, f"st={st}")

    st, d = req("POST", "/auth/login", {"username": uname, "password": pwd})
    check("正确登录 → 200", st == 200 and d.get("token"), f"st={st}")
    token2 = d.get("token", "")

    st, d = req("GET", "/auth/me", token=token2)
    check("me 返回 user_id+username", st == 200 and d.get("username") == uname, f"st={st}")

    st, _ = req("GET", "/state")
    check("无 token /state → 401", st == 401, f"st={st}")

    # ================= 状态 + 关系 =================
    section("2 · 状态 + 关系类型")
    st, d = req("GET", "/state", token=token2)
    check("state 初始无关系类型", st == 200 and d.get("relationship_type") is None, f"st={st}")

    st, d = req("POST", "/relationship", {"relationship_type": "breakup", "subject_name": "林薇"}, token=token2)
    check("设置关系类型 breakup", st == 200 and d.get("relationship_type") == "breakup"
          and d.get("relationship_type_source") == "manual", f"st={st}")

    st, _ = req("POST", "/relationship", {"relationship_type": "friend"}, token=token2)
    check("非法关系类型 → 422", st == 422, f"st={st}")

    # ================= 访谈 =================
    section("3 · 访谈(start → answer 多轮 → 轮询报告)")
    st, d = req("POST", "/interview/start", {"user_id": uid, "loss_type": "breakup"}, timeout=20)
    ok_start = st == 200 and d.get("session_id") and d.get("question")
    check("访谈 start 返回 session_id+首问", ok_start, f"st={st} d={(str(d)[:60]) if d else d}")
    session_id = d.get("session_id", "")

    # 回答多轮:每次都带明确目标倾向(goal_signal),加速收尾;上限 MAX_TURNS=20。
    answer_text = "我想带着这段记忆继续往前走,不想忘记TA,TA 是我很重要的人。"
    rounds = 0
    completed = False
    for i in range(20):
        st, r = req("POST", "/interview/answer",
                    {"session_id": session_id, "answer": answer_text}, timeout=LLM_TIMEOUT)
        rounds = i + 1
        if st != 200:
            check(f"访谈回答第{i + 1}轮(HTTP {st})", False, f"st={st} r={r}")
            break
        action = r.get("action")
        if action == "complete":
            completed = True
            print(f"  {_ts()}      访谈第 {i + 1} 轮完成: {str(r)[:80]}")
            break
    check(f"访谈完成(action=complete,共 {rounds} 轮)", completed, f"rounds={rounds}")

    # 轮询报告就绪(后台生成)
    report_ready = False
    for _ in range(REPORT_POLL_MAX):
        st, r = req("GET", f"/interview/{session_id}", timeout=20)
        if st == 200 and r.get("report_ready") is True:
            report_ready = True
            break
        time.sleep(REPORT_POLL_INTERVAL)
    check("报告后台生成完成 report_ready", report_ready, f"session={session_id}")

    # ================= 初始报告 + 进入主界面 =================
    section("4 · 初始报告 + enter-main")
    st, d = req("GET", f"/initial-report/{uid}")
    ok_report = st == 200 and d.get("title") and d.get("summary") and d.get("quote")
    check("初始报告视图 title+summary+quote", ok_report, f"st={st} keys={sorted(d.keys()) if isinstance(d, dict) else d}")
    check("分手报告含 relationship_analysis", st == 200 and isinstance(d.get("relationship_analysis"), dict),
          f"st={st}")

    st, d = req("POST", f"/onboarding/{uid}/enter-main")
    check("enter-main → phase=main", st == 200 and d.get("phase") == "main", f"st={st} d={d}")

    st, s = req("GET", "/state", token=token2)
    check("state first_report_status=pinned", st == 200 and s.get("first_report_status") == "pinned", f"st={st}")

    # ================= 聊天(2 次会话) =================
    section("5 · 聊天 ×2(带 session_id,构成「2 会话」)")
    ok1 = do_chat(uid, "sess_a", "我最近总想起她,尤其是晚上。")
    check("聊天第 1 次会话", ok1, f"uid={uid}")
    ok2 = do_chat(uid, "sess_b", "我想把没说完的话慢慢说给自己听。")
    check("聊天第 2 次会话", ok2, f"uid={uid}")

    # ================= 主页 / 日历 / 历史 =================
    section("6 · 主页 / 情绪日历 / 聊天历史")
    st, d = req("GET", f"/home/{uid}", timeout=LLM_TIMEOUT)
    ok_home = st == 200 and "calendar" in d and "nudges" in d and "themes" in d
    check("主页聚合 calendar+nudges+themes", ok_home, f"st={st} keys={sorted(d.keys()) if isinstance(d, dict) else d}")

    st, d = req("GET", f"/chat/history/{uid}/days")
    ok_days = st == 200 and isinstance(d.get("days"), list) and len(d.get("days", [])) >= 1
    check("聊天历史天数非空", ok_days, f"st={st}")

    day_date = ""
    if isinstance(d.get("days"), list) and d["days"]:
        day_date = d["days"][0].get("date", "")
    st, dd = req("GET", f"/chat/history/{uid}/day/{day_date}") if day_date else (-1, {})
    ok_day = st == 200 and isinstance(dd.get("messages"), list) and len(dd.get("messages", [])) >= 2
    check("聊天历史当天消息(≥2)", ok_day, f"st={st} date={day_date}")

    # ================= 树洞:资格 → 写 → 第二用户 → 匹配 → 回信 =================
    section("7 · 树洞(写信 → 回信 → 看板 → 弹窗)")
    ok_w, dw = treehole_write(uid, "奶奶走后,我总觉得心里空了一块,有些话没来得及说。")
    check("树洞写信成功(ok=true + letter_id)", ok_w and dw.get("letter_id"), f"d={dw}")
    letter_id = dw.get("letter_id", "")

    # 第二用户:注册 + 关系 + 2 次聊天 + 写信(供主用户回信)
    uname2 = "acc2_" + str(int(time.time()))
    uid2, token2b = register(uname2, pwd)
    ok_reg2 = bool(uid2) and bool(token2b)
    check("第二用户注册", ok_reg2, f"uid2={uid2}")
    if ok_reg2:
        ok_rel2 = set_relationship(token2b, "breakup", "苏遥")
        check("第二用户设置关系", ok_rel2, "")
        ok_c2a = do_chat(uid2, "sess_a", "我也刚经历分开,白天还好,晚上特别难熬。")
        ok_c2b = do_chat(uid2, "sess_b", "想找人说说,又怕打扰别人。")
        check("第二用户聊天 ×2", ok_c2a and ok_c2b, "")
        ok_w2, dw2 = treehole_write(uid2, "和TA分开以后,总在半夜醒来,想不通为什么会走到这一步。")
        check("第二用户写信成功", ok_w2 and dw2.get("letter_id"), f"d={dw2}")
        letter2_id = dw2.get("letter_id", "")

        # 主用户:直接回信第二用户来信(写信/回信资格与匹配改由 /treehole/popup 聚合呈现)
        target = letter2_id
        if target:
            st, drp = req("POST", "/treehole/reply", {"user_id": uid, "letter_id": target,
                                                      "content": "慢慢来,你没有忘记,也没有一个人扛着。"},
                          timeout=LLM_TIMEOUT)
            check("回信提交(ok=true + reply_id)", st == 200 and drp.get("ok") is True and drp.get("reply_id"),
                  f"st={st} d={drp}")
        else:
            check("回信提交(有可回目标)", False, "无匹配来信")

        # 主用户:树洞看板(我写的信)
        st, dletters = req("GET", f"/treehole/letters/{uid}")
        check("树洞看板-我写的信", st == 200 and isinstance(dletters.get("letters"), list)
              and len(dletters.get("letters", [])) >= 1, f"st={st}")

        # 主用户:打开 App 一次性弹窗
        st, dpop = req("GET", f"/treehole/popup/{uid}", timeout=LLM_TIMEOUT)
        check("树洞弹窗聚合(返回 popups 列表)", st == 200 and isinstance(dpop.get("popups"), list), f"st={st}")

    # ================= 物件 / 照片 =================
    section("8 · 物件 / 照片看板(无上传时为空)")
    st, d = req("GET", f"/item/{uid}")
    check("物件 list 返回 items", st == 200 and isinstance(d.get("items"), list), f"st={st}")
    st, d = req("GET", f"/photo/{uid}")
    check("照片 list 返回 photos", st == 200 and isinstance(d.get("photos"), list), f"st={st}")

    # ================= 结束归档 =================
    section("9 · 结束归档")
    st, d = req("POST", "/ending/commit", {"ritual": "buried"}, token=token2)
    check("结束提交 → 归档(destination=018)", st == 200 and d.get("archived_at") is not None
          and d.get("destination") == "018", f"st={st}")

    st, d2 = req("POST", "/ending/commit", {"ritual": "dissolved"}, token=token2)
    check("幂等重提不覆盖 ritual", st == 200 and d2.get("ending_ritual") == "buried",
          f"st={st} ritual={d2.get('ending_ritual')}")

    st, _ = req("POST", "/ending/commit", {"ritual": "nope"}, token=token2)
    check("非法 ritual → 422", st == 422, f"st={st}")

    st, d = req("GET", "/state", token=token2)
    check("state 已归档 destination=018", st == 200 and d.get("destination") == "018", f"st={st}")

    # ================= 登出 =================
    section("10 · 登出")
    req("POST", "/auth/logout", {"token": token2})
    st, _ = req("GET", "/auth/me", token=token2)
    check("登出后 me → 401", st == 401, f"st={st}")

    # 汇总
    print("=" * 64)
    print(f"通过 {PASS} / {PASS + FAIL} · 耗时 {int(time.time() - T0)}s")
    if FAILURES:
        print("失败项:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("[OK] 联调验收通过")


if __name__ == "__main__":
    main()
