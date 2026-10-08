from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from .models import (
    DownloadError,
    DownloadRequest,
    ErrorCategory,
    InspectRequest,
    MediaInfo,
    ProgressEvent,
    StreamSummary,
    dated_output_prefix,
)


@dataclass
class BackendDownload:
    media: MediaInfo
    path: Path
    selected_format: str
    warnings: list[str]


class _Logger:
    def __init__(self):
        self.warnings: list[str] = []

    def debug(self, message):
        return None

    def info(self, message):
        return None

    def warning(self, message):
        text = str(message).strip()
        if text and text not in self.warnings:
            self.warnings.append(text)

    def error(self, message):
        return None


def _classify_error(exc: Exception) -> DownloadError:
    raw = str(exc)
    text = raw.lower()
    if "unsupported url" in text or "no suitable extractor" in text:
        category, message, retryable = ErrorCategory.UNSUPPORTED_SOURCE, "Nguồn này chưa được hỗ trợ.", False
    elif any(token in text for token in (
        "sign in",
        "login",
        "authentication",
        "members-only",
        "private video",
        "fresh cookies",
    )):
        category, message, retryable = ErrorCategory.AUTH_REQUIRED, "Nội dung cần đăng nhập hoặc không công khai.", False
    elif any(token in text for token in ("timed out", "temporary failure", "connection", "http error 5")):
        category, message, retryable = ErrorCategory.NETWORK, "Kết nối mạng gặp lỗi. Hãy thử lại.", True
    elif any(token in text for token in ("http error 403", "forbidden", "anti-bot", "access denied")):
        category, message, retryable = ErrorCategory.UNAVAILABLE, "Nguồn từ chối truy cập trực tiếp vào nội dung này.", False
    elif any(token in text for token in ("ffmpeg", "postprocessing", "post-processing")):
        category, message, retryable = ErrorCategory.POSTPROCESS, "Không thể ghép hoặc xử lý file media.", False
    elif any(token in text for token in ("unavailable", "removed", "not available", "copyright")):
        category, message, retryable = ErrorCategory.UNAVAILABLE, "Nội dung không còn khả dụng.", False
    else:
        category, message, retryable = ErrorCategory.UNKNOWN, "Không thể xử lý link này.", False
    return DownloadError(category, message, retryable=retryable, cause=raw[-500:])


_DIRECT_MEDIA_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"}
_MAX_ATTEMPTS = 3


def direct_media_id(url: str) -> str | None:
    """Stable, unique id for a direct file link such as a CDN `.../<slug>/1080p.mp4`.

    yt-dlp's generic extractor uses the file name as id, so two different files both
    named `1080p.mp4` got the same output name and `reuse` returned the first file.
    """
    parts = urlsplit(url)
    segments = [item for item in parts.path.split("/") if item]
    if not segments or Path(segments[-1]).suffix.lower() not in _DIRECT_MEDIA_SUFFIXES:
        return None
    readable = "_".join(segments[-2:-1] + [Path(segments[-1]).stem])
    readable = re.sub(r"[^A-Za-z0-9_-]+", "-", readable).strip("-")[:60] or "media"
    digest = hashlib.sha1(parts._replace(fragment="").geturl().encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}"


def _retry_options(exc: Exception) -> dict | None:
    """Option changes for one more attempt after a transient local failure; None = do not retry."""
    text = str(exc).lower()
    if "winerror 32" in text or "being used by another process" in text:
        # Windows (AV/indexer) briefly locks the merged `.temp` file; the retry reuses downloaded parts.
        return {}
    if "http error 416" in text or "range not satisfiable" in text:
        # A stale `.part` from an earlier run cannot be resumed: start the file over.
        return {"continuedl": False}
    return None


AUDIO_PROFILES = {"audio", "audio-mp3"}
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_PREMIUM_AUDIO_KBPS = 200
PREMIUM_PROBE_URL = "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
ROTATED_COOKIE_MESSAGE = (
    "Cookie đã bị trình duyệt xoay vòng nên hết hiệu lực. Lấy cookie mới từ cửa sổ ẩn danh, "
    "xuất xong thì đóng cửa sổ đó (không đăng xuất) để cookie dùng được lâu."
)


