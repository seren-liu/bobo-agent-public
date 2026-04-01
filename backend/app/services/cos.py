from __future__ import annotations

import mimetypes
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class COSService:
    def __init__(self) -> None:
        self.secret_id = os.getenv("COS_SECRET_ID", "")
        self.secret_key = os.getenv("COS_SECRET_KEY", "")
        self.region = os.getenv("COS_REGION", "")
        self.bucket = os.getenv("COS_BUCKET", "")
        self.scheme = os.getenv("COS_SCHEME", "https")
        self.read_url_expired = int(os.getenv("COS_READ_URL_EXPIRED_SECONDS", "3600"))

    def _build_ext(self, filename: str, content_type: str) -> str:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext:
            return ext
        guessed = mimetypes.guess_extension(content_type or "")
        if guessed:
            return guessed.lstrip(".")
        return "bin"

    def _build_key(self, user_id: str, filename: str, content_type: str) -> str:
        now = datetime.now(UTC)
        year_month = now.strftime("%Y-%m")
        ext = self._build_ext(filename, content_type)
        timestamp_ms = int(now.timestamp() * 1000)
        short_random = uuid4().hex[:6]
        safe_ext = re.sub(r"[^a-z0-9]", "", ext.lower()) or "bin"
        return f"photos/{user_id}/{year_month}/bobo-{timestamp_ms}-{short_random}.{safe_ext}"

    def _create_client(self):
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except Exception as exc:  # pragma: no cover - tested via monkeypatch
            raise RuntimeError("qcloud_cos is required for COS upload URL generation") from exc

        config = CosConfig(
            Region=self.region,
            SecretId=self.secret_id,
            SecretKey=self.secret_key,
            Scheme=self.scheme,
        )
        return CosS3Client(config)

    def _build_file_url(self, key: str) -> str:
        return f"{self.scheme}://{self.bucket}.cos.{self.region}.myqcloud.com/{key}"

    def _build_bucket_prefix(self) -> str:
        return f"{self.scheme}://{self.bucket}.cos.{self.region}.myqcloud.com/"

    def _is_signed_url(self, file_url: str) -> bool:
        query_keys = {key.lower() for key, _ in parse_qsl(urlsplit(file_url).query, keep_blank_values=True)}
        return "q-sign-algorithm" in query_keys

    def get_upload_url(self, filename: str, content_type: str, user_id: str) -> dict[str, str]:
        if not content_type.startswith("image/"):
            raise ValueError("only image uploads are supported")
        key = self._build_key(user_id=user_id, filename=filename, content_type=content_type)
        client = self._create_client()
        upload_url = client.get_presigned_url(
            Method="PUT",
            Bucket=self.bucket,
            Key=key,
            Expired=300,
            Params={"ContentType": content_type},
        )
        return {
            "upload_url": upload_url,
            "file_url": self._build_file_url(key),
        }

    def get_presigned_read_url(self, file_url: str, expired: int = 600) -> str:
        """Generate a presigned GET URL so external services (e.g. vision models) can read a private object."""
        if not file_url or self._is_signed_url(file_url):
            return file_url
        prefix = self._build_bucket_prefix()
        if not file_url.startswith(prefix):
            return file_url  # not a COS URL in our bucket, return as-is
        key = file_url[len(prefix):]
        client = self._create_client()
        return client.get_presigned_url(
            Method="GET",
            Bucket=self.bucket,
            Key=key,
            Expired=expired,
        )

    def get_display_url(self, file_url: str, expired: int | None = None) -> str:
        """Generate a temporary readable URL for app display while keeping the bucket private."""
        ttl = expired if expired is not None else self.read_url_expired
        signed_url = self.get_presigned_read_url(file_url, expired=ttl)

        # Encourage inline rendering for image previews when COS honors response overrides.
        parts = urlsplit(signed_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("response-content-disposition", "inline")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
