from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from datetime import date
from pathlib import Path

from video_downloader.models import (
    DownloadError,
    DownloadRequest,
    ErrorCategory,
    InspectRequest,
    MediaInfo,
    ProgressEvent,
    dated_output_prefix,
)
from video_downloader.service import VideoDownloader
from video_downloader.ytdlp_backend import (
    BackendDownload,
    YtDlpBackend,
    _classify_error,
    _retry_options,
    audio_quality_warnings,
    cookie_rotated,
    direct_media_id,
    pick_storyboard,
    rights_summary,
    sample_evenly,
    youtube_music_url,
)
from video_downloader.cookie_store import (
    cookie_target,
    default_cookie_file,
    save_youtube_cookies,
    summarize_cookie_file,
)

FUTURE = "2000000000"
PASTED_COOKIES = "\n".join([
    "# Netscape HTTP Cookie File",
    "# comment from extension",
    "\t".join([".youtube.com", "TRUE", "/", "TRUE", FUTURE, "SID", "sid-value"]),
    "\t".join(["#HttpOnly_.youtube.com", "TRUE", "/", "TRUE", FUTURE, "__Secure-3PSID", "psid-value"]),
    "\t".join([".youtube.com", "TRUE", "/", "TRUE", FUTURE, "SAPISID", "sapisid-value"]),
    "\t".join([".google.com", "TRUE", "/", "TRUE", FUTURE, "NID", "google-only"]),
])


class FakeBackend:
    def inspect(self, request):
        return MediaInfo(
            canonical_url=request.url,
            extractor="fixture",
            media_id="abc123",
            title="Fixture video",
            duration=12,
        )

    def download(self, request, output_dir, on_progress, is_cancelled):
        if on_progress:
            on_progress(ProgressEvent(stage="download", message="fixture", percent=50))
        path = output_dir / "Fixture video [abc123].mp4"
        path.write_bytes(b"fixture")
        return BackendDownload(self.inspect(request), path, "fixture-format", [])


