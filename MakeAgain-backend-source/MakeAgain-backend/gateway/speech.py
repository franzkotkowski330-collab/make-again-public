"""语音网关:TTS(文本转语音)+ ASR(语音识别),经腾讯 MaaS 代理转发 MiniMax / 混元。

同步接口。无 key 或 mock_speech 时进入 mock(返回明确占位,不真正生成音频 / 调外部服务)。
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from config import get_settings


class SpeechError(Exception):
    """语音服务调用失败。"""


# 共享 httpx 连接池(线程安全):复用与 MaaS 语音网关的连接,避免每请求重开 TLS。
_shared_client: httpx.Client | None = None
_shared_client_lock = threading.Lock()


def _get_shared_client() -> httpx.Client:
    global _shared_client
    if _shared_client is None:
        with _shared_client_lock:
            if _shared_client is None:
                _shared_client = httpx.Client(
                    timeout=get_settings().speech_timeout,
                    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                )
    return _shared_client


def _extract(obj: Any, *keys: str) -> Any:
    """在未知响应结构里递归找第一个匹配 key 的值(防上游字段变动)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                return v
        for v in obj.values():
            found = _extract(v, *keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _extract(item, *keys)
            if found is not None:
                return found
    return None


class SpeechClient:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._client = _get_shared_client()

    @property
    def mock(self) -> bool:
        if self.settings.environment == "production" and self.settings.mock_speech: raise RuntimeError("Production forbids mock gateway")
        if not self.settings.mock_speech and not self.settings.maas_api_key: raise RuntimeError("Gateway configuration missing")
        return self.settings.mock_speech

    def _post(self, path: str, payload: dict[str, Any]) -> dict:
        url = self.settings.maas_base_url.rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {self.settings.maas_api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            raise SpeechError(f"语音服务网络错误: {e}") from e
        if resp.status_code != 200:
            detail = resp.text[:300]
            try:
                err = (resp.json().get("error") or {})
                detail = err.get("message_zh") or err.get("message") or detail
            except ValueError:
                pass
            raise SpeechError(f"语音服务返回 {resp.status_code}: {detail}")
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def tts(self, text: str, voice_id: str | None = None, speed: float = 1.0,
            vol: float = 1.0, pitch: float = 0.0) -> dict:
        """文本 → 语音,返回 {audio_url, mock, raw};audio_url 为可直接播放的音频地址。"""
        if self.mock:
            return {"audio_url": None, "mock": True, "text": text}

        payload = {
            "model": self.settings.tts_model,
            "text": text,
            "voice_setting": {
                "voice_id": voice_id or self.settings.tts_voice_id,
                "speed": speed,
                "vol": vol,
                "pitch": pitch,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
            "subtitle_enable": False,
            "output_format": "url",
        }
        data = self._post("/v1/wand/minimax-tts/sync_tts", payload)
        url = _extract(data, "audio", "audio_url")
        if url is None:
            url = _extract(data, "url")
        return {"audio_url": url, "mock": False, "raw": data}

    def transcribe(self, input_url: str) -> dict:
        """音频/视频 URL → 转写文本,返回 {text, mock, raw};text 优先取 output.text 全文。"""
        if self.mock:
            return {"text": "", "mock": True, "input_url": input_url}

        payload = {"model": self.settings.asr_model, "input_url": input_url}
        data = self._post("/v1/wand/asrproxy/sync_transcribe", payload)
        text = None
        if isinstance(data, dict) and isinstance(data.get("output"), dict):
            text = data["output"].get("text")
        if not text:
            text = _extract(data, "text", "transcript")
        return {"text": text or "", "mock": False, "raw": data}
