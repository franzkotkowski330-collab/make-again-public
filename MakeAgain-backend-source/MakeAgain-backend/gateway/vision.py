"""视觉客户端:腾讯云图像识别(物品识别)。

设计要点:
- recognize(image) -> str | None:调用腾讯云 TIAA「通用图像标签 DetectLabelPro」识别图中主体,
  返回置信度最高的标签(用于兜底 item_name / 存 label)。
- 抠图(主体分割)不在本类:腾讯云通用抠图走「数据万象 AIPicMatting」,须对桶内对象操作,
  故放在 gateway.storage.ObjectStore.ai_matte。
- 无凭据 / mock_vision / 调用失败时兜底:recognize 返回 None,让整条链路离线也能跑通。
- 鉴权用腾讯云 TC3-HMAC-SHA256(服务 tiia,版本 2019-05-29);凭据复用 vision_secret_id/key,
  空则回退 cos_secret_id/key(同一套 CAM 凭据)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx

from config import get_settings
from gateway.schemas import parse_json


class VisionError(Exception):
    """视觉服务调用失败。"""


def _tc3_authorization(secret_id: str, secret_key: str, service: str, host: str,
                       action: str, version: str, region: str, payload: str,
                       timestamp: int) -> str:
    """生成腾讯云签名方法 v3(TC3-HMAC-SHA256)的 Authorization 请求头。"""
    algorithm = "TC3-HMAC-SHA256"
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = ("POST\n/\n\n" + canonical_headers + "\n"
                         + signed_headers + "\n" + hashed_payload)

    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, service)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return (f"{algorithm} Credential={secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}")


class VisionClient:
    _HOST = "tiia.tencentcloudapi.com"
    _SERVICE = "tiia"
    _VERSION = "2019-05-29"

    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    @property
    def available(self) -> bool:
        """是否有可用的真实视觉服务(未 mock,且有独立凭据或回退 cos 凭据)。"""
        if self.settings.mock_vision:
            return False
        sid = self.settings.vision_secret_id or self.settings.cos_secret_id
        skey = self.settings.vision_secret_key or self.settings.cos_secret_key
        return bool(sid and skey)

    def recognize(self, image: bytes) -> str | None:
        """识别图中主体,返回置信度最高的标签;不可用/失败时返回 None。"""
        if not self.available:
            return None
        try:
            return self._recognize_tencent(image)
        except Exception:  # noqa: BLE001 视觉失败不阻塞主链路
            return None

    def _recognize_tencent(self, image: bytes) -> str | None:
        sid = self.settings.vision_secret_id or self.settings.cos_secret_id
        skey = self.settings.vision_secret_key or self.settings.cos_secret_key
        region = self.settings.vision_region
        action = "DetectLabelPro"

        payload = json.dumps(
            {"ImageBase64": base64.b64encode(image).decode("ascii")},
            separators=(",", ":"),
        )
        timestamp = int(time.time())
        authorization = _tc3_authorization(
            sid, skey, self._SERVICE, self._HOST, action, self._VERSION, region,
            payload, timestamp,
        )
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": self._HOST,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": self._VERSION,
            "X-TC-Region": region,
        }
        try:
            resp = httpx.post(f"https://{self._HOST}", headers=headers,
                              content=payload, timeout=30.0)
        except httpx.HTTPError as e:
            raise VisionError(f"腾讯云图像识别网络错误: {e}") from e
        if resp.status_code != 200:
            raise VisionError(f"腾讯云图像识别返回 {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        labels = (data.get("Response") or {}).get("Labels") or []
        if not labels:
            return None
        best = max(labels, key=lambda x: x.get("Confidence") or 0)
        return best.get("Name")

    def ground(self, image_url: str, description: str) -> dict:
        """多模态定位:找出图中与描述最相符的物品,返回 {"label","bbox"}。

        - label: str | None,模型给该物品起的名称。
        - bbox: [x1,y1,x2,y2] | None,归一化坐标 [0,1](相对整张图)。
        走 TokenHub 多模态模型(hy-vision);不可用/失败时两个字段均返回 None,链路降级为整图。
        """
        if not (self.settings.maas_api_key and not self.settings.mock_vision):
            return {"label": None, "bbox": None}
        try:
            return self._ground_maas(image_url, description)
        except Exception:  # noqa: BLE001 定位失败不阻塞主链路
            return {"label": None, "bbox": None}

    def _ground_maas(self, image_url: str, description: str) -> dict:
        query = (description or "").strip() or "主体物品"
        prompt = (
            "图中有一件物品，用户描述是「" + query + "」。请找出图中与这个描述最相符的物品，"
            "返回它的名称和所在位置。位置用归一化坐标表示（相对整张图，左上角为原点，范围 0 到 1000）。"
            "只输出一个 JSON，不要其它文字，格式：{\"label\":\"物品名\",\"bbox\":[x1,y1,x2,y2]}。"
            "如果图中找不到相符的物品，返回 {\"label\":\"\",\"bbox\":[]}。"
        )
        url = self.settings.maas_base_url.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": self.settings.vision_llm_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.settings.maas_api_key}",
                   "Content-Type": "application/json"}
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=60.0)
        except httpx.HTTPError as e:
            raise VisionError(f"定位网络错误: {e}") from e
        if resp.status_code != 200:
            raise VisionError(f"定位返回 {resp.status_code}: {resp.text[:200]}")
        content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
        parsed, _ = parse_json(content)
        if not isinstance(parsed, dict):
            return {"label": None, "bbox": None}

        label = (parsed.get("label") or "").strip() or None
        bbox = None
        raw = parsed.get("bbox")
        if isinstance(raw, list) and len(raw) == 4:
            try:
                vals = [float(x) for x in raw]
            except (TypeError, ValueError):
                vals = []
            if len(vals) == 4 and max(vals) > 1.5:      # 0-1000 空间(Qwen 约定)
                vals = [v / 1000.0 for v in vals]
            if len(vals) == 4:
                x1, y1, x2, y2 = [min(max(v, 0.0), 1.0) for v in vals]
                if x2 > x1 and y2 > y1:
                    bbox = [x1, y1, x2, y2]
        return {"label": label, "bbox": bbox}

    def verify(self, image_url: str, description: str) -> dict:
        """多模态校验:判断抠图是否确为用户描述的那件物品,返回 {"match","reason"}。

        不可用 / 调用失败时返回 match=True(放行),离线与降级链路都能跑通。
        """
        if not (self.settings.maas_api_key and not self.settings.mock_vision):
            return {"match": True, "reason": ""}
        try:
            return self._verify_maas(image_url, description)
        except Exception:  # noqa: BLE001 校验失败不阻塞主链路
            return {"match": True, "reason": ""}

    def _verify_maas(self, image_url: str, description: str) -> dict:
        query = (description or "").strip() or "主体物品"
        prompt = (
            "这是一张从用户上传图片里抠出来的物品图。用户描述是「" + query + "」。"
            "请判断抠出的这件物品是否符合用户描述。只输出 JSON,不要其它文字,格式:"
            "{\"match\": true 或 false, \"reason\": \"一句简短中文说明(不符合时说明为什么)\"}。"
        )
        url = self.settings.maas_base_url.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": self.settings.vision_llm_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.settings.maas_api_key}",
                   "Content-Type": "application/json"}
        resp = httpx.post(url, headers=headers, json=payload, timeout=60.0)
        if resp.status_code != 200:
            raise VisionError(f"校验返回 {resp.status_code}: {resp.text[:200]}")
        content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
        parsed, _ = parse_json(content)
        if not isinstance(parsed, dict):
            return {"match": True, "reason": ""}
        raw = parsed.get("match", True)
        if isinstance(raw, str):
            match = raw.strip().lower() not in ("false", "0", "no", "否", "不是", "不符合", "假")
        else:
            match = bool(raw)
        return {"match": match, "reason": str(parsed.get("reason") or "")}
