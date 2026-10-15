"""真实视觉测试:识别(DetectLabelPro)+ 抠图(数据万象 AIPicMatting)。

无 PIL 依赖:用纯 Python 生成一张合法 PNG 作测试图。
- 识别:调 TIAA DetectLabelPro,打印完整响应(合成图可能无标签,但能验证签名/请求格式/解析)。
- 抠图:上传测试图到 COS 后调 ai_matte;若桶未开通数据万象会得到明确报错(链路会回退存原图)。

运行(需网络 + COS 凭据):
    python scripts/vision_real.py
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import time
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import httpx

from config import get_settings
from gateway.storage import ObjectStore, StorageError
from gateway.vision import VisionClient, _tc3_authorization


def make_png(width: int = 120, height: int = 120, rgb=(200, 80, 60)) -> bytes:
    """纯 Python 生成一张 8-bit RGB 合法 PNG(无需 PIL)。"""
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    row = b"\x00" + bytes(rgb) * width          # 每行:filter 字节 0 + RGB
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(row * height))
            + chunk(b"IEND", b""))


def main() -> None:
    s = get_settings()
    png = make_png()
    print("测试图: 合法 PNG,", len(png), "字节")
    print(f"vision 凭据已配置 = {bool(s.vision_secret_id and s.vision_secret_key) or bool(s.cos_secret_id and s.cos_secret_key)}")
    print(f"cos bucket = {s.cos_bucket or '(未配置)'}\n")

    # ---- 识别:DetectLabelPro(打印完整响应便于诊断)----
    print("=" * 64)
    print("【识别】TIAA DetectLabelPro")
    print("=" * 64)
    sid = s.vision_secret_id or s.cos_secret_id
    skey = s.vision_secret_key or s.cos_secret_key
    action = "DetectLabelPro"
    payload = json.dumps({"ImageBase64": base64.b64encode(png).decode("ascii")},
                         separators=(",", ":"))
    ts = int(time.time())
    auth = _tc3_authorization(sid, skey, "tiia", "tiia.tencentcloudapi.com",
                              action, "2019-05-29", s.vision_region, payload, ts)
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json; charset=utf-8",
        "Host": "tiia.tencentcloudapi.com",
        "X-TC-Action": action,
        "X-TC-Timestamp": str(ts),
        "X-TC-Version": "2019-05-29",
        "X-TC-Region": s.vision_region,
    }
    try:
        r = httpx.post("https://tiia.tencentcloudapi.com", headers=headers,
                       content=payload, timeout=30.0)
        print(f"HTTP {r.status_code}")
        print("响应:", r.text[:600])
    except Exception as e:  # noqa: BLE001
        print("识别请求异常:", e)

    vc = VisionClient(s)
    print(f"\nVisionClient.recognize(测试图) -> {vc.recognize(png)!r} (合成图无标签则 None)")

    # ---- 抠图:数据万象 AIPicMatting ----
    print("\n" + "=" * 64)
    print("【抠图】数据万象 AIPicMatting(需桶已开通数据万象)")
    print("=" * 64)
    store = ObjectStore(s)
    print(f"ObjectStore.backend = {store.backend}")
    if store.backend != "cos":
        print("  [SKIP] COS 未配置,跳过抠图")
    else:
        key = store.upload("vision_test.png", png, content_type="image/png", prefix="test")
        print(f"  已上传测试图: {key}")
        try:
            cutout = store.ai_matte(key)
            print(f"  [OK ] 抠图成功,{len(cutout)} 字节,前 4 字节 {cutout[:4]!r}")
        except StorageError as e:
            print(f"  [FAIL] 抠图失败(桶可能未开通/绑定数据万象,链路将回退存原图): {e}")
        # 清理测试对象
        try:
            store._cos.delete_object(Bucket=store._bucket, Key=key)
            print("  已清理测试对象")
        except Exception:  # noqa: BLE001
            pass

    # ---- 定位 + 裁切:VLM grounding(hy-vision)+ 数据万象 imageMogr2/cut ----
    print("\n" + "=" * 64)
    print("【定位】VLM grounding(hy-vision-2.0-instruct,TokenHub)")
    print("=" * 64)
    if not s.maas_api_key:
        print("  [SKIP] maas_api_key 未配置,跳过定位")
    elif store.backend != "cos":
        print("  [SKIP] COS 未配置(grounding 需要公网图片 URL)")
    else:
        gkey = store.upload("vision_ground.png", png, content_type="image/png", prefix="test")
        gurl = store.presigned_url(gkey)
        print(f"  已上传: {gkey}")
        g = vc.ground(gurl, "手链")
        print(f"  ground -> label={g.get('label')!r}  bbox={g.get('bbox')!r}")

        # 裁切:有 bbox 则按其转像素框;否则用固定 60x60x0x0 验证端点可用
        print("\n" + "=" * 64)
        print("【裁切】数据万象 imageMogr2/cut")
        print("=" * 64)
        box = [0, 0, 120, 120]
        if g.get("bbox"):
            x1, y1, x2, y2 = g["bbox"]
            box = [x1 * 120, y1 * 120, x2 * 120, y2 * 120]
        try:
            cropped = store.ai_crop(gkey, box)
            ok = cropped[:8] == b"\x89PNG\r\n\x1a\n"
            print(f"  [{'OK ' if ok else 'WARN'}] 裁切 {box} -> {len(cropped)} 字节,PNG 魔数 {'匹配' if ok else '不匹配'}")
        except StorageError as e:
            print(f"  [FAIL] 裁切失败(桶可能未开通数据万象): {e}")
        try:
            store._cos.delete_object(Bucket=store._bucket, Key=gkey)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
