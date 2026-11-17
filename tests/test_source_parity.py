"""Check the CLI does not pre-filter accessible short-drama episodes or streams."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drama_direct import DevicePool, DirectError
from drama_downloader import Downloader, Episode, choose_variant


class SourceParityTests(unittest.TestCase):
    def test_single_stream_with_requested_quality(self):
        source = {"video_url": "https://video.example/stream.mp4", "key_hex": ""}
        self.assertEqual(choose_variant(source, "1080")["url"], source["video_url"])

    def test_unknown_quality_metadata_is_not_rejected(self):
        source = {"variants": [{"url": "https://video.example/stream.mp4", "quality_height": 0}]}
        self.assertEqual(choose_variant(source, "720")["url"], source["variants"][0]["url"])

    def test_device_pool_matches_source_twenty_slots(self):
        calls = []

        def new_device(*, mock_origin=None):
            calls.append(len(calls) + 1)
            return {"device_id": f"device-{calls[-1]}", "install_id": f"install-{calls[-1]}", "status": "active"}

        with tempfile.TemporaryDirectory() as temp:
            with patch("drama_direct.register_device", side_effect=new_device):
                pool = DevicePool(str(Path(temp) / "pool.json"), desired=20)
                self.assertEqual(pool.ensure(), 20)
                self.assertEqual(len(pool.status()), 20)
                with self.assertRaises(DirectError):
                    pool.ensure(21)
            reloaded = DevicePool(str(Path(temp) / "pool.json"), desired=20)
            self.assertEqual(len(reloaded.status()), 20)

    def test_marked_episode_not_skipped_client_side(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "video"
            folder.mkdir()
            seen = []

            def on_download(ep, _folder):
                seen.append(ep.number)
                return "完成"

            with patch.object(Downloader, "details", return_value=({"title": "测试"}, [
                Episode(1, "123", "第1集", disabled=True),
                Episode(2, "456", "第2集", disabled=False),
            ])), patch.object(Downloader, "save_metadata", return_value=folder), \
                    patch.object(Downloader, "one", side_effect=on_download):
                app = Downloader(None, Path(temp), 2, "best")
                self.assertEqual(app.download("abc", "all"), 0)
            self.assertEqual(set(seen), {1, 2})


if __name__ == "__main__":
    unittest.main()
