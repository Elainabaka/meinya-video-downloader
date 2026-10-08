from __future__ import annotations

import argparse
import json

from .models import DownloadRequest
from .service import VideoDownloader


def main() -> int:
    parser = argparse.ArgumentParser(prog="video-downloader", description="Meinya video downloader")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_parser = sub.add_parser("inspect", help="Đọc metadata, không tải media")
    inspect_parser.add_argument("url")
    preview_parser = sub.add_parser("preview", help="Lưu ảnh khung hình và đọc quyền dùng, không tải media")
    preview_parser.add_argument("url")
    preview_parser.add_argument("--output", required=True)
    preview_parser.add_argument("--sheets", type=int, default=12)
    download_parser = sub.add_parser("download", help="Tải một media")
    download_parser.add_argument("url")
    download_parser.add_argument("--output", required=True)
    download_parser.add_argument("--profile", choices=["best", "mp4-compatible", "audio", "audio-mp3"], default="best")
    download_parser.add_argument("--quality", type=int, default=1080)
    args = parser.parse_args()
    service = VideoDownloader()
    if args.command == "inspect":
        print(json.dumps(service.inspect(args.url).to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "preview":
        print(json.dumps(service.preview(args.url, args.output, args.sheets), ensure_ascii=False, indent=2))
        return 0
    result = service.download(DownloadRequest(
        url=args.url,
        output_dir=args.output,
        profile=args.profile,
        quality_limit=args.quality,
    ))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
