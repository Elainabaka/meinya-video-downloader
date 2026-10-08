"""Tính năng tải khi cần của Meinya Video.

Chỉ có một thứ nặng: ffmpeg (kèm ffprobe). yt-dlp cần ffmpeg để ghép luồng và tách MP3, còn ffprobe
kiểm file sau khi tải. Thiếu hai thứ này thì không tải được, nên app hỏi trước khi cài.
Playwright không cần nữa: chỉ phần Douyin dùng nó, và bản này không có phần Douyin.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading

# Bản đang chạy trên máy chủ nhân (ffmpeg-8.0.1-full_build). Ghim để mọi máy cài đúng bản đã kiểm.
FFMPEG = {
    "ten": "ffmpeg",
    "mo_ta": "Ghép luồng video và âm thanh, tách MP3, và kiểm file sau khi tải. Thiếu thì không tải được.",
    "winget": "Gyan.FFmpeg",
    "ban": "8.0.1",
    "trang": "https://ffmpeg.org/download.html",
    "dung_luong": "khoảng 234 MB tải về, khoảng 616 MB sau khi cài (đo từ bản 8.0.1)",
    "nguon": "winget, gói Gyan.FFmpeg 8.0.1 (zip từ github.com/GyanD/codexffmpeg, winget kiểm SHA-256). Trang chính thức: ffmpeg.org",
    "giay_phep": "GPL-3.0 (bản build full của Gyan; xem ffmpeg.org/legal.html)",
}

_khoa = threading.Lock()
_dang_cai = threading.Event()
_loi = {"text": None}


def ffmpeg_co() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def trang_thai() -> dict:
    """Trạng thái để giao diện quyết định có hỏi cài không."""
    return {
        "co": ffmpeg_co(),
        "dang_cai": _dang_cai.is_set(),
        "loi": _loi["text"],
        "winget": shutil.which("winget") is not None,
    }


def thong_tin() -> dict:
    """Chữ cho hộp thoại: tên, mô tả, dung lượng, nguồn, giấy phép."""
    return {key: FFMPEG[key] for key in ("ten", "mo_ta", "dung_luong", "nguon", "giay_phep")}


def _winget_mac_dinh(args: list):
    ket_qua = subprocess.run(["winget", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if ket_qua.returncode != 0:
        duoi = (ket_qua.stdout or ket_qua.stderr).strip().splitlines()[-3:]
        raise RuntimeError("winget lỗi: " + " | ".join(duoi))


def cai_ffmpeg(tien_trinh=None, chay=None):
    """Cài ffmpeg bằng winget, ghim bản FFMPEG["ban"]. Không có winget thì báo đường dẫn tải tay.

    tien_trinh(giai_doan, phan_so) nhận tiến trình; chay thay lệnh winget khi test.
    """
    tien = tien_trinh or (lambda giai_doan, frac: None)
    if chay is None and not shutil.which("winget"):
        raise RuntimeError(f"Máy chưa có winget. Tải ffmpeg tại {FFMPEG['trang']}, rồi thêm vào PATH.")
    with _khoa:
        if _dang_cai.is_set():
            raise RuntimeError("Đang cài ffmpeg rồi.")
        _dang_cai.set()
        _loi["text"] = None
    try:
        tien("winget đang cài ffmpeg (có thể vài phút)", 0.1)
        (chay or _winget_mac_dinh)(["install", "-e", "--id", FFMPEG["winget"], "--version", FFMPEG["ban"],
                                    "--accept-package-agreements", "--accept-source-agreements"])
        # winget đặt ffmpeg sau shim trong WinGet\Links; app đang chạy chưa có trong PATH nên thêm vào tại đây
        links = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links")
        if os.path.isdir(links) and links not in os.environ.get("PATH", ""):
            os.environ["PATH"] = links + os.pathsep + os.environ.get("PATH", "")
        if not ffmpeg_co():
            raise RuntimeError("Cài xong nhưng chưa thấy ffmpeg. Đóng app, mở lại rồi bấm tải lại.")
        tien("Xong", 1.0)
    except Exception as exc:
        _loi["text"] = str(exc) or type(exc).__name__
        raise
    finally:
        _dang_cai.clear()
