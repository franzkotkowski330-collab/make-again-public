"""真实上传主链路测试:定位(grounding)+ 裁切 + 抠图 + 落库(走真实腾讯云视觉/COS)。

与 scripts/vision_real.py 的区别:这里直接调 agent.item.handle_upload(产品实际走的代码),
用一张「白底 + 红色方块」的合成图,观察整条链路能否跑通并拿到可访问的结果 URL。

运行(需网络 + 视觉/COS/CI 授权):
    python scripts/item_upload_real.py
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent import item as item_mod
from config import Settings
from gateway.client import LLMClient
from gateway.storage import ObjectStore
from gateway.vision import VisionClient
from memory import store
from memory.db import Base

# 临时库(与 sim 一致)
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
engine = create_engine(f"sqlite:///{_tmp.name.replace(chr(92), '/')}",
                       connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
import memory.async_write as _aw
_aw.SessionLocal = S
_aw.LLMClient = lambda: LLMClient(settings=Settings(mock_llm=True))


def make_object_png(w=200, h=200, bg=(255, 255, 255), fg=(220, 40, 40),
                    box=(60, 60, 140, 140)) -> bytes:
    """纯 Python 生成「白底 + 红方块」的合法 PNG(无需 PIL)。"""
    def chunk(t, d):
        return (struct.pack(">I", len(d)) + t + d
                + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))

    rows = b""
    for y in range(h):
        row = b"\x00"
        for x in range(w):
            row += bytes(fg) if box[0] <= x < box[2] and box[1] <= y < box[3] else bytes(bg)
        rows += row
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class FakeClient:
    settings = Settings(mock_llm=True)
    mock = True


def main() -> None:
    db = S()
    store.get_or_create_user(db, "REAL-UPLOAD")
    db.commit()

    png = make_object_png()
    print(f"测试图: 白底 + 红方块,{len(png)} 字节")
    vision = VisionClient()
    obj_store = ObjectStore()
    print(f"vision.available={vision.available}  obj_store.backend={obj_store.backend}\n")

    print("=" * 64)
    print("【上传主链路】handle_upload(定位 → 裁切 → 抠图 → 落库)")
    print("=" * 64)
    up = item_mod.handle_upload(
        db, "REAL-UPLOAD", png, item_name="", intent="keep",
        description="一个红色的方块", client=FakeClient(),
        vision=vision, obj_store=obj_store)
    db.commit()

    print(f"  item_name  = {up['item_name']}")
    print(f"  label      = {up['label']}")
    print(f"  intent     = {up['intent']}")
    print(f"  backend    = {up['backend']}")
    print(f"  match      = {up.get('match')}  ok={up.get('ok')}")
    print(f"  cutout_url   = {up['cutout_url'][:110]}...")
    print("  (原图已按需求删除,仅保留抠图)")

    # 拉取结果 URL 验证可访问且是合法 PNG
    print("\n" + "=" * 64)
    print("【结果校验】拉取 cutout URL")
    print("=" * 64)
    for name, url in (("cutout", up["cutout_url"]),):
        try:
            r = httpx.get(url, timeout=30.0)
            is_png = r.content[:8] == b"\x89PNG\r\n\x1a\n"
            print(f"  [{name}] HTTP {r.status_code}  {len(r.content)} 字节  PNG={'是' if is_png else '否'}")
        except Exception as e:  # noqa: BLE001
            print(f"  [{name}] 拉取失败: {e}")


if __name__ == "__main__":
    main()