def fake_verify(path, profile):
    return {
        "format": {"format_name": "mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }


class _FlakyYDL:
    """Fake yt_dlp.YoutubeDL: the first call fails with `error`, the next one writes the file."""

    calls: list[dict] = []
    error: Exception = Exception("")

    def __init__(self, opts):
        self.opts = opts
        self.path = Path(opts["paths"]["home"]) / "clip.mp4"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        _FlakyYDL.calls.append(dict(self.opts))
        if len(_FlakyYDL.calls) == 1:
            raise _FlakyYDL.error
        self.path.write_bytes(b"media")
        return {"id": "1080p", "title": "1080p", "extractor": "generic", "webpage_url": url, "ext": "mp4"}

    def prepare_filename(self, info):
        return str(self.path)


class ContractTests(unittest.TestCase):
    def test_direct_links_with_same_file_name_get_different_ids(self):
        # Coverr serves every clip as .../<slug>/1080p.mp4 and yt-dlp's generic id was just "1080p",
        # so the second clip "reused" the first file.
        a = direct_media_id("https://cdn.coverr.co/videos/coverr-typing-on-laptop/1080p.mp4")
        b = direct_media_id("https://cdn.coverr.co/videos/coverr-city-at-night/1080p.mp4")
        self.assertNotEqual(a, b)
        self.assertEqual(a, direct_media_id("https://cdn.coverr.co/videos/coverr-typing-on-laptop/1080p.mp4"))
        self.assertTrue(a.startswith("coverr-typing-on-laptop_1080p-"))
        self.assertIsNone(direct_media_id("https://www.youtube.com/watch?v=nZoaZDOe6bg"))

    def test_transient_local_failures_are_retried(self):
        locked = OSError("[WinError 32] The process cannot access the file because it is being used by another process")
        self.assertEqual(_retry_options(locked), {})
        self.assertEqual(_retry_options(Exception("HTTP Error 416: Requested Range Not Satisfiable")), {"continuedl": False})
        self.assertIsNone(_retry_options(Exception("HTTP Error 403: Forbidden")))

    def test_download_restarts_stale_part_and_names_direct_links_uniquely(self):
        _FlakyYDL.calls = []
        _FlakyYDL.error = Exception("HTTP Error 416: Requested Range Not Satisfiable")
        url = "https://cdn.coverr.co/videos/coverr-city-at-night/1080p.mp4"
        request = DownloadRequest.from_value({"url": url, "output_dir": ".", "profile": "mp4-compatible"})
        fake = mock.Mock(YoutubeDL=_FlakyYDL)
        with tempfile.TemporaryDirectory() as temp,                 mock.patch.object(YtDlpBackend, "_yt_dlp", return_value=fake),                 mock.patch("video_downloader.ytdlp_backend.time.sleep"):
            result = YtDlpBackend().download(request, Path(temp).resolve(), None, None)
        self.assertEqual(len(_FlakyYDL.calls), 2)
        self.assertTrue(_FlakyYDL.calls[0]["continuedl"])
        self.assertFalse(_FlakyYDL.calls[1]["continuedl"])
        self.assertEqual(result.media.media_id, direct_media_id(url))
        self.assertIn(f"[{direct_media_id(url)}]", _FlakyYDL.calls[0]["outtmpl"]["default"])

    def test_rejects_non_http_url(self):
        with self.assertRaises(DownloadError):
            DownloadRequest.from_value({"url": "file:///secret", "output_dir": "."})

    def test_normalizes_request(self):
        request = DownloadRequest.from_value({
            "url": "https://example.com/video",
            "output_dir": ".",
            "profile": "mp4-compatible",
            "quality_limit": "1080p",
        })
        self.assertEqual(request.quality_limit, 1080)
        self.assertEqual(request.profile, "mp4-compatible")
        self.assertFalse(request.write_metadata)

    def test_output_date_prefix(self):
        self.assertEqual(dated_output_prefix(date(2026, 9, 10)), "T10-9-26 + ")

    def test_normalizes_douyin_modal_url(self):
        request = InspectRequest.from_value(
            "https://www.douyin.com/jingxuan?modal_id=7681847483353386281"
        )
        self.assertEqual(
            request.url,
            "https://www.douyin.com/video/7681847483353386281",
        )

    def test_douyin_cookie_failure_is_auth_required(self):
        error = _classify_error(RuntimeError("Fresh cookies (not necessarily logged in) are needed"))
        self.assertEqual(error.category, ErrorCategory.AUTH_REQUIRED)
        self.assertFalse(error.retryable)

    def test_format_policy(self):
        request = DownloadRequest.from_value({
            "url": "https://example.com/video", "output_dir": ".",
            "profile": "mp4-compatible", "quality_limit": 720,
        })
        selector = YtDlpBackend._format_selector(request)
        self.assertIn("[aspect_ratio>=1][height<=720]", selector)
        self.assertIn("[aspect_ratio<1][width<=720]", selector)
        self.assertIn("ext=mp4", selector)
        self.assertLess(selector.index("vcodec^=av01"), selector.index("vcodec^=hevc"))
        self.assertLess(selector.index("vcodec^=hevc"), selector.index("bv*[aspect_ratio>=1][height<=720][ext=mp4]+ba"))
        self.assertTrue(selector.endswith("/best"))

    def test_quality_limit_uses_short_side_for_vertical_video(self):
        import yt_dlp

        formats = [
            {"format_id": f"{codec}-{w}", "url": "https://example.com/v", "ext": "mp4",
             "width": w, "height": h, "vcodec": codec, "acodec": "none", "tbr": w}
            for codec in ("avc1.640033", "av01.0.08M.08")
            for w, h in ((480, 852), (720, 1280), (1080, 1920), (2160, 3840))
        ] + [{"format_id": "a", "url": "https://example.com/a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128}]
        info = {"id": "x", "title": "x", "extractor": "test", "extractor_key": "Test", "webpage_url": "https://example.com/v", "formats": formats}
        for profile in ("best", "mp4-compatible"):
            request = DownloadRequest.from_value({"url": "https://example.com/v", "output_dir": ".", "profile": profile, "quality_limit": 1080})
            with yt_dlp.YoutubeDL({"quiet": True, "simulate": True, "format": YtDlpBackend._format_selector(request)}) as ydl:
                chosen = ydl.process_ie_result(dict(info), download=False)
            self.assertEqual(chosen["format_id"], "av01.0.08M.08-1080+a", profile)

    def test_audio_profile_keeps_source_stream(self):
        request = DownloadRequest.from_value({"url": "https://example.com/v", "output_dir": ".", "profile": "audio"})
        self.assertEqual(YtDlpBackend._format_selector(request), "bestaudio/best")
        extract = YtDlpBackend._audio_postprocessors("audio")[0]
        self.assertEqual(extract, {"key": "FFmpegExtractAudio", "preferredcodec": "best"})

    def test_audio_mp3_profile_is_explicit_reencode(self):
        request = DownloadRequest.from_value({"url": "https://example.com/v", "output_dir": ".", "profile": "audio-mp3"})
        self.assertEqual(YtDlpBackend._format_selector(request), "bestaudio/best")
        self.assertEqual(YtDlpBackend._audio_postprocessors("audio-mp3")[0]["preferredcodec"], "mp3")

    def test_youtube_links_map_to_music_domain(self):
        expected = "https://music.youtube.com/watch?v=kJQP7kiw5Fk"
        for url in (
            "https://www.youtube.com/watch?v=kJQP7kiw5Fk&list=RD1&t=10",
            "https://m.youtube.com/watch?v=kJQP7kiw5Fk",
            "https://youtu.be/kJQP7kiw5Fk?si=abc",
            "https://music.youtube.com/watch?v=kJQP7kiw5Fk&feature=share",
        ):
            self.assertEqual(youtube_music_url(url), expected, url)
        self.assertIsNone(youtube_music_url("https://www.youtube.com/playlist?list=PL1"))
        self.assertIsNone(youtube_music_url("https://example.com/watch?v=kJQP7kiw5Fk"))

    def test_cookie_file_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertIsNone(default_cookie_file(root, environ={}))
            (root / "cookie").mkdir()
            cookie = root / "cookie" / "yt premium.txt"
            cookie.write_text("# Netscape HTTP Cookie File\n", "utf-8")
            self.assertEqual(default_cookie_file(root, environ={}), cookie)
            explicit = root / "other.txt"
            explicit.write_text("", "utf-8")
            self.assertEqual(default_cookie_file(root, environ={"MEINYA_COOKIE_FILE": str(explicit)}), explicit)
            self.assertIsNone(default_cookie_file(root, environ={"MEINYA_COOKIE_FILE": str(root / "missing.txt")}))

    def test_save_cookies_keeps_only_youtube_and_hides_values(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "cookie" / "youtube.txt"
            summary = save_youtube_cookies(PASTED_COOKIES, target, now=1_700_000_000)
            saved = target.read_text("utf-8")
            self.assertTrue(saved.startswith("# Netscape HTTP Cookie File"))
            self.assertIn("psid-value", saved)
            self.assertNotIn("google-only", saved)
            self.assertEqual(summary.cookie_count, 3)
            self.assertTrue(summary.logged_in)
            self.assertTrue(summary.expires_at.startswith("2033-"))
            self.assertNotIn("value", str(summary.to_dict()))
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_save_cookies_accepts_spaces_from_clipboard(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "youtube.txt"
            summary = save_youtube_cookies(PASTED_COOKIES.replace("\t", "    "), target, now=1_700_000_000)
            self.assertEqual(summary.cookie_count, 3)
            self.assertIn("\tSAPISID\t", target.read_text("utf-8"))

    def test_save_cookies_rejects_bad_input_without_touching_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "youtube.txt"
            target.write_text("old", "utf-8")
            logged_out = "\n".join(line for line in PASTED_COOKIES.splitlines() if "SAPISID" not in line)
            expired = PASTED_COOKIES.replace(FUTURE, "1600000000")
            expired_auth = PASTED_COOKIES.replace(FUTURE + "\tSAPISID", "1600000000\tSAPISID")
            invalid_cookies = (
                "", "hello world", logged_out, expired, expired_auth,
                PASTED_COOKIES.replace(".youtube.com", ".notyoutube.com"),
                *(PASTED_COOKIES.replace(FUTURE, expiry) for expiry in ("nan", "inf", "-1", "999999999999999999999")),
            )
            for text in invalid_cookies:
                with self.subTest(text=text), self.assertRaises(DownloadError):
                    save_youtube_cookies(text, target, now=1_700_000_000)
                self.assertEqual(target.read_text("utf-8"), "old")
            self.assertEqual(target.read_text("utf-8"), "old")

    def test_cookie_status_rejects_expired_auth_and_malformed_expiry(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "youtube.txt"
            for expiry in ("1600000000", "nan", "inf", "999999999999999999999"):
                target.write_text(PASTED_COOKIES.replace(FUTURE + "\tSAPISID", expiry + "\tSAPISID"), "utf-8")
                with self.subTest(expiry=expiry):
                    summary = summarize_cookie_file(target, now=1_700_000_000)
                    self.assertFalse(summary.logged_in)
                    self.assertNotIn("sapisid-value", str(summary.to_dict()))

    def test_cookie_target_overwrites_file_in_use(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(cookie_target(root, environ={}), root / "cookie" / "youtube.txt")
            (root / "cookie").mkdir()
            existing = root / "cookie" / "yt premium.txt"
            existing.write_text("", "utf-8")
            self.assertEqual(cookie_target(root, environ={}), existing)
            self.assertEqual(cookie_target(root, environ={"MEINYA_COOKIE_FILE": str(root / "x.txt")}), root / "x.txt")
            self.assertFalse(summarize_cookie_file(None).configured)

    def test_detects_rotated_youtube_cookies(self):
        warning = "[youtube] The provided YouTube account cookies are no longer valid. They have likely been rotated"
        self.assertTrue(cookie_rotated([warning]))
        self.assertFalse(cookie_rotated(["Some other warning"]))

    def test_audio_quality_warnings(self):
        self.assertEqual(audio_quality_warnings({"abr": 257}, used_cookie=True), [])
        self.assertIn("Cookie", audio_quality_warnings({"abr": 129}, used_cookie=True)[0])
        self.assertIn("Premium", audio_quality_warnings({"abr": 129}, used_cookie=False)[0])
        self.assertEqual(audio_quality_warnings({}, used_cookie=False), [])

    def test_audio_format_description_names_source_stream(self):
        info = {"format_id": "774", "acodec": "opus", "abr": 257.3}
        self.assertEqual(YtDlpBackend._describe_format(info, "bestaudio/best", "audio"), "774 · opus 257k")
        self.assertEqual(YtDlpBackend._describe_format({"format_id": "401+251"}, "sel", "best"), "401+251")

    def test_playlist_download_is_rejected_until_partial_results_exist(self):
        with self.assertRaises(DownloadError):
            DownloadRequest.from_value({
                "url": "https://example.com/playlist",
                "output_dir": ".",
                "allow_playlist": True,
                "playlist_limit": 5,
            })

    def test_service_publishes_verified_result_and_manifest(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            service = VideoDownloader(FakeBackend(), verifier=fake_verify)
            result = service.download({
                "url": "https://example.com/video",
                "output_dir": temp,
                "profile": "best",
                "write_metadata": True,
            }, on_progress=events.append)
            self.assertEqual(result.status, "success")
            self.assertTrue(Path(result.artifacts[0].path).is_file())
            self.assertTrue(Path(result.metadata_path).is_file())
            self.assertEqual(events[-1].percent, 100)

    def test_service_does_not_write_manifest_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            result = VideoDownloader(FakeBackend(), verifier=fake_verify).download({
                "url": "https://example.com/video",
                "output_dir": temp,
                "profile": "best",
            })
            self.assertEqual(result.status, "success")
            self.assertEqual(result.metadata_path, "")
            self.assertEqual(list(Path(temp).glob("*.meinya.json")), [])

    def test_rights_summary_reports_what_the_source_states(self):
        reserved = rights_summary({"description": "How it is made\n🎥Copyright(C) 2026. X. All rights reserved.", "channel": "X"})
        self.assertEqual((reserved["signal"], reserved["license"], len(reserved["statements"])), ("all-rights-reserved", "standard", 1))
        cc = rights_summary({"license": "Creative Commons Attribution license (reuse allowed)", "description": "plain"})
        self.assertEqual((cc["signal"], cc["statements"]), ("cc", []))
        self.assertEqual(rights_summary({"description": "Free to use with credit"})["signal"], "unknown")

    def test_preview_picks_largest_storyboard_and_samples_evenly(self):
        formats = [{"format_note": "storyboard", "width": 48, "fragments": [{}]},
                   {"format_note": "storyboard", "width": 341, "fragments": [{}, {}]},
                   {"format_note": "1080p", "width": 1920}]
        self.assertEqual(pick_storyboard(formats)["width"], 341)
        self.assertIsNone(pick_storyboard([{"format_note": "1080p", "width": 1920}]))
        self.assertEqual(sample_evenly(3, 12), [0, 1, 2])
        self.assertEqual(sample_evenly(18, 6), [0, 3, 7, 10, 14, 17])
        self.assertEqual(sample_evenly(18, 1), [0])

    def test_preview_needs_backend_support(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(DownloadError) as caught:
                VideoDownloader(FakeBackend(), verifier=fake_verify).preview("https://example.com/video", temp)
            self.assertEqual(caught.exception.category, ErrorCategory.UNSUPPORTED_SOURCE)

    def test_failure_is_a_typed_result(self):
        service = VideoDownloader(FakeBackend(), verifier=fake_verify)
        result = service.download({"url": "not-a-url", "output_dir": "."})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error["category"], "invalid_request")

    def test_cancel_before_start_is_typed_result(self):
        with tempfile.TemporaryDirectory() as temp:
            service = VideoDownloader(FakeBackend(), verifier=fake_verify)
            result = service.download({
                "url": "https://example.com/video",
                "output_dir": temp,
            }, is_cancelled=lambda: True)
            self.assertEqual(result.status, "cancelled")
            self.assertEqual(result.error["category"], "cancelled")


if __name__ == "__main__":
    unittest.main()
