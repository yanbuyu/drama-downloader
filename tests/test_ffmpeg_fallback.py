"""测试 imageio-ffmpeg 自动发现，以及已完整缓存的检测。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from drama_downloader import find_ffmpeg, completed_video_cache

class CencTests(unittest.TestCase):
    def test_find_fallback(self):
        with patch("shutil.which", return_value=None):
            result = find_ffmpeg()
            self.assertIsNotNone(result, "pip install imageio-ffmpeg")
            self.assertTrue(Path(result).is_file())

    def test_reuse_only_verified_full_file(self):
        with tempfile.TemporaryDirectory() as td:
            part = Path(td) / "008.mp4.part"
            part.write_bytes(b"\x00\x00\x00\x18ftyp" + bytes(512))
            state = Path(str(part) + ".json")
            state.write_text(json.dumps({"url_hash":"old", "total":part.stat().st_size}))
            self.assertTrue(completed_video_cache(part))
            state.write_text(json.dumps({"url_hash":"old", "total":part.stat().st_size + 2}))
            self.assertFalse(completed_video_cache(part))

if __name__ == "__main__": unittest.main()
