from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from video_downloader.service import VideoDownloader


@unittest.skipUnless(os.environ.get("MEINYA_RUN_NETWORK_TEST") == "1", "network smoke is opt-in")
class NetworkSmokeTests(unittest.TestCase):
    URL = "https://raw.githubusercontent.com/mediaelement/mediaelement-files/master/big_buck_bunny.mp4"

    def test_public_github_sample_downloads_and_verifies(self):
        with tempfile.TemporaryDirectory() as temp:
            service = VideoDownloader()
            media = service.inspect(self.URL)
            self.assertTrue(media.title)
            result = service.download({
                "url": self.URL,
                "output_dir": temp,
                "profile": "mp4-compatible",
                "quality_limit": 480,
                "write_metadata": True,
                "retries": 1,
            })
            self.assertEqual(result.status, "success", result.error)
            self.assertTrue(Path(result.artifacts[0].path).is_file())
            self.assertIn("h264", result.artifacts[0].codecs)

    def test_audio_profile_keeps_source_codec(self):
        with tempfile.TemporaryDirectory() as temp:
            result = VideoDownloader().download({
                "url": self.URL,
                "output_dir": temp,
                "profile": "audio",
                "retries": 1,
            })
            self.assertEqual(result.status, "success", result.error)
            self.assertNotIn("mp3", result.artifacts[0].codecs)
            self.assertEqual(result.artifacts[0].media_type, "audio")

    def test_audio_mp3_profile_extracts_and_verifies_mp3(self):
        with tempfile.TemporaryDirectory() as temp:
            result = VideoDownloader().download({
                "url": self.URL,
                "output_dir": temp,
                "profile": "audio-mp3",
                "write_metadata": True,
                "retries": 1,
            })
            self.assertEqual(result.status, "success", result.error)
            artifact = Path(result.artifacts[0].path)
            self.assertEqual(artifact.suffix.lower(), ".mp3")
            self.assertIn("mp3", result.artifacts[0].codecs)


if __name__ == "__main__":
    unittest.main()
