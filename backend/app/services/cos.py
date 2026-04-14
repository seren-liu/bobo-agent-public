"""腾讯云 COS 文件服务模块。

本模块负责图片上传请求校验、对象 key 生成，以及 COS 预签名 URL 的构建。
主要服务于移动端拍照/截图上传链路，并兼顾私有桶下的图片展示与外部读取。

核心职责:
- 校验上传图片的类型、大小和分辨率
- 为上传对象生成稳定且可追踪的存储 key
- 生成 PUT 上传预签名 URL
- 生成 GET 读取/展示预签名 URL
"""

from __future__ import annotations

import mimetypes
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# 不同上传来源的限制配置
# photo: 拍照上传
# screenshot: 截图上传
# manual: 手动补录或其他轻量图片来源
UPLOAD_LIMITS = {
    "photo": {"max_bytes": 2 * 1024 * 1024, "max_pixels": 12_000_000},
    "screenshot": {"max_bytes": 3 * 1024 * 1024, "max_pixels": 12_000_000},
    "manual": {"max_bytes": int(1.5 * 1024 * 1024), "max_pixels": 12_000_000},
}

# 允许上传的图片 MIME 类型集合
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


class COSService:
    """COS 服务封装。

    统一处理 COS 访问所需的环境配置，以及上传/读取相关能力。
    """

    def __init__(self) -> None:
        """从环境变量加载 COS 配置。"""
        self.secret_id = os.getenv("COS_SECRET_ID", "")
        self.secret_key = os.getenv("COS_SECRET_KEY", "")
        self.region = os.getenv("COS_REGION", "")
        self.bucket = os.getenv("COS_BUCKET", "")
        self.scheme = os.getenv("COS_SCHEME", "https")
        self.read_url_expired = int(os.getenv("COS_READ_URL_EXPIRED_SECONDS", "3600"))

    def _build_ext(self, filename: str, content_type: str) -> str:
        """根据文件名或 MIME 类型推断文件扩展名。

        参数:
            filename: 原始文件名。
            content_type: 文件 MIME 类型。

        返回:
            不带点号的扩展名，无法推断时返回 "bin"。
        """
        # 优先使用原始文件名中的后缀，保留客户端明确提供的格式信息
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext:
            return ext
        # 文件名没有后缀时，回退到 MIME 类型推断
        guessed = mimetypes.guess_extension(content_type or "")
        if guessed:
            return guessed.lstrip(".")
        # 最终兜底，避免生成无后缀 key
        return "bin"

    def _build_key(self, user_id: str, filename: str, content_type: str) -> str:
        """构建上传对象在 COS 中的存储 key。

        key 设计目标:
        - 按用户和月份分层，便于管理和排查
        - 使用时间戳和随机串避免冲突
        - 清理扩展名中的非法字符，降低对象名噪音

        参数:
            user_id: 用户标识符。
            filename: 原始文件名。
            content_type: 文件 MIME 类型。

        返回:
            COS 对象 key。
        """
        now = datetime.now(UTC)
        # 使用年月目录做分桶，便于按时间范围检索和运营排查
        year_month = now.strftime("%Y-%m")
        ext = self._build_ext(filename, content_type)
        # 毫秒时间戳 + 短随机串，兼顾可读性和碰撞概率
        timestamp_ms = int(now.timestamp() * 1000)
        short_random = uuid4().hex[:6]
        # 扩展名只保留小写字母和数字，避免特殊字符进入对象路径
        safe_ext = re.sub(r"[^a-z0-9]", "", ext.lower()) or "bin"
        return f"photos/{user_id}/{year_month}/bobo-{timestamp_ms}-{short_random}.{safe_ext}"

    def _create_client(self):
        """创建 qcloud_cos 客户端实例。

        返回:
            配置完成的 CosS3Client。

        异常:
            RuntimeError: 当前环境未安装 qcloud_cos 依赖时抛出。
        """
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except Exception as exc:  # pragma: no cover - tested via monkeypatch
            raise RuntimeError("qcloud_cos is required for COS upload URL generation") from exc

        # 每次按当前环境配置创建客户端，避免模块导入时就强绑定外部依赖
        config = CosConfig(
            Region=self.region,
            SecretId=self.secret_id,
            SecretKey=self.secret_key,
            Scheme=self.scheme,
        )
        return CosS3Client(config)

    def _build_file_url(self, key: str) -> str:
        """根据对象 key 构建未签名的文件访问 URL。"""
        return f"{self.scheme}://{self.bucket}.cos.{self.region}.myqcloud.com/{key}"

    def _build_bucket_prefix(self) -> str:
        """构建当前 bucket 的 URL 前缀，用于识别本桶内对象。"""
        return f"{self.scheme}://{self.bucket}.cos.{self.region}.myqcloud.com/"

    def _is_signed_url(self, file_url: str) -> bool:
        """判断给定 URL 是否已经带有 COS 签名参数。"""
        query_keys = {key.lower() for key, _ in parse_qsl(urlsplit(file_url).query, keep_blank_values=True)}
        return "q-sign-algorithm" in query_keys

    def _extract_bucket_key(self, file_url: str) -> str | None:
        """Extract the COS object key when the URL belongs to the configured bucket."""
        if not file_url:
            return None
        parts = urlsplit(file_url)
        expected = urlsplit(self._build_bucket_prefix())
        if parts.scheme != expected.scheme or parts.netloc != expected.netloc:
            return None
        return parts.path.lstrip("/") or None

    def validate_user_file_url(self, file_url: str, user_id: str) -> str:
        """Ensure the file URL belongs to this user's photo namespace in the configured COS bucket."""
        key = self._extract_bucket_key(file_url)
        if not key:
            raise ValueError("invalid_image_url")
        expected_prefix = f"photos/{user_id}/"
        if not key.startswith(expected_prefix):
            raise ValueError("invalid_image_url")
        return key

    def validate_upload_request(
        self,
        *,
        content_type: str,
        file_size: int,
        width: int,
        height: int,
        source_type: str,
    ) -> None:
        """校验上传请求是否满足图片类型和体积限制。

        参数:
            content_type: 文件 MIME 类型。
            file_size: 文件大小，单位字节。
            width: 图片宽度，单位像素。
            height: 图片高度，单位像素。
            source_type: 上传来源类型，如 photo、screenshot。

        异常:
            ValueError: 校验失败时抛出带错误码语义的异常。
        """
        normalized_content_type = (content_type or "").lower()
        # MIME 类型必须在白名单内，避免上传非图片或未支持格式
        if normalized_content_type not in ALLOWED_IMAGE_TYPES:
            raise ValueError("unsupported_image_type")
        # 上传来源决定限制策略，未知来源直接拒绝
        if source_type not in UPLOAD_LIMITS:
            raise ValueError("unsupported_source_type")
        limits = UPLOAD_LIMITS[source_type]
        # 限制原始文件体积，减少带宽和后续处理成本
        if file_size > limits["max_bytes"]:
            raise ValueError("image_too_large")
        # 用总像素数限制超大图，避免视觉处理链路成本失控
        if width * height > limits["max_pixels"]:
            raise ValueError("image_resolution_too_large")

    def get_upload_url(
        self,
        filename: str,
        content_type: str,
        user_id: str,
        *,
        file_size: int,
        width: int,
        height: int,
        source_type: str,
    ) -> dict[str, str]:
        """生成图片上传所需的预签名 PUT URL。

        参数:
            filename: 原始文件名。
            content_type: 文件 MIME 类型。
            user_id: 用户标识符。
            file_size: 文件大小，单位字节。
            width: 图片宽度。
            height: 图片高度。
            source_type: 上传来源类型。

        返回:
            包含 upload_url 和 file_url 的字典。
        """
        # 先做严格校验，避免为非法请求生成可用上传地址
        self.validate_upload_request(
            content_type=content_type,
            file_size=file_size,
            width=width,
            height=height,
            source_type=source_type,
        )
        key = self._build_key(user_id=user_id, filename=filename, content_type=content_type)
        client = self._create_client()
        # PUT 预签名 URL 只给短时有效期，降低泄露风险
        upload_url = client.get_presigned_url(
            Method="PUT",
            Bucket=self.bucket,
            Key=key,
            Expired=300,
            Params={"ContentType": content_type},
        )
        return {
            "upload_url": upload_url,
            # 返回稳定 file_url，后续识别、确认、展示都基于它继续派生
            "file_url": self._build_file_url(key),
        }

    def get_presigned_read_url(self, file_url: str, expired: int = 600) -> str:
        """生成对象读取用的预签名 GET URL。

        主要用于视觉模型等外部服务读取私有桶中的图片对象。

        参数:
            file_url: 原始文件 URL。
            expired: URL 有效期，单位秒。

        返回:
            可读的预签名 URL；如果无需处理则返回原始 URL。
        """
        # 空 URL 或已签名 URL 直接返回，避免重复签名
        if not file_url or self._is_signed_url(file_url):
            return file_url
        key = self._extract_bucket_key(file_url)
        if not key:
            # 非当前 bucket 的 URL 不做处理，保持调用方传入语义
            return file_url
        client = self._create_client()
        return client.get_presigned_url(
            Method="GET",
            Bucket=self.bucket,
            Key=key,
            Expired=expired,
        )

    def get_display_url(self, file_url: str, expired: int | None = None) -> str:
        """生成用于应用展示的临时可读 URL。

        在保持桶私有的前提下，为前端图片预览提供短时访问能力。

        参数:
            file_url: 原始文件 URL。
            expired: 可选自定义有效期；未提供时使用默认展示 TTL。

        返回:
            带展示优化参数的可读 URL。
        """
        # 展示链路默认使用更长一些的 TTL，减少频繁重新签名
        ttl = expired if expired is not None else self.read_url_expired
        signed_url = self.get_presigned_read_url(file_url, expired=ttl)

        # 尝试通过响应头覆盖提示浏览器以内联方式展示图片
        parts = urlsplit(signed_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("response-content-disposition", "inline")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
