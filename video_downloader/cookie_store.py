from __future__ import annotations

import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import DownloadError, ErrorCategory

ENV_FILE_NAME = "MEINYA_COOKIE_FILE"
DEFAULT_COOKIE_NAME = "youtube.txt"
_HEADER = "# Netscape HTTP Cookie File"
_HTTPONLY = "#HttpOnly_"
# yt-dlp cần một cookie phiên và một cookie SAPISID để ký request đăng nhập YouTube.
_SESSION_COOKIES = {"SID", "__Secure-1PSID", "__Secure-3PSID"}
_AUTH_COOKIES = {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"}


@dataclass
class CookieSummary:
    """Trạng thái cookie gửi được cho UI; không bao giờ chứa giá trị cookie."""

    configured: bool
    file_name: str = ""
    cookie_count: int = 0
    logged_in: bool = False
    expires_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cookie_root(root: Path | None) -> Path:
    return (root or Path(__file__).resolve().parent.parent) / "cookie"


def default_cookie_file(root: Path | None = None, environ=None) -> Path | None:
    """Cookie YouTube cục bộ: biến môi trường MEINYA_COOKIE_FILE, sau đó file .txt đầu tiên trong `cookie/`."""
    environ = os.environ if environ is None else environ
    configured = str(environ.get(ENV_FILE_NAME) or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    try:
        return next(iter(sorted(path for path in _cookie_root(root).glob("*.txt") if path.is_file())), None)
    except OSError:
        return None


def cookie_target(root: Path | None = None, environ=None) -> Path:
    """File sẽ bị ghi đè khi cập nhật cookie: đúng file đang dùng, hoặc `cookie/youtube.txt`."""
    environ = os.environ if environ is None else environ
    configured = str(environ.get(ENV_FILE_NAME) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return default_cookie_file(root, environ) or _cookie_root(root) / DEFAULT_COOKIE_NAME


def _parse(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in str(text or "").lstrip("﻿").splitlines():
        line = raw.strip("\r\n")
        if not line.strip() or (line.startswith("#") and not line.startswith(_HTTPONLY)):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            # Clipboard đôi khi đổi tab thành dấu cách; giá trị cookie YouTube không chứa khoảng trắng.
            parts = line.split(None, 6)
        if len(parts) != 7:
            continue
        domain = parts[0][len(_HTTPONLY):] if parts[0].startswith(_HTTPONLY) else parts[0]
        domain = domain.removeprefix(".").lower()
        if domain != "youtube.com" and not domain.endswith(".youtube.com"):
            continue
        try:
            expires = int(parts[4] or 0)
            if expires < 0:
                continue
            datetime.fromtimestamp(expires, timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
        if parts[1] not in {"TRUE", "FALSE"} or parts[3] not in {"TRUE", "FALSE"}:
            continue
        if not parts[2].startswith("/") or not parts[5] or not parts[6]:
            continue
        rows.append(parts)
    return rows


def _summarize(rows: list[list[str]], now: float) -> tuple[bool, str, bool]:
    names = {row[5] for row in rows}
    session_expiry = []
    expired = False
    for row in rows:
        if row[5] not in _SESSION_COOKIES | _AUTH_COOKIES:
            continue
        try:
            expires = int(row[4] or 0)
        except ValueError:
            continue
        if expires > 0:
            session_expiry.append(expires)
            expired = expired or expires <= now
    logged_in = bool(names & _SESSION_COOKIES) and bool(names & _AUTH_COOKIES)
    expires_at = (
        datetime.fromtimestamp(min(session_expiry), timezone.utc).isoformat() if session_expiry else ""
    )
    return logged_in, expires_at, expired


def save_youtube_cookies(text: str, target: Path, now: float | None = None) -> CookieSummary:
    """Kiểm tra cookie dạng Netscape rồi ghi đè `target`; chỉ giữ cookie youtube.com."""
    rows = _parse(text)
    if not rows:
        raise DownloadError(
            ErrorCategory.INVALID_REQUEST,
            "Không thấy cookie youtube.com. Hãy mở music.youtube.com, copy cookie dạng Netscape rồi dán lại.",
        )
    logged_in, _, expired = _summarize(rows, time.time() if now is None else now)
    if not logged_in:
        raise DownloadError(
            ErrorCategory.INVALID_REQUEST,
            "Cookie này chưa đăng nhập YouTube. Hãy đăng nhập tài khoản Premium rồi copy lại.",
        )
    if expired:
        raise DownloadError(ErrorCategory.INVALID_REQUEST, "Cookie đăng nhập trong đoạn dán đã hết hạn.")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join([_HEADER, "", *("\t".join(row) for row in rows)]) + "\n"
    handle, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    temp = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            output.write(body)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return summarize_cookie_file(target, now)


def summarize_cookie_file(path: Path | None, now: float | None = None) -> CookieSummary:
    if not path or not Path(path).is_file():
        return CookieSummary(configured=False)
    path = Path(path)
    try:
        rows = _parse(path.read_text("utf-8", errors="replace"))
        updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return CookieSummary(configured=False, file_name=path.name)
    logged_in, expires_at, expired = _summarize(rows, time.time() if now is None else now)
    return CookieSummary(
        configured=True,
        file_name=path.name,
        cookie_count=len(rows),
        logged_in=logged_in and not expired,
        expires_at=expires_at,
        updated_at=updated,
    )
