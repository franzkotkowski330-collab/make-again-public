"""内容安全:检测真实定位/联系方式/身份信息,阻止其进入树洞信或回信。

两层检测:
1. 正则硬匹配(零 LLM,不可绕过):手机号、座机、邮箱、身份证、社交账号、URL、地址。
2. LLM 语义检测:真实人名、具体工作单位/学校、具体住址(正则难覆盖的语义信息)。
两者命中即视为"含敏感信息",调用方据此拒绝(让用户去掉后再发),而不是静默改写。
"""
from __future__ import annotations

import re

from gateway.client import LLMClient

_MOBILE = re.compile(r"1[3-9]\d{9}")
_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_IDCARD = re.compile(r"\d{17}[\dXx]")
_SOCIAL = re.compile(r"(微信|vx|VX|qq|QQ|抖音|微博|钉钉|电话|手机)\s*[:：]?\s*[a-zA-Z0-9_-]{5,}")
_URL = re.compile(r"https?://\S+")
_ADDR = re.compile(r"(省|市|区|县|镇|乡|村)[^\n,。;]{0,8}(路|街|号|栋|单元|室|小区|大厦|广场|花园|苑)")
_ME_CONTACT = re.compile(r"(加我|联系我|找我|搜我)\s*[^\n,。;]{0,10}\d{5,}")

_REGEX_RULES: list[tuple[str, re.Pattern]] = [
    ("contact", _MOBILE), ("contact", _LANDLINE), ("contact", _EMAIL),
    ("contact", _IDCARD), ("contact", _SOCIAL), ("contact", _URL),
    ("location", _ADDR), ("contact", _ME_CONTACT),
]

_MODERATION_SYSTEM = "你是内容安全审核助手,只输出 JSON,不要多余文字。"


def scan_pii(text: str, client: LLMClient | None = None) -> dict:
    """返回 {"clean": bool, "flags": [{"type","text"}]}。clean=True 表示未检出敏感信息。"""
    flags: list[dict] = []

    for typ, pat in _REGEX_RULES:
        for m in pat.finditer(text or ""):
            flags.append({"type": typ, "text": m.group(0)})

    if client is not None and not client.mock:
        parsed, _ = client.chat_json(
            [
                {"role": "system", "content": _MODERATION_SYSTEM},
                {"role": "user", "content": (
                    "检查下面这段文字是否包含任何可能暴露真实身份的信息,包括:\n"
                    "- 真实人名(全名或可定位到具体人的称呼)\n"
                    "- 具体地址(省市区县 + 路/街/小区/楼栋/门牌)\n"
                    "- 具体工作单位或学校名称\n"
                    "- 联系方式(电话/微信/QQ/邮箱等账号)\n"
                    "只输出 JSON:{\"pii\":[{\"type\":\"name|location|workplace|contact\",\"text\":\"片段\"}]}\n"
                    "没有则 pii 为空数组。\n\n文字:\n" + text
                )},
            ],
            temperature=0.1, model=client.settings.llm_fast_model,
        )
        if isinstance(parsed, dict):
            for it in parsed.get("pii") or []:
                if isinstance(it, dict) and it.get("text"):
                    flags.append({"type": it.get("type", "other"), "text": str(it["text"])})

    seen: set[str] = set()
    uniq: list[dict] = []
    for f in flags:
        if f["text"] not in seen:
            seen.add(f["text"])
            uniq.append(f)

    return {"clean": len(uniq) == 0, "flags": uniq}
