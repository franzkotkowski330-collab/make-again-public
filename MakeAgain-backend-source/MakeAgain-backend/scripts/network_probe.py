"""外部服务连通性探测:DeepSeek / 腾讯 MaaS(语音) / 腾讯 COS / 腾讯云视觉。

回答「本环境到外部服务是否可达」,分两层:
- 网络层:DNS 解析 + TCP 443 握手(直连,不走代理)
- 应用层:真实最小化 API 调用(走 httpx,自动尊重系统代理)
    · DeepSeek: chat/completions, max_tokens=1
    · 腾讯 MaaS: minimax-tts 极小文本「你好」
    · 腾讯 COS: list_objects(MaxKeys=1)
    · 腾讯云视觉:仅 host 可达性(TIAA 图像识别/主体分割,后端尚未接入)

凭据全部从 .env 读(get_settings),本脚本不硬编码、不回显任何密钥值。
运行:
    python scripts/network_probe.py            # 网络层 + 应用层
    python scripts/network_probe.py --no-api   # 仅网络层(DNS/TCP)
    python scripts/network_probe.py --timeout 8
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import httpx

from config import get_settings

# host -> 服务名(仅网络层探测用;应用层走各自 client)
HOSTS = [
    ("api.deepseek.com", "DeepSeek LLM"),
    ("tokenhub.tencentmaas.com", "腾讯 MaaS(语音)"),
    ("make-again-1388447375.cos.ap-guangzhou.myqcloud.com", "腾讯 COS(桶)"),
    ("tiia.tencentcloudapi.com", "腾讯云视觉(TIAA 识别/分割)"),
]

results: list[tuple[str, bool]] = []  # (描述, 是否通过)


def _mark(name: str, ok: bool) -> None:
    results.append((name, ok))


def _resolve(host: str, timeout: float) -> tuple[bool, str, float]:
    t0 = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = sorted({i[4][0] for i in infos})
        return True, f"{len(ips)} 个 IP,首个 {ips[0]}", (time.perf_counter() - t0) * 1000
    except Exception as e:  # noqa: BLE001
        return False, str(e), (time.perf_counter() - t0) * 1000


def _tcp443(host: str, timeout: float) -> tuple[bool, str, float]:
    t0 = time.perf_counter()
    try:
        s = socket.create_connection((host, 443), timeout=timeout)
        s.close()
        return True, "443 握手成功", (time.perf_counter() - t0) * 1000
    except Exception as e:  # noqa: BLE001
        return False, str(e), (time.perf_counter() - t0) * 1000


def probe_network(timeout: float) -> None:
    print("=" * 64)
    print("【网络层】DNS 解析 + TCP 443 握手(直连)")
    print("=" * 64)
    for host, name in HOSTS:
        ok_dns, dns_msg, dns_ms = _resolve(host, timeout)
        if ok_dns:
            ok_tcp, tcp_msg, tcp_ms = _tcp443(host, timeout)
        else:
            ok_tcp, tcp_msg, tcp_ms = False, "(跳过)", 0.0
        ok = ok_dns and ok_tcp
        _mark(f"网络 {name}", ok)
        state = "OK " if ok else "FAIL"
        print(f"  [{state}] {name} ({host})")
        print(f"        DNS {dns_ms:6.0f}ms  {dns_msg}")
        print(f"        TCP {tcp_ms:6.0f}ms  {tcp_msg}")


def probe_deepseek(s, timeout: float) -> None:
    print("\n" + "=" * 64)
    print("【应用层 · DeepSeek】chat/completions,max_tokens=1")
    print("=" * 64)
    if not s.llm_api_key:
        print("  [SKIP] 未配置 LLM_API_KEY")
        return
    url = s.llm_base_url.rstrip("/") + "/chat/completions"
    payload = {"model": s.llm_fast_model, "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 1, "temperature": 0}
    headers = {"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, trust_env=True) as c:
            r = c.post(url, headers=headers, json=payload)
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code == 200:
            d = r.json()
            content = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            model = d.get("model") or s.llm_fast_model
            _mark("DeepSeek 应用层", True)
            print(f"  [OK ] HTTP 200,{ms:.0f}ms,model={model},content={content[:40]!r}")
        else:
            _mark("DeepSeek 应用层", False)
            print(f"  [FAIL] HTTP {r.status_code},{ms:.0f}ms: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        _mark("DeepSeek 应用层", False)
        print(f"  [FAIL] 异常: {e}")


def probe_maas(s, timeout: float) -> None:
    print("\n" + "=" * 64)
    print("【应用层 · 腾讯 MaaS】minimax-tts,极小文本「你好」")
    print("=" * 64)
    if not s.maas_api_key:
        print("  [SKIP] 未配置 MAAS_API_KEY")
        return
    url = s.maas_base_url.rstrip("/") + "/v1/wand/minimax-tts/sync_tts"
    payload = {
        "model": s.tts_model, "text": "你好",
        "voice_setting": {"voice_id": s.tts_voice_id},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        "output_format": "url",
    }
    headers = {"Authorization": f"Bearer {s.maas_api_key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, trust_env=True) as c:
            r = c.post(url, headers=headers, json=payload)
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code == 200:
            _mark("MaaS 应用层", True)
            has_audio = "audio" in r.text or "url" in r.text
            print(f"  [OK ] HTTP 200,{ms:.0f}ms,返回含音频地址={has_audio}")
        else:
            _mark("MaaS 应用层", False)
            print(f"  [FAIL] HTTP {r.status_code},{ms:.0f}ms: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        _mark("MaaS 应用层", False)
        print(f"  [FAIL] 异常: {e}")


def probe_cos(s, timeout: float) -> None:
    print("\n" + "=" * 64)
    print("【应用层 · 腾讯 COS】list_objects(MaxKeys=1)")
    print("=" * 64)
    if not (s.cos_secret_id and s.cos_secret_key and s.cos_bucket):
        print("  [SKIP] 缺 COS 凭据/bucket")
        return
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        _mark("COS 应用层", False)
        print("  [FAIL] 未安装 qcloud_cos SDK(生产环境需 pip install cos-python-sdk-v5)")
        return
    try:
        cfg = CosConfig(Region=s.cos_region, SecretId=s.cos_secret_id, SecretKey=s.cos_secret_key)
        cos = CosS3Client(cfg)
        t0 = time.perf_counter()
        resp = cos.list_objects(Bucket=s.cos_bucket, MaxKeys=1)
        ms = (time.perf_counter() - t0) * 1000
        keys = [o.get("Key") for o in (resp.get("Contents") or [])]
        _mark("COS 应用层", True)
        print(f"  [OK ] {ms:.0f}ms,bucket={s.cos_bucket},返回 {len(keys)} 个对象")
    except Exception as e:  # noqa: BLE001
        _mark("COS 应用层", False)
        print(f"  [FAIL] {e}")


def summary() -> int:
    print("\n" + "=" * 64)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    print("-" * 64)
    print(f"  通过 {passed}/{total}")
    print("=" * 64)
    return 0 if passed == total else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-api", action="store_true", help="仅网络层,不发起真实 API 调用")
    ap.add_argument("--timeout", type=float, default=10.0, help="单次请求超时秒数")
    args = ap.parse_args()

    s = get_settings()
    print("外部服务连通性探测(密钥仅显示是否已配置,不回显明文)")
    print(f"  LLM_API_KEY 已配置 = {bool(s.llm_api_key)}")
    print(f"  MAAS_API_KEY 已配置 = {bool(s.maas_api_key)}")
    print(f"  COS 凭据/bucket 已配置 = {bool(s.cos_secret_id and s.cos_secret_key and s.cos_bucket)}")

    probe_network(args.timeout)
    if not args.no_api:
        probe_deepseek(s, args.timeout)
        probe_maas(s, args.timeout)
        probe_cos(s, args.timeout)

    sys.exit(summary())


if __name__ == "__main__":
    main()
