from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _fatal(root: Path, exc: Exception):
    try:
        (root / "downloader_error.log").write_text(traceback.format_exc(), "utf-8")
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"Meinya Video không thể khởi động:\n\n{exc}\n\nChi tiết: downloader_error.log",
                "Meinya Video · Error",
                0x10,
            )
            return
        except Exception:
            pass
    print(f"[Meinya Video] Fatal: {exc}", file=sys.stderr)


def main():
    import webview
    from app_api import DownloaderAPI

    root = Path(__file__).resolve().parent
    api = DownloaderAPI(root)
    window = webview.create_window(
        "Meinya Video · ElainaBaka",
        url=(root / "web" / "index.html").as_uri(),
        js_api=api,
        width=1360,
        height=860,
        min_size=(980, 680),
        background_color="#0b0914",
        text_select=False,
    )
    api.bind_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    app_root = Path(__file__).resolve().parent
    try:
        main()
    except Exception as error:
        _fatal(app_root, error)
