"""Compact episode filenames and safe migration of old downloaded files."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drama_downloader import Downloader, Episode, episode_file_stem, safe_name

# Short enough to create fixture filenames on both Windows and Linux.
INTRO = "结婚三年，女主被丈夫冷落，决定带着孩子离开原来的家，重新开始生活，丈夫终于后悔了"


class FilenameTests(unittest.TestCase):
    def test_compact_name(self):
        self.assertEqual(episode_file_stem(1, "best"), "001-第1集[best]")
        self.assertEqual(episode_file_stem(109, "1080"), "109-第109集[1080]")

    def test_reuse_legacy_download_without_network(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "离婚报告夹进租房申请，他后悔疯了"
            folder.mkdir()
            episode = Episode(1, "video-001", INTRO)
            legacy = folder / (safe_name("001-" + INTRO) + "[best].mp4")
            legacy.write_bytes(b"downloaded movie")
            (folder / "series.json").write_text(json.dumps({
                "episodes": [{"number": 1, "id": "video-001", "title": INTRO}]
            }, ensure_ascii=False), encoding="utf8")
            info = {"title": folder.name, "episode_count": 1, "episodes": [vars(episode)]}
            with patch.object(Downloader, "details", return_value=(info, [episode])), \
                    patch("drama_downloader.verify_mp4") as verify, \
                    patch("drama_downloader.transfer") as transfer:
                self.assertEqual(Downloader(None, Path(root), 2, "best").download("series", "1"), 0)
                verify.assert_called_once()
                transfer.assert_not_called()
            self.assertFalse(legacy.exists())
            self.assertEqual((folder / "001-第1集[best].mp4").read_bytes(), b"downloaded movie")

    def test_wrong_video_id_never_renamed(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            legacy = folder / (safe_name("001-" + INTRO) + "[best].mp4")
            legacy.write_bytes(b"not necessarily episode 1")
            (folder / "series.json").write_text(json.dumps({
                "episodes": [{"number": 1, "id": "other-id", "title": INTRO}]
            }, ensure_ascii=False), encoding="utf8")
            Downloader(None, folder, 1, "best").migrate_legacy_filenames(
                folder, [Episode(1, "video-001", INTRO)])
            self.assertTrue(legacy.exists())
            self.assertFalse((folder / "001-第1集[best].mp4").exists())

    def test_partial_download_state_moves_together(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            old_stem = safe_name("008-" + INTRO) + "[720]"
            (folder / (old_stem + ".mp4.part")).write_bytes(b"partial")
            (folder / (old_stem + ".mp4.part.json")).write_text('{"total":123}')
            (folder / "series.json").write_text(json.dumps({
                "episodes": [{"number": 8, "id": "id008", "title": INTRO}]
            }, ensure_ascii=False), encoding="utf8")
            Downloader(None, folder, 1, "720").migrate_legacy_filenames(
                folder, [Episode(8, "id008", INTRO)])
            self.assertFalse((folder / (old_stem + ".mp4.part")).exists())
            self.assertEqual((folder / "008-第8集[720].mp4.part").read_bytes(), b"partial")
            self.assertEqual(
                (folder / "008-第8集[720].mp4.part.json").read_text(), '{"total":123}')

    def test_never_overwrite_new_file(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            old = folder / (safe_name("001-" + INTRO) + "[best].mp4")
            new = folder / "001-第1集[best].mp4"
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            (folder / "series.json").write_text(json.dumps({
                "episodes": [{"number": 1, "id": "id001", "title": INTRO}]
            }, ensure_ascii=False))
            Downloader(None, folder, 1, "best").migrate_legacy_filenames(
                folder, [Episode(1, "id001", INTRO)])
            self.assertEqual(old.read_bytes(), b"old")
            self.assertEqual(new.read_bytes(), b"new")


if __name__ == "__main__":
    unittest.main()
