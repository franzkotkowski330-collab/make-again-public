"""鉴权:密码哈希(pbkdf2,标准库,零新依赖)+ 会话 token + 当前用户依赖。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from memory import store
from memory.db import get_session
from memory.models import utcnow

_ITERATIONS = 200_000
_SESSION_DAYS = 30


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (hash_hex, salt_hex)。salt 不传则随机生成。"""
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        digest, _ = hash_password(password, bytes.fromhex(salt_hex))
    except ValueError:
        return False
    return secrets.compare_digest(digest, hash_hex)


def new_token() -> str:
    return secrets.token_hex(32)


def new_expiry() -> datetime:
    return utcnow() + timedelta(days=_SESSION_DAYS)


def get_current_user(authorization: str | None = Header(None), db: Session = Depends(get_session)) -> str:
    """解析 `Authorization: Bearer <token>` → 当前 user_id;未登录/过期抛 401。

    本次仅 /auth/me 使用;后续把外圈数据接口接入鉴权时复用作依赖即可。
    """
    from api.security import authenticate
    return authenticate(db,authorization).id
