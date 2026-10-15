"""对象存储:腾讯云 COS(生产)+ 本地目录(无 COS 凭据时兜底,仅跑通链路)。

语音走「私有桶 + 预签名 URL」:上传后生成为期几分钟的 GET 预签名 URL,
再把它当 input_url 喂给 ASR,音频不长期公网暴露。
"""
from __future__ import annotations

from pathlib import Path

import httpx

from config import get_settings


class StorageError(Exception):
    """对象存储调用失败。"""


class ObjectStore:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._cos = None
        self._bucket = None
        if not self.settings.mock_storage and not all((self.settings.cos_secret_id,self.settings.cos_secret_key,self.settings.cos_bucket)):
            raise StorageError("Private object storage configuration missing")
        if self.settings.environment=="production" and self.settings.mock_storage: raise StorageError("Production forbids local mock storage")
        if not self.settings.mock_storage and self.settings.cos_secret_id and self.settings.cos_secret_key and self.settings.cos_bucket:
            from qcloud_cos import CosConfig, CosS3Client
            cfg = CosConfig(Region=self.settings.cos_region,
                            SecretId=self.settings.cos_secret_id,
                            SecretKey=self.settings.cos_secret_key,
                            Timeout=self.settings.cos_timeout,
                            EnableOldDomain=self.settings.cos_use_old_domain,
                            EnableInternalDomain=False)
            # retry=1 限制重试:COS 不可达时 2 次 × 超时即失败,避免 3 次重试把上传挂满 2 分钟
            self._cos = CosS3Client(cfg, retry=1)
            self._bucket = self.settings.cos_bucket

    @property
    def backend(self) -> str:
        return "cos" if self._cos else "local"

    def _full_key(self, key: str, prefix: str | None = None) -> str:
        prefix = (prefix if prefix is not None else self.settings.cos_upload_prefix).strip("/")
        return f"{prefix}/{key.lstrip('/')}" if prefix else key.lstrip("/")

    def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream",
               prefix: str | None = None) -> str:
        """上传字节,返回完整对象 key(含前缀)。prefix 缺省时用 cos_upload_prefix。"""
        full_key = self._full_key(key, prefix)
        if self._cos:
            from qcloud_cos import CosClientError, CosServiceError
            try:
                self._cos.put_object(Bucket=self._bucket, Key=full_key, Body=data, ContentType=content_type)
            except (CosServiceError, CosClientError) as e:
                msg = e.get_error_msg() if hasattr(e, "get_error_msg") else str(e)
                raise StorageError(f"COS 上传失败: {msg}") from e
        else:
            dest = Path(self.settings.storage_local_dir) / full_key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        return full_key

    def presigned_url(self, full_key: str, expires: int | None = None) -> str:
        """生成 GET 预签名 URL,供 ASR 拉取;本地兜底返回 local:// 相对路径(无公网)。"""
        expires = expires or self.settings.cos_presign_expires
        if self._cos:
            return self._cos.get_presigned_url(Method="GET", Bucket=self._bucket, Key=full_key, Expired=expires)
        from api.media import local_url
        return local_url(full_key,expires)

    def delete(self, full_key: str) -> None:
        """删除桶内对象(用于不保留原图 / 中间图的场景)。失败抛 StorageError。"""
        if self._cos:
            from qcloud_cos import CosClientError, CosServiceError
            try:
                self._cos.delete_object(Bucket=self._bucket, Key=full_key)
            except (CosServiceError, CosClientError) as e:
                msg = e.get_error_msg() if hasattr(e, "get_error_msg") else str(e)
                raise StorageError(f"COS 删除失败: {msg}") from e
        else:
            dest = Path(self.settings.storage_local_dir) / full_key
            try:
                dest.unlink(missing_ok=True)
            except OSError as e:
                raise StorageError(f"本地删除失败: {e}")

    def ai_matte(self, full_key: str) -> bytes:
        """数据万象通用抠图(主体分割):对桶内对象做 AIPicMatting,返回透明底 PNG 字节。

        需桶已开通并绑定数据万象;本地兜底 / 未开通 / 失败时抛 StorageError,
        由调用方回退为「存原图」。
        """
        if not self._cos:
            raise StorageError("本地存储兜底不支持数据万象抠图")
        try:
            url = self._cos.get_presigned_url(
                Bucket=self._bucket, Key=full_key, Method="GET",
                Params={"ci-process": "AIPicMatting"}, Expired=60,
            )
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"生成抠图签名失败: {e}") from e
        try:
            resp = httpx.get(url, timeout=30.0)
        except httpx.HTTPError as e:
            raise StorageError(f"数据万象抠图网络错误: {e}") from e
        if resp.status_code != 200:
            raise StorageError(f"数据万象抠图返回 {resp.status_code}: {resp.text[:200]}")
        return resp.content

    def ai_crop(self, full_key: str, box: list[float]) -> bytes:
        """数据万象裁切:按像素框 [x1,y1,x2,y2] 裁桶内对象,返回裁切后图片字节(imageMogr2/cut)。"""
        if not self._cos:
            raise StorageError("本地存储兜底不支持数据万象裁切")
        x1, y1, x2, y2 = [int(round(v)) for v in box]
        w, h = max(1, x2 - x1), max(1, y2 - y1)
        dx, dy = max(0, x1), max(0, y1)
        try:
            url = self._cos.get_presigned_url(
                Bucket=self._bucket, Key=full_key, Method="GET",
                Params={"imageMogr2/cut": f"{w}x{h}x{dx}x{dy}"}, Expired=60,
            )
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"生成裁切签名失败: {e}") from e
        try:
            resp = httpx.get(url, timeout=30.0)
        except httpx.HTTPError as e:
            raise StorageError(f"数据万象裁切网络错误: {e}") from e
        if resp.status_code != 200:
            raise StorageError(f"数据万象裁切返回 {resp.status_code}: {resp.text[:200]}")
        return resp.content
