"""模型网关:OpenAI 兼容的 LLM 客户端,默认指向 deepseek-v4-pro。

无 API key 时自动进入 mock 模式,返回确定性占位回复,保证链路可跑通。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterator

import httpx

from config import get_settings
from gateway.schemas import parse_json


class LLMError(Exception):
    """LLM 调用失败。"""


# 共享 httpx 连接池(线程安全):多用户并发时复用与 LLM 网关的连接,避免每请求重开 TLS。
# max_connections 兜底并发上限,防止小服务器(2 核 4G)瞬时打开过多 socket。
_shared_client: httpx.Client | None = None
_shared_client_lock = threading.Lock()


def _get_shared_client() -> httpx.Client:
    global _shared_client
    if _shared_client is None:
        with _shared_client_lock:
            if _shared_client is None:
                _shared_client = httpx.Client(
                    timeout=get_settings().llm_timeout,
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _shared_client


class LLMClient:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._client = _get_shared_client()

    @property
    def mock(self) -> bool:
        if self.settings.environment == "production" and self.settings.mock_llm: raise RuntimeError("Production forbids mock gateway")
        if not self.settings.mock_llm and not self.settings.llm_api_key: raise RuntimeError("Gateway configuration missing")
        return self.settings.mock_llm

    def chat(self, messages: list[dict], temperature: float = 0.7,
             max_tokens: int | None = None, model: str | None = None,
             tools: list[dict] | None = None) -> dict:
        """返回 {"content": str, "usage": {prompt_tokens, completion_tokens}, "tool_calls": [...]}。

        model 为空时用冷路径模型(llm_model);热路径可传 settings.llm_fast_model。
        传入 tools 时启用 function calling,响应里的 tool_calls 原样透出;mock 下为空列表。
        """
        if self.mock:
            return {
                "content": self._mock_reply(messages),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                "tool_calls": [],
            }

        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        resp = self._post_with_retry(url, headers, payload)
        data = resp.json()
        message = (data["choices"][0].get("message") or {}) if data.get("choices") else {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        usage = data.get("usage") or {}
        return {"content": content, "usage": usage, "tool_calls": tool_calls}

    def chat_json(self, messages: list[dict], temperature: float = 0.2,
                  max_tokens: int | None = None, model: str | None = None) -> tuple[Any | None, dict]:
        """要求模型返回 JSON 并解析;失败时 parsed 为 None,附带原始文本。"""
        result = self.chat(messages, temperature=temperature, max_tokens=max_tokens, model=model)
        parsed, raw = parse_json(result["content"])
        return parsed, {"usage": result["usage"], "raw": raw}

    def _post_with_retry(self, url: str, headers: dict, payload: dict,
                         retries: int = 3, backoff: float = 2.0) -> httpx.Response:
        """带重试的 POST:抗瞬时断连/限流/5xx,避免一次抖动打断长链路(如评估)。"""
        last: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self._client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as e:
                last = e
            else:
                if resp.status_code == 200:
                    return resp
                last = LLMError(f"LLM 返回 {resp.status_code}: {resp.text[:300]}")
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
        raise LLMError(f"LLM 调用失败(重试 {retries} 次): {last}") from last

    def chat_stream(self, messages: list[dict], temperature: float = 0.7,
                    max_tokens: int | None = None, model: str | None = None) -> Iterator[str]:
        """流式返回 content 增量(SSE),供 TTS / 前端逐字渲染。

        每个 yield 是一段增量文本;reasoning 内容被跳过(不外吐),
        只有最终的 content 会流式返回。
        """
        if self.mock:
            text = self._mock_reply(messages)
            for i in range(0, len(text), 8):
                yield text[i:i + 8]
            return

        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            with self._client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = "".join(resp.iter_text())[:300]
                    raise LLMError(f"LLM 返回 {resp.status_code}: {body}")
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    # 跳过 reasoning 内容,只吐正文
                    piece = delta.get("content")
                    if piece:
                        yield piece
        except httpx.HTTPError as e:
            raise LLMError(f"LLM 网络错误: {e}") from e

    def _mock_reply(self, messages: list[dict]) -> str:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return "（mock）我在这里,愿意听你慢慢说。你可以告诉我现在最难受的是什么。"