def cookie_rotated(warnings: list[str]) -> bool:
    return any("cookies are no longer valid" in text.lower() for text in warnings)


def is_youtube_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in _YOUTUBE_HOSTS


_RIGHTS_LINE = re.compile(
    r"copyright|©|\(c\)|all rights reserved|creative commons|cc[ -]by|cc0|public domain|free to use|no copyright"
    r"|royalty[- ]free|permission|re-?use|re-?upload|credit|版权|授权|bản quyền",
    re.IGNORECASE,
)


def rights_summary(info: dict) -> dict:
    """Những gì nguồn tự ghi về quyền dùng lại. Chỉ là dữ kiện để người/agent xét, không phải giấy phép."""
    license_name = str(info.get("license") or "")
    lines = [line.strip() for line in str(info.get("description") or "").splitlines() if _RIGHTS_LINE.search(line)]
    text = " ".join(lines).lower()
    if "creative commons" in license_name.lower():
        signal = "cc"
    elif "all rights reserved" in text or "版权所有" in text:
        signal = "all-rights-reserved"
    else:
        signal = "unknown"
    return {
        "signal": signal,
        "license": license_name or "standard",
        "statements": lines[:8],
        "channel": str(info.get("channel") or info.get("uploader") or ""),
        "channel_url": str(info.get("channel_url") or info.get("uploader_url") or ""),
        "upload_date": str(info.get("upload_date") or ""),
    }


def pick_storyboard(formats: list[dict]) -> dict | None:
    """Bảng khung hình có ô lớn nhất (YouTube: `sb0`)."""
    boards = [f for f in formats if f.get("format_note") == "storyboard" and f.get("fragments")]
    return max(boards, key=lambda f: f.get("width") or 0, default=None)


def sample_evenly(count: int, limit: int) -> list[int]:
    if count <= limit:
        return list(range(count))
    return sorted({round(i * (count - 1) / (limit - 1)) for i in range(limit)}) if limit > 1 else [0]


