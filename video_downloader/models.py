from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


class ErrorCategory(str, Enum):
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_SOURCE = "unsupported_source"
    AUTH_REQUIRED = "auth_required"
    UNAVAILABLE = "unavailable"
    NETWORK = "network"
    DEPENDENCY_MISSING = "dependency_missing"
    DISK_SPACE = "disk_space"
    POSTPROCESS = "postprocess"
    VERIFICATION = "verification"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class DownloadError(RuntimeError):
    def __init__(self, category: ErrorCategory, message: str, *, retryable: bool = False, cause: str = ""):
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self.message,
            "retryable": self.retryable,
            "cause": self.cause,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dated_output_prefix(value: date | None = None) -> str:
    current = value or date.today()
    return f"T{current.day}-{current.month}-{current.year % 100:02d} + "


def validate_url(value: str) -> str:
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise DownloadError(ErrorCategory.INVALID_REQUEST, "Link phải bắt đầu bằng http:// hoặc https://.")
    if parts.username or parts.password:
        raise DownloadError(ErrorCategory.INVALID_REQUEST, "Không đặt tài khoản hoặc mật khẩu trực tiếp trong link.")
    if (parts.hostname or "").lower() in {"douyin.com", "www.douyin.com"}:
        modal_ids = parse_qs(parts.query).get("modal_id") or []
        if modal_ids and modal_ids[0].isdigit():
            return f"https://www.douyin.com/video/{modal_ids[0]}"
    return url


@dataclass(frozen=True)
class InspectRequest:
    url: str
    allow_playlist: bool = False
    playlist_limit: int | None = None

    @classmethod
    def from_value(cls, value: InspectRequest | dict[str, Any] | str) -> InspectRequest:
        if isinstance(value, cls):
            request = value
        elif isinstance(value, str):
            request = cls(url=value)
        else:
            request = cls(
                url=str(value.get("url", "")),
                allow_playlist=bool(value.get("allow_playlist", False)),
                playlist_limit=value.get("playlist_limit"),
            )
        url = validate_url(request.url)
        limit = request.playlist_limit
        if request.allow_playlist:
            if limit is None:
                limit = 20
            try:
                limit = int(limit)
            except (TypeError, ValueError) as exc:
                raise DownloadError(ErrorCategory.INVALID_REQUEST, "Giới hạn playlist không hợp lệ.") from exc
            if not 1 <= limit <= 100:
                raise DownloadError(ErrorCategory.INVALID_REQUEST, "Giới hạn playlist phải từ 1 đến 100.")
        return cls(url=url, allow_playlist=request.allow_playlist, playlist_limit=limit)


@dataclass(frozen=True)
class DownloadRequest(InspectRequest):
    output_dir: str = ""
    profile: str = "best"
    quality_limit: int | None = 1080
    conflict_policy: str = "reuse"
    write_metadata: bool = False
    retries: int = 3
    timeout_seconds: int = 20

    @classmethod
    def from_value(cls, value: DownloadRequest | dict[str, Any]) -> DownloadRequest:
        if isinstance(value, cls):
            raw = asdict(value)
        else:
            raw = dict(value or {})
        inspected = InspectRequest.from_value(raw)
        if inspected.allow_playlist:
            raise DownloadError(ErrorCategory.INVALID_REQUEST, "Bản 0.1 chỉ tải từng video; playlist sẽ được mở sau khi có partial-result contract.")
        profile = str(raw.get("profile", "best"))
        if profile not in {"best", "mp4-compatible", "audio", "audio-mp3"}:
            raise DownloadError(ErrorCategory.INVALID_REQUEST, "Chế độ tải không hợp lệ.")
        conflict = str(raw.get("conflict_policy", "reuse"))
        if conflict not in {"reuse", "rename", "overwrite"}:
            raise DownloadError(ErrorCategory.INVALID_REQUEST, "Cách xử lý file trùng không hợp lệ.")
        output = str(raw.get("output_dir", "")).strip()
        if not output:
            raise DownloadError(ErrorCategory.INVALID_REQUEST, "Hãy chọn thư mục lưu video.")
        quality = raw.get("quality_limit", 1080)
        if quality in (None, "", "best", 0, "0"):
            quality = None
        else:
            try:
                quality = int(str(quality).lower().replace("p", ""))
            except ValueError as exc:
                raise DownloadError(ErrorCategory.INVALID_REQUEST, "Giới hạn chất lượng không hợp lệ.") from exc
            if quality not in {360, 480, 720, 1080, 1440, 2160, 4320}:
                raise DownloadError(ErrorCategory.INVALID_REQUEST, "Chất lượng phải là một mức video chuẩn.")
        retries = max(0, min(10, int(raw.get("retries", 3))))
        timeout = max(5, min(120, int(raw.get("timeout_seconds", 20))))
        return cls(
            url=inspected.url,
            allow_playlist=inspected.allow_playlist,
            playlist_limit=inspected.playlist_limit,
            output_dir=output,
            profile=profile,
            quality_limit=quality,
            conflict_policy=conflict,
            write_metadata=bool(raw.get("write_metadata", False)),
            retries=retries,
            timeout_seconds=timeout,
        )


@dataclass
class StreamSummary:
    format_id: str
    label: str
    ext: str
    resolution: str
    filesize: int | None = None


@dataclass
class MediaInfo:
    canonical_url: str
    extractor: str
    media_id: str
    title: str
    uploader: str = ""
    duration: float | None = None
    thumbnail_url: str = ""
    is_live: bool = False
    is_playlist: bool = False
    entry_count: int | None = None
    streams: list[StreamSummary] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProgressEvent:
    stage: str
    message: str
    percent: float | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed: float | None = None
    eta: float | None = None
    item_index: int = 1
    item_total: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Artifact:
    path: str
    media_type: str
    container: str
    codecs: list[str]
    byte_size: int


@dataclass
class DownloadResult:
    status: str
    source: dict[str, str] = field(default_factory=dict)
    profile: str = ""
    selected_format: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    metadata_path: str = ""
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    started_at: str = field(default_factory=utc_now)
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_output_dir(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".meinya-write-check"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise DownloadError(ErrorCategory.DISK_SPACE, f"Không thể ghi vào thư mục: {path}", cause=str(exc)) from exc
    return path
