"""微信小程序登录网关:code2session(code → openid / session_key / unionid)。

无 appid/secret 或 mock_wechat 时进入 mock 模式,用 code 的确定性哈希派生 openid,
保证「上线前」无需真实微信调用即可端到端测试登录链路(同 code 恒同 openid)。
"""
from __future__ import annotations

import hashlib
import threading

import httpx

from config import get_settings


class WeChatError(Exception):
    """微信 code2session 调用失败(网络错误 / 微信返回 errcode)。"""


# 共享 httpx 连接池(线程安全):复用与微信服务器的连接,避免每请求重开 TLS。
_shared_client: httpx.Client | None = None
_shared_client_lock = threading.Lock()


def _get_shared_client() -> httpx.Client:
    global _shared_client
    if _shared_client is None:
        with _shared_client_lock:
            if _shared_client is None:
                _shared_client = httpx.Client(
                    timeout=get_settings().wechat_timeout,
                    limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
                )
    return _shared_client


class WeChatClient:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._client = _get_shared_client()

    @property
    def mock(self) -> bool:
        if self.settings.environment == "production" and self.settings.mock_wechat: raise RuntimeError("Production forbids mock gateway")
        if not self.settings.mock_wechat and not (self.settings.wechat_appid and self.settings.wechat_secret): raise RuntimeError("Gateway configuration missing")
        return self.settings.mock_wechat

    def code2session(self, code: str) -> dict:
        """code → {openid, session_key, unionid};失败抛 WeChatError。

        session_key 只供服务端解密用户信息使用,不下发客户端。
        """
        if self.mock:
            if not code:
                raise WeChatError("缺少 code")
            openid = "mock-openid-" + hashlib.sha1(code.encode("utf-8")).hexdigest()[:16]
            return {"openid": openid, "session_key": "", "unionid": None}

        url = self.settings.wechat_code2session_url
        params = {
            "appid": self.settings.wechat_appid,
            "secret": self.settings.wechat_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as e:
            raise WeChatError(f"微信服务网络错误: {e}") from e
        try:
            data = resp.json()
        except ValueError:
            raise WeChatError(f"微信服务返回非 JSON: {resp.text[:200]}")
        if data.get("errcode"):
            raise WeChatError(f"微信 code2session 错误 {data.get('errcode')}: {data.get('errmsg')}")
        openid = data.get("openid")
        if not openid:
            raise WeChatError("微信未返回 openid")
        return {
            "openid": openid,
            "session_key": data.get("session_key", ""),
            "unionid": data.get("unionid"),
        }