def _image_ext(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"


def youtube_music_url(url: str) -> str | None:
    """Đổi link video YouTube sang music.youtube.com; chỉ tại đây tài khoản Premium mới nhận luồng audio ~256k."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    video_id = ""
    if host == "youtu.be":
        video_id = parts.path.strip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS and parts.path == "/watch":
        video_id = (parse_qs(parts.query).get("v") or [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None
    return f"https://music.youtube.com/watch?v={video_id}"


def audio_quality_warnings(info: dict, used_cookie: bool) -> list[str]:
    abr = info.get("abr")
    if not abr or abr >= _PREMIUM_AUDIO_KBPS:
        return []
    if used_cookie:
        return [f"Chưa lấy được luồng Premium ~256k (đang là {round(abr)}k). Cookie có thể đã hết hạn; bấm \"Cookie YouTube\" để dán cookie mới."]
    return [f"Chưa có cookie YouTube Premium nên âm thanh tối đa ~{round(abr)}k."]


@contextlib.contextmanager
def _cookie_copy(source: Path):
    # yt-dlp ghi đè cookiefile khi đóng; dùng bản sao tạm để file của người dùng không bị đổi.
    handle, name = tempfile.mkstemp(prefix="meinya-cookie-", suffix=".txt")
    os.close(handle)
    copy = Path(name)
    try:
        shutil.copyfile(source, copy)
        yield copy
    finally:
        copy.unlink(missing_ok=True)


class YtDlpBackend:
    def __init__(self, cookie_file: Path | Callable[[], Path | None] | None = None):
        # Nhận callable để cookie cập nhật từ UI có hiệu lực ngay ở lượt tải sau.
        self._cookie_file = cookie_file

    def _current_cookie(self) -> Path | None:
        path = self._cookie_file() if callable(self._cookie_file) else self._cookie_file
        return path if path and Path(path).is_file() else None

    def check_premium(self) -> dict:
        """Hỏi YT Music bằng cookie hiện tại xem tài khoản có nhận luồng audio Premium không."""
        cookie = self._current_cookie()
        if not cookie:
            return {"premium": False, "best_audio": "", "message": "Chưa có cookie YouTube."}
        yt_dlp = self._yt_dlp()
        logger = _Logger()
        opts = {
            "quiet": True,
            "no_warnings": False,
            "logger": logger,
            "skip_download": True,
            "ignoreconfig": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "cachedir": False,
        }
        try:
            with _cookie_copy(cookie) as copy:
                opts["cookiefile"] = str(copy)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(PREMIUM_PROBE_URL, download=False)
        except Exception as exc:
            raise _classify_error(exc) from exc
        audio = [
            fmt for fmt in info.get("formats") or []
            if fmt.get("vcodec") == "none" and fmt.get("acodec") not in (None, "none") and fmt.get("abr")
        ]
        best = max(audio, key=lambda fmt: fmt["abr"], default=None)
        if cookie_rotated(logger.warnings):
            return {"premium": False, "best_audio": "", "message": ROTATED_COOKIE_MESSAGE}
        if not best:
            return {"premium": False, "best_audio": "", "message": "YouTube không trả luồng âm thanh nào."}
        label = f"{best.get('format_id')} · {best.get('acodec')} {round(best['abr'])}k"
        premium = best["abr"] >= _PREMIUM_AUDIO_KBPS
        message = (
            f"Premium hoạt động: luồng tốt nhất {label}."
            if premium else
            f"Chưa nhận Premium (tốt nhất {label}). Cookie hết hạn hoặc tài khoản không phải Premium."
        )
        return {"premium": premium, "best_audio": label, "message": message}

    def _yt_dlp(self):
        try:
            import yt_dlp
        except ImportError as exc:
            raise DownloadError(
                ErrorCategory.DEPENDENCY_MISSING,
                "Thiếu yt-dlp. Chạy cài đặt trong README rồi thử lại.",
                cause=str(exc),
            ) from exc
        return yt_dlp

    def inspect(self, request: InspectRequest) -> MediaInfo:
        yt_dlp = self._yt_dlp()
        logger = _Logger()
        opts = {
            "quiet": True,
            "no_warnings": False,
            "logger": logger,
            "skip_download": True,
            "ignoreconfig": True,
            "noplaylist": not request.allow_playlist,
            "playlistend": request.playlist_limit if request.allow_playlist else None,
            "socket_timeout": 20,
            "cachedir": False,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(request.url, download=False)
        except Exception as exc:
            error = _classify_error(exc)
            raise error from exc
        return self._map_media(info, logger.warnings)

    def preview(self, request: InspectRequest, output_dir: Path, max_sheets: int = 12) -> dict:
        """Xem trước khi tải: lưu vài tấm bảng khung hình có sẵn của nguồn (không tải media) và đọc dòng quyền dùng."""
        yt_dlp = self._yt_dlp()
        logger = _Logger()
        opts = {"quiet": True, "no_warnings": False, "logger": logger, "skip_download": True, "ignoreconfig": True,
                "noplaylist": True, "socket_timeout": 20, "cachedir": False}
        sheets: list[dict] = []
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(request.url, download=False)
                media = self._map_media(info, logger.warnings)
                if media.is_playlist:
                    raise DownloadError(ErrorCategory.UNSUPPORTED_SOURCE, "Xem trước chỉ nhận một video, không nhận playlist.")
                board = pick_storyboard(info.get("formats") or [])
                fragments = (board or {}).get("fragments") or []
                starts = [sum(float(f.get("duration") or 0) for f in fragments[:i]) for i in range(len(fragments))]
                per_sheet = ((board or {}).get("rows") or 1) * ((board or {}).get("columns") or 1)
                fps = float((board or {}).get("fps") or 0)   # tấm cuối thường thiếu ô: tính bước theo fps, không theo độ dài tấm
                for index in sample_evenly(len(fragments), max(1, max_sheets)):
                    data = ydl.urlopen(fragments[index]["url"]).read()
                    path = output_dir / f"{media.media_id}-xem-{int(starts[index]):05d}s{_image_ext(data)}"
                    path.write_bytes(data)
                    step = 1 / fps if fps else float(fragments[index].get("duration") or 0) / per_sheet
                    sheets.append({"path": str(path), "start": round(starts[index], 1), "seconds_per_tile": round(step, 2)})
        except DownloadError:
            raise
        except Exception as exc:
            raise _classify_error(exc) from exc
        warnings = list(logger.warnings)
        if not sheets:
            warnings.append("Nguồn này không có bảng khung hình; chỉ có ảnh bìa (thumbnail_url).")
        return {
            "media": {"canonical_url": media.canonical_url, "extractor": media.extractor, "media_id": media.media_id,
                      "title": media.title, "duration": media.duration, "thumbnail_url": media.thumbnail_url},
            "rights": rights_summary(info),
            "grid": {"rows": (board or {}).get("rows"), "columns": (board or {}).get("columns"),
                     "tile_width": (board or {}).get("width"), "tile_height": (board or {}).get("height")},
            "sheets": sheets,
            "warnings": warnings,
        }

    def download(
        self,
        request: DownloadRequest,
        output_dir: Path,
        on_progress: Callable[[ProgressEvent], None] | None,
        is_cancelled: Callable[[], bool] | None,
    ) -> BackendDownload:
        yt_dlp = self._yt_dlp()
        logger = _Logger()
        captured: list[Path] = []
        started = time.time()

        def emit(event: ProgressEvent):
            if on_progress:
                on_progress(event)

        def progress_hook(data):
            if is_cancelled and is_cancelled():
                raise DownloadError(ErrorCategory.CANCELLED, "Đã hủy tải xuống.")
            status = data.get("status")
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            done = data.get("downloaded_bytes")
            percent = (done / total * 100) if done is not None and total else None
            if status == "finished" and data.get("filename"):
                captured.append(Path(data["filename"]))
            emit(ProgressEvent(
                stage="download" if status != "finished" else "merge",
                message="Đang tải dữ liệu…" if status != "finished" else "Đang hoàn thiện file…",
                percent=percent,
                downloaded_bytes=done,
                total_bytes=total,
                speed=data.get("speed"),
                eta=data.get("eta"),
            ))

        def post_hook(data):
            if is_cancelled and is_cancelled():
                raise DownloadError(ErrorCategory.CANCELLED, "Đã hủy tải xuống.")
            info = data.get("info_dict") or {}
            filepath = info.get("filepath")
            if filepath:
                captured.append(Path(filepath))
            emit(ProgressEvent(stage="postprocess", message="Đang xử lý định dạng…", percent=99))

        selector = self._format_selector(request)
        source_url = request.url
        cookie = self._current_cookie() if request.profile in AUDIO_PROFILES and is_youtube_url(request.url) else None
        use_cookie = cookie is not None
        if use_cookie:
            source_url = youtube_music_url(request.url) or request.url
        direct_id = direct_media_id(request.url)
        template = f"{dated_output_prefix()}%(title).140B [{direct_id or '%(id)s'}]"
        if request.conflict_policy == "rename":
            template += " [%(epoch)s]"
        template += ".%(ext)s"
        opts = {
            "quiet": True,
            "no_warnings": False,
            "logger": logger,
            "ignoreconfig": True,
            "noplaylist": not request.allow_playlist,
            "playlistend": request.playlist_limit if request.allow_playlist else None,
            "paths": {"home": str(output_dir), "temp": str(output_dir)},
            "outtmpl": {"default": template},
            "windowsfilenames": True,
            "format": selector,
            "socket_timeout": request.timeout_seconds,
            "retries": request.retries,
            "fragment_retries": request.retries,
            "continuedl": True,
            "overwrites": request.conflict_policy == "overwrite",
            "nooverwrites": request.conflict_policy != "overwrite",
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [post_hook],
            "cachedir": False,
        }
        if request.profile == "mp4-compatible":
            opts["merge_output_format"] = "mp4"
        elif request.profile in AUDIO_PROFILES:
            opts["writethumbnail"] = True
            opts["postprocessors"] = self._audio_postprocessors(request.profile)

        for attempt in range(_MAX_ATTEMPTS):
            try:
                with contextlib.ExitStack() as stack:
                    if cookie:
                        opts["cookiefile"] = str(stack.enter_context(_cookie_copy(cookie)))
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(source_url, download=True)
                        prepared = Path(ydl.prepare_filename(info))
                        captured.append(prepared)
                break
            except DownloadError:
                raise
            except Exception as exc:
                if is_cancelled and is_cancelled():
                    raise DownloadError(ErrorCategory.CANCELLED, "Đã hủy tải xuống.") from exc
                retry = _retry_options(exc)
                if retry is not None and attempt + 1 < _MAX_ATTEMPTS:
                    opts.update(retry)
                    time.sleep(1.0 + attempt)
                    continue
                error = _classify_error(exc)
                raise error from exc

        warnings = list(logger.warnings)
        if request.profile in AUDIO_PROFILES and is_youtube_url(request.url):
            quality = audio_quality_warnings(info, use_cookie)
            if quality and cookie_rotated(warnings):
                quality = [ROTATED_COOKIE_MESSAGE]
            warnings = quality + [text for text in warnings if not cookie_rotated([text])]
        media = self._map_media(info, warnings)
        if direct_id:
            media.media_id = direct_id  # same identity as the file name, unique per URL
        final_path = self._resolve_artifact(captured, output_dir, media.media_id, started, request.profile)
        selected = self._describe_format(info, selector, request.profile)
        return BackendDownload(media=media, path=final_path, selected_format=selected, warnings=warnings)

    @staticmethod
    def _describe_format(info: dict, selector: str, profile: str) -> str:
        format_id = str(info.get("format_id") or selector)
        abr, acodec = info.get("abr"), str(info.get("acodec") or "")
        if profile in AUDIO_PROFILES and abr and acodec not in {"", "none"}:
            return f"{format_id} · {acodec} {round(abr)}k"
        return format_id

    @staticmethod
    def _audio_postprocessors(profile: str) -> list[dict]:
        # "best" giữ nguyên luồng gốc (Opus → .opus, AAC → .m4a) bằng stream copy; chỉ audio-mp3 mới encode lại.
        extract = {"key": "FFmpegExtractAudio", "preferredcodec": "best"}
        if profile == "audio-mp3":
            extract = {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
        return [
            extract,
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail", "already_have_thumbnail": False},
        ]

    @staticmethod
    def _format_selector(request: DownloadRequest) -> str:
        if request.profile in AUDIO_PROFILES:
            return "bestaudio/best"
        # "1080p" means the short side: a vertical 1080×1920 video is 1080p, not 1920p.
        quality = request.quality_limit
        limits = (
            (f"[aspect_ratio>=1][height<={quality}]", f"[aspect_ratio<1][width<={quality}]")
            if quality else ("",)
        )
        codec_order = ("av01", "av1", "hevc", "hev1", "hvc1", "h265", "x265")
        if request.profile == "mp4-compatible":
            preferred = [
                candidate
                for codec in codec_order
                for limit in limits
                for candidate in (
                    f"bv*{limit}[ext=mp4][vcodec^={codec}]+ba[ext=m4a]",
                    f"b{limit}[ext=mp4][vcodec^={codec}]",
                )
            ]
            fallback = [
                candidate
                for template in ("bv*{}[ext=mp4]+ba[ext=m4a]", "b{}[ext=mp4]", "bv*{}+ba", "b{}")
                for candidate in (template.format(limit) for limit in limits)
            ]
            return "/".join((*preferred, *fallback, "best"))
        preferred = [
            candidate
            for codec in codec_order
            for limit in limits
            for candidate in (
                f"bv*{limit}[vcodec^={codec}]+ba",
                f"b{limit}[vcodec^={codec}]",
            )
        ]
        fallback = [
            candidate
            for template in ("bv*{}+ba", "b{}")
            for candidate in (template.format(limit) for limit in limits)
        ]
        return "/".join((*preferred, *fallback, "best"))

    @staticmethod
    def _map_media(info: dict, warnings: list[str]) -> MediaInfo:
        kind = str(info.get("_type") or "")
        is_playlist = kind in {"playlist", "multi_video"} or bool(info.get("entries"))
        entries = info.get("entries") or []
        first = next((entry for entry in entries if entry), None) if is_playlist else info
        source = first or info
        live_status = source.get("live_status")
        is_live = bool(source.get("is_live")) or live_status in {"is_live", "is_upcoming"}
        if is_live:
            raise DownloadError(ErrorCategory.UNAVAILABLE, "Livestream hoặc nội dung đang lên lịch chưa được hỗ trợ.")
        streams: list[StreamSummary] = []
        seen: set[tuple] = set()
        for fmt in source.get("formats") or []:
            height = fmt.get("height")
            width = fmt.get("width")
            resolution = f"{width}×{height}" if width and height else (fmt.get("resolution") or "audio")
            key = (resolution, fmt.get("ext"), fmt.get("vcodec"), fmt.get("acodec"))
            if key in seen:
                continue
            seen.add(key)
            label = resolution
            if fmt.get("fps"):
                label += f" · {fmt['fps']}fps"
            streams.append(StreamSummary(
                format_id=str(fmt.get("format_id") or ""),
                label=label,
                ext=str(fmt.get("ext") or ""),
                resolution=resolution,
                filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
            ))
        streams.sort(key=lambda item: item.filesize or 0, reverse=True)
        return MediaInfo(
            canonical_url=str(source.get("webpage_url") or info.get("webpage_url") or ""),
            extractor=str(source.get("extractor_key") or source.get("extractor") or info.get("extractor") or ""),
            media_id=str(source.get("id") or info.get("id") or ""),
            title=str(info.get("title") or source.get("title") or "Không có tiêu đề"),
            uploader=str(source.get("uploader") or source.get("channel") or ""),
            duration=source.get("duration"),
            thumbnail_url=str(source.get("thumbnail") or ""),
            is_live=is_live,
            is_playlist=is_playlist,
            entry_count=len(entries) if is_playlist else None,
            streams=streams[:16],
            warnings=list(warnings),
        )

    @staticmethod
    def _resolve_artifact(
        candidates: list[Path], output_dir: Path, media_id: str, started: float, profile: str
    ) -> Path:
        ignored = {".part", ".ytdl", ".json", ".jpg", ".jpeg", ".png", ".webp", ".vtt", ".srt"}
        normalized: list[Path] = []
        for path in candidates:
            try:
                resolved = path.resolve()
                resolved.relative_to(output_dir)
            except (OSError, ValueError):
                continue
            if resolved.is_file() and resolved.suffix.lower() not in ignored:
                normalized.append(resolved)
            if profile in AUDIO_PROFILES:
                for suffix in (".opus", ".m4a", ".mp3", ".ogg", ".aac", ".flac"):
                    extracted = resolved.with_suffix(suffix)
                    if extracted.is_file():
                        normalized.append(extracted)
        if not normalized and media_id:
            marker = re.compile(rf"\[{re.escape(media_id)}\]", re.IGNORECASE)
            for path in output_dir.iterdir():
                try:
                    if path.is_file() and marker.search(path.name) and path.suffix.lower() not in ignored:
                        if path.stat().st_mtime >= started - 5:
                            normalized.append(path.resolve())
                except OSError:
                    continue
        if not normalized:
            raise DownloadError(ErrorCategory.POSTPROCESS, "Tải xong nhưng không tìm thấy file media cuối cùng.")
        normalized.sort(key=lambda path: (path.stat().st_mtime, path.stat().st_size), reverse=True)
        return normalized[0]
