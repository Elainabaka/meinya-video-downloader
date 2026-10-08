# Video Downloader

App desktop (Windows) tải một video hoặc tách âm thanh bằng [yt-dlp](https://github.com/yt-dlp/yt-dlp). Sau khi tải, `ffprobe` kiểm tra file cuối cùng; chỉ báo thành công khi file đọc được.

> **Chỉ tải nội dung bạn có quyền, và tuân điều khoản của nền tảng.**

## Yêu cầu

- Windows, Python 3.10 trở lên.
- `ffmpeg` và `ffprobe` trong `PATH`.

## Cài đặt và chạy

```powershell
python -m pip install -r requirements.txt
.\run.bat
```

Dán link, chọn MP4 hoặc âm thanh, chọn thư mục lưu rồi bấm **Tải xuống**.

## Dòng lệnh

```powershell
python -m video_downloader.cli inspect "https://example.com/video"
python -m video_downloader.cli preview "https://example.com/video" --output ".\xem"
python -m video_downloader.cli download "https://example.com/video" --output ".\downloads" --profile mp4-compatible --quality 1080
```

`preview` chỉ lưu ảnh khung hình và thông tin quyền dùng, không tải media.

## Cookie YouTube (tùy chọn)

Chỉ dùng cookie của chính tài khoản YouTube của bạn. Có thể đưa cookie dạng Netscape vào app (nút **Cookie YouTube**) hoặc đặt file vào thư mục `cookie/`. Cookie chỉ lưu trên máy bạn, giao diện không bao giờ hiển thị lại giá trị cookie, và thư mục `cookie/` đã nằm trong `.gitignore`. Không chia sẻ file cookie.

## Giới hạn

- Chỉ tải một video mỗi lần. Playlist và DRM chưa được hỗ trợ.
- Bản này không có cơ chế riêng cho Douyin.
- Nhiều nền tảng thay đổi cách phát liên tục. Khi tải lỗi, thử cập nhật phiên bản `yt-dlp` (đang ghim trong `requirements.txt`).

## Test

```powershell
python -m unittest discover -s tests -v
```

Test mạng chỉ chạy khi đặt `MEINYA_RUN_NETWORK_TEST=1`.

## Giấy phép

- Mã của repo: MIT, xem [LICENSE](LICENSE).
- `yt-dlp`: Unlicense. `pywebview`: BSD-3-Clause.
- `yt-dlp[default]` kéo theo `mutagen`, giấy phép **GPL-2.0-or-later**. Repo này không đóng gói `mutagen`; nó được cài cùng `yt-dlp` khi bạn chạy `pip install -r requirements.txt`.
- Logo và tên **ElainaBaka** là của chủ sở hữu, **không thuộc giấy phép MIT** của repo. Không dùng lại logo hay tên này khi chưa có phép.

---

## English

Windows desktop app that downloads one video, or extracts its audio, with `yt-dlp`. After each download, `ffprobe` checks the final file and only then reports success.

> **Only download content you have the right to download, and follow the platform's terms.**

- Requirements: Windows, Python 3.10+, `ffmpeg` and `ffprobe` on `PATH`.
- Install and run: `python -m pip install -r requirements.txt`, then `.\run.bat`.
- CLI: `python -m video_downloader.cli download "<url>" --output ".\downloads"`.
- YouTube cookies (optional, Netscape format) are for your own account only, stored locally, and never shown back in the UI.
- Single video only. Playlists and DRM are not supported. No Douyin-specific handling.
- Tests: `python -m unittest discover -s tests -v`.
- Code license: MIT. `mutagen` (pulled in by `yt-dlp[default]`) is GPL-2.0-or-later. The logo and the name ElainaBaka are not covered by the MIT license.
