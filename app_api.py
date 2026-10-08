from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import webview

from video_downloader.models import DownloadError, ProgressEvent
from video_downloader.service import VideoDownloader


def _open_path(path: str):
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - local user-selected folder
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


class DownloaderAPI:
    def __init__(self, root: Path):
        self._root = root
        self._window = None
        self._service = VideoDownloader()
        self._state_path = root / ".downloader_state.json"
        self._state = self._load_state()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._job = self._idle_job()

    @staticmethod
    def _idle_job():
        return {"running": False, "stage": "idle", "message": "Sẵn sàng", "percent": 0, "result": None}

    def bind_window(self, window):
        self._window = window

    def _load_state(self):
        try:
            return json.loads(self._state_path.read_text("utf-8"))
        except Exception:
            return {}

    def _save_state(self):
        try:
            self._state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), "utf-8")
        except OSError:
            pass

    def initial_state(self):
        output = self._state.get("output_dir")
        if not output:
            output = str(Path.home() / "Downloads" / "Meinya Video")
        return {
            "output_dir": output,
            "profile": self._state.get("profile", "mp4-compatible"),
            "quality_limit": self._state.get("quality_limit", 1080),
            "version": "0.2.0",
        }

    def inspect_url(self, url: str):
        try:
            return {"ok": True, "media": self._service.inspect(url).to_dict()}
        except DownloadError as exc:
            return {"ok": False, "error": exc.to_dict()}
        except Exception as exc:
            return {"ok": False, "error": {"message": "Không đọc được link này.", "cause": str(exc)}}

    # Cookie chỉ đi một chiều từ UI vào: các hàm dưới trả trạng thái, không bao giờ trả giá trị cookie.
    def cookie_status(self):
        try:
            return {"ok": True, "cookie": self._service.cookie_status()}
        except Exception as exc:
            return {"ok": False, "error": {"message": "Không đọc được trạng thái cookie.", "cause": str(exc)}}

    def save_cookies(self, text: str):
        try:
            return {"ok": True, "cookie": self._service.save_cookies(text)}
        except DownloadError as exc:
            return {"ok": False, "error": exc.to_dict()}
        except Exception as exc:
            return {"ok": False, "error": {"message": "Không lưu được cookie.", "cause": str(exc)}}

    def check_premium(self):
        try:
            return {"ok": True, **self._service.check_premium()}
        except DownloadError as exc:
            return {"ok": False, "error": exc.to_dict()}
        except Exception as exc:
            return {"ok": False, "error": {"message": "Không kiểm tra được Premium.", "cause": str(exc)}}

    def choose_output(self):
        if not self._window:
            return ""
        try:
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        except Exception:
            try:
                result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            except Exception:
                return ""
        if isinstance(result, (list, tuple)):
            result = result[0] if result else ""
        if result:
            self._state["output_dir"] = str(result)
            self._save_state()
        return str(result or "")

    def start_download(self, payload: dict):
        with self._lock:
            if self._job.get("running"):
                return {"ok": False, "error": "Đang có một lượt tải khác."}
            self._cancel.clear()
            self._job = {"running": True, "stage": "inspect", "message": "Đang chuẩn bị…", "percent": 1, "result": None}
        self._state.update({
            "output_dir": payload.get("output_dir", ""),
            "profile": payload.get("profile", "mp4-compatible"),
            "quality_limit": payload.get("quality_limit", 1080),
        })
        self._save_state()

        def on_progress(event: ProgressEvent):
            with self._lock:
                self._job.update({key: value for key, value in event.to_dict().items() if value is not None})
                self._job["running"] = True

        def worker():
            result = self._service.download(payload, on_progress=on_progress, is_cancelled=self._cancel.is_set)
            with self._lock:
                self._job.update(
                    running=False,
                    stage="done" if result.status == "success" else result.status,
                    message="Tải xong" if result.status == "success" else (
                        "Đã hủy" if result.status == "cancelled" else (result.error or {}).get("message", "Tải thất bại")
                    ),
                    percent=100 if result.status == "success" else self._job.get("percent", 0),
                    result=result.to_dict(),
                )

        threading.Thread(target=worker, name="meinya-video-download", daemon=True).start()
        return {"ok": True}

    def job_status(self):
        with self._lock:
            return dict(self._job)

    def cancel_download(self):
        self._cancel.set()
        return {"ok": True}

    def open_output(self):
        output = self._state.get("output_dir", "")
        if not output or not Path(output).is_dir():
            return {"ok": False, "error": "Thư mục lưu chưa tồn tại."}
        try:
            _open_path(output)
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
