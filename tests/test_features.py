"""Tính năng tải khi cần (ffmpeg): kiểm tra có, cài bằng winget đúng bản ghim, và chặn tải khi thiếu.

Không gọi winget thật và không gọi mạng: lệnh winget và việc cài được thay bằng bản giả.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from video_downloader import features as F


class FfmpegFeatureTest(unittest.TestCase):
    def setUp(self):
        F._loi["text"] = None

    def test_co_khi_du_ffmpeg_va_ffprobe(self):
        with mock.patch.object(F.shutil, "which", side_effect=lambda name: f"/bin/{name}"):
            self.assertTrue(F.ffmpeg_co())

    def test_thieu_ffprobe_thi_chua_co(self):
        def which(name):
            return None if name == "ffprobe" else f"/bin/{name}"
        with mock.patch.object(F.shutil, "which", side_effect=which):
            self.assertFalse(F.ffmpeg_co())

    def test_trang_thai_du_truong(self):
        with mock.patch.object(F, "ffmpeg_co", return_value=False):
            status = F.trang_thai()
        self.assertEqual(set(status), {"co", "dang_cai", "loi", "winget"})
        self.assertFalse(status["co"])
        self.assertFalse(status["dang_cai"])

    def test_cai_ghim_dung_ban_va_goi_winget(self):
        calls, steps = [], []
        with mock.patch.object(F, "ffmpeg_co", return_value=True), \
                mock.patch.object(F.shutil, "which", return_value="C:/winget.exe"):
            F.cai_ffmpeg(lambda stage, frac: steps.append(frac), chay=calls.append)
        args = calls[0]
        self.assertEqual(args[:2], ["install", "-e"])
        self.assertIn("Gyan.FFmpeg", args)
        self.assertEqual(F.FFMPEG["ban"], "8.0.1")
        self.assertEqual(args[args.index("--version") + 1], "8.0.1")
        self.assertEqual(steps[-1], 1.0)

    def test_cai_xong_ma_van_thieu_thi_bao_loi(self):
        with mock.patch.object(F, "ffmpeg_co", return_value=False), \
                mock.patch.object(F.shutil, "which", return_value="C:/winget.exe"):
            with self.assertRaises(RuntimeError):
                F.cai_ffmpeg(chay=lambda args: None)
        self.assertIn("chưa thấy ffmpeg", F.trang_thai()["loi"])
        self.assertFalse(F.trang_thai()["dang_cai"])

    def test_thieu_winget_thi_bao_tai_tay(self):
        with mock.patch.object(F.shutil, "which", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                F.cai_ffmpeg()
        self.assertIn("winget", str(ctx.exception))

    def test_winget_loi_thi_ghi_vao_trang_thai(self):
        def fail(args):
            raise RuntimeError("winget lỗi: test")
        with mock.patch.object(F.shutil, "which", return_value="C:/winget.exe"):
            with self.assertRaises(RuntimeError):
                F.cai_ffmpeg(chay=fail)
        self.assertIn("winget lỗi", F.trang_thai()["loi"])


class FeatureApiTest(unittest.TestCase):
    def setUp(self):
        import app_api
        self.tmp = tempfile.TemporaryDirectory()
        self.api = app_api.DownloaderAPI(Path(self.tmp.name))
        F._loi["text"] = None

    def tearDown(self):
        self.tmp.cleanup()

    def _wait_job(self):
        for _ in range(400):
            job = self.api.feature_job_status()
            if not job["running"]:
                return job
            time.sleep(0.01)
        self.fail("cài không kết thúc")

    def test_thieu_ffmpeg_thi_tu_choi_tai_va_bao_can_cai(self):
        with mock.patch.object(F, "ffmpeg_co", return_value=False):
            result = self.api.start_download({"url": "https://example.com/video"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["need"], "ffmpeg")

    def test_features_status_du_truong(self):
        with mock.patch.object(F, "ffmpeg_co", return_value=False):
            status = self.api.features_status()
        self.assertFalse(status["ffmpeg"]["co"])
        self.assertEqual(status["info"]["ten"], "ffmpeg")
        self.assertIn("8.0.1", status["info"]["nguon"])
        self.assertIn("MB", status["info"]["dung_luong"])

    def test_da_co_thi_khong_cai_lai(self):
        with mock.patch.object(F, "ffmpeg_co", return_value=True):
            self.assertFalse(self.api.install_ffmpeg()["ok"])

    def test_cai_xong_thi_job_bao_done(self):
        def fake_cai(tien):
            tien("Giả lập", 0.5)
        with mock.patch.object(F, "ffmpeg_co", return_value=False), \
                mock.patch.object(F, "cai_ffmpeg", side_effect=fake_cai):
            self.assertTrue(self.api.install_ffmpeg()["ok"])
            job = self._wait_job()
        self.assertTrue(job["done"])
        self.assertIsNone(job["error"])
        self.assertEqual(job["percent"], 100)

    def test_cai_loi_thi_job_bao_loi_va_app_van_chay(self):
        def fake_cai(tien):
            raise RuntimeError("winget lỗi: test")
        with mock.patch.object(F, "ffmpeg_co", return_value=False), \
                mock.patch.object(F, "cai_ffmpeg", side_effect=fake_cai):
            self.assertTrue(self.api.install_ffmpeg()["ok"])
            job = self._wait_job()
        self.assertFalse(job["done"])
        self.assertIn("winget lỗi", job["error"])
        with mock.patch.object(F, "ffmpeg_co", return_value=False):
            self.assertEqual(self.api.features_status()["ffmpeg"]["co"], False)  # vẫn trả lời


if __name__ == "__main__":
    unittest.main()
