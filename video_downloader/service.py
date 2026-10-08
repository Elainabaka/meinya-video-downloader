from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Protocol

from .models import (
    Artifact,
    DownloadError,
    DownloadRequest,
    DownloadResult,
    ErrorCategory,
    InspectRequest,
    MediaInfo,
    ProgressEvent,
    ensure_output_dir,
    utc_now,
)
from .cookie_store import cookie_target, default_cookie_file, save_youtube_cookies, summarize_cookie_file
from .ytdlp_backend import AUDIO_PROFILES, BackendDownload, YtDlpBackend


class Backend(Protocol):
    def inspect(self, request: InspectRequest) -> MediaInfo: ...

    def download(
        self,
        request: DownloadRequest,
        output_dir: Path,
        on_progress: Callable[[ProgressEvent], None] | None,
        is_cancelled: Callable[[], bool] | None,
    ) -> BackendDownload: ...


class VideoDownloader:
    def __init__(self, backend: Backend | None = None, verifier=None):
        self._backend = backend or YtDlpBackend(cookie_file=default_cookie_file)
        self._verifier = verifier or verify_media

    def inspect(self, request: InspectRequest | dict | str) -> MediaInfo:
        parsed = InspectRequest.from_value(request)
        return self._backend.inspect(parsed)

    def preview(self, request: InspectRequest | dict | str, output_dir: str, max_sheets: int = 12) -> dict:
        """Ảnh khung hình + dòng quyền dùng của một video, không tải media. Ô thứ k của một tấm (trái sang phải, trên xuống
        dưới, tính từ 0) ở giây `start + k * seconds_per_tile`."""
        preview = getattr(self._backend, "preview", None)
        if not preview:
            raise DownloadError(ErrorCategory.UNSUPPORTED_SOURCE, "Backend này không xem trước được.")
        return preview(InspectRequest.from_value(request), ensure_output_dir(output_dir), max_sheets)

    def cookie_status(self) -> dict:
        """Trạng thái cookie YouTube cục bộ, không chứa giá trị cookie."""
        return summarize_cookie_file(default_cookie_file()).to_dict()

    def save_cookies(self, text: str) -> dict:
        """Ghi đè cookie YouTube cục bộ bằng nội dung Netscape người dùng dán vào."""
        return save_youtube_cookies(text, cookie_target()).to_dict()

    def check_premium(self) -> dict:
        check = getattr(self._backend, "check_premium", None)
        if not check:
            raise DownloadError(ErrorCategory.UNSUPPORTED_SOURCE, "Backend này không kiểm tra được Premium.")
        return check()

    def download(
        self,
        request: DownloadRequest | dict,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        started = utc_now()
        profile = ""
        try:
            parsed = DownloadRequest.from_value(request)
            profile = parsed.profile
            output_dir = ensure_output_dir(parsed.output_dir)
            if is_cancelled and is_cancelled():
                raise DownloadError(ErrorCategory.CANCELLED, "Đã hủy tải xuống.")
            if on_progress:
                on_progress(ProgressEvent(stage="inspect", message="Đang đọc thông tin link…", percent=1))
            payload = self._backend.download(parsed, output_dir, on_progress, is_cancelled)
            if on_progress:
                on_progress(ProgressEvent(stage="verify", message="Đang kiểm tra file cuối…", percent=99))
            probe = self._verifier(payload.path, parsed.profile)
            artifact = Artifact(
                path=str(payload.path),
                media_type="audio" if parsed.profile in AUDIO_PROFILES else "video",
                container=str((probe.get("format") or {}).get("format_name") or payload.path.suffix.lstrip(".")),
                codecs=[
                    str(stream.get("codec_name"))
                    for stream in probe.get("streams", [])
                    if stream.get("codec_name") and not (stream.get("disposition") or {}).get("attached_pic")
                ],
                byte_size=payload.path.stat().st_size,
            )
            metadata_path = ""
            result = DownloadResult(
                status="success",
                source={
                    "extractor": payload.media.extractor,
                    "media_id": payload.media.media_id,
                    "canonical_url": payload.media.canonical_url,
                    "title": payload.media.title,
                },
                profile=parsed.profile,
                selected_format=payload.selected_format,
                artifacts=[artifact],
                warnings=payload.warnings,
                started_at=started,
                finished_at=utc_now(),
            )
            if parsed.write_metadata:
                manifest = payload.path.with_suffix(payload.path.suffix + ".meinya.json")
                result.metadata_path = str(manifest)
                manifest.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            if on_progress:
                on_progress(ProgressEvent(stage="verify", message="Hoàn tất", percent=100))
            return result
        except DownloadError as exc:
            status = "cancelled" if exc.category == ErrorCategory.CANCELLED else "failed"
            return DownloadResult(
                status=status,
                profile=profile,
                error=exc.to_dict(),
                started_at=started,
                finished_at=utc_now(),
            )
        except Exception as exc:
            error = DownloadError(ErrorCategory.UNKNOWN, "Có lỗi ngoài dự kiến.", cause=str(exc))
            return DownloadResult(
                status="failed",
                profile=profile,
                error=error.to_dict(),
                started_at=started,
                finished_at=utc_now(),
            )


def verify_media(path: Path, profile: str) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise DownloadError(ErrorCategory.DEPENDENCY_MISSING, "Thiếu ffprobe để kiểm tra file đã tải.")
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except OSError as exc:
        raise DownloadError(ErrorCategory.VERIFICATION, "Không chạy được ffprobe.", cause=str(exc)) from exc
    if proc.returncode != 0:
        raise DownloadError(ErrorCategory.VERIFICATION, "File tải về không đọc được.", cause=proc.stderr[-500:])
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DownloadError(ErrorCategory.VERIFICATION, "ffprobe trả về dữ liệu không hợp lệ.") from exc
    streams = data.get("streams") or []
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    if profile in AUDIO_PROFILES and not has_audio:
        raise DownloadError(ErrorCategory.VERIFICATION, "File cuối không có luồng âm thanh.")
    if profile not in AUDIO_PROFILES and not has_video:
        raise DownloadError(ErrorCategory.VERIFICATION, "File cuối không có luồng video.")
    return data
