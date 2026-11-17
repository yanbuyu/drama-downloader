"""Short-drama episode identity must use vid_index, not novel-directory position."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drama_downloader import (
    DramaError, Downloader, Episode, parse_short_drama_episodes, parse_series_info,
)


def result(episodes, total=None):
    return {"code": 0, "data": {"7704180453418224702": {"video_data": {
        "series_id_str": "7704180453418224702",
        "series_title": "离婚报告夹进租房申请，他后悔疯了",
        "episode_cnt": total or len(episodes),
        "video_list": episodes,
    }}}}


class EpisodesTests(unittest.TestCase):
    def test_real_vid_index_not_array_position(self):
        raw = result([
            {"vid": "id119", "vid_index": 119, "title": "第119集"},
            {"vid": "id110", "vid_index": 110, "title": "第110集"},
        ], 109)
        parsed = parse_short_drama_episodes(raw, "7704180453418224702")
        self.assertEqual([x.number for x in parsed], [110, 119])
        self.assertEqual([x.id for x in parsed], ["id110", "id119"])

    def test_correct_episode_numbers_from_drama_list(self):
        raw = result([
            {"vid": "id002", "vid_index": 2, "title": "第2集"},
            {"vid": "id001", "vid_index": 1, "title": "第1集"},
        ])
        parsed = parse_short_drama_episodes(raw, "7704180453418224702")
        self.assertEqual([(x.number, x.id, x.title) for x in parsed],
                         [(1, "id001", "第1集"), (2, "id002", "第2集")])
        meta = parse_series_info(raw, "7704180453418224702")
        self.assertEqual(meta["title"], "离婚报告夹进租房申请，他后悔疯了")
        self.assertEqual(meta["episode_count"], 2)

    def test_missing_first_ten_does_not_download_110_to_119(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "剧"
            folder.mkdir()
            old = [Episode(i, f"vid{i}", f"第{i}集") for i in range(110, 219)]
            with patch.object(Downloader, 'details', return_value=({"title":"剧", "episode_count":109}, old)), \
                    patch.object(Downloader, 'save_metadata', return_value=folder), \
                    patch.object(Downloader, 'one') as runner:
                d = Downloader(None, Path(td), 8, "best")
                with self.assertRaisesRegex(DramaError, "缺少真实集号"):
                    d.download("7704180453418224702", "1-10")
                runner.assert_not_called()

    def test_old_misnumbered_file_is_preserved_in_backup(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            file = folder / "001-第110集[best].mp4"
            file.write_bytes(b"existing video")
            d = Downloader(None, folder, 1, "best")
            d.backup_mismatched_legacy_files(folder, {1})
            self.assertFalse(file.exists())
            self.assertEqual((folder / "_wrong_episode_backup" / file.name).read_bytes(), b"existing video")

    def test_duplicate_index_refused(self):
        raw = result([{"vid":"first","vid_index":1},{"vid":"other","vid_index":1}])
        with self.assertRaisesRegex(DramaError, "重复"):
            parse_short_drama_episodes(raw, "7704180453418224702")


if __name__ == '__main__':
    unittest.main()
