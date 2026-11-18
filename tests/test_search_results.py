"""Search includes all upstream media tabs and reports only real heat data."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from drama_downloader import main, parse_search, search_display_heat


PAYLOAD = {
    "code": 0,
    "data": {
        "search_tabs": [
            {
                "tab_type": 1,
                "title": "综合",
                "data": [
                    {"book_data": [
                        {"movie_id": "film001", "title": {"text": "电影作品"},
                         "heat_score": 7890, "source": "search"},
                        {"film_id": "film002", "title": "第二部电影",
                         "series_play_cnt": 120000, "source": "search"},
                    ]},
                    {"video_data": [
                        {"series_id_str": "drama001", "series_title": "测试短剧",
                         "episode_cnt": 12,
                         "secondary_info_list": [
                             {"content": "多人二刷"},
                             {"content": "9320万热度", "line_idx": 1},
                         ]}
                    ]},
                ],
            },
            {
                "tab_type": 11,
                "title": "短剧",
                "data": [
                    {"video_data": [
                        {"series_id_str": "drama001", "series_title": "测试短剧",
                         "episode_cnt": 12},
                        {"series_id_str": "drama002", "series_title": "无热度短剧",
                         "source": "search"}
                    ]}
                ]
            },
        ],
    },
}


class SearchTests(unittest.TestCase):
    def test_movies_and_dramas_are_not_lost(self):
        results = parse_search(PAYLOAD)
        self.assertEqual({row["series_id"] for row in results},
                         {"film001", "film002", "drama001", "drama002"})
        drama = next(row for row in results if row["series_id"] == "drama001")
        self.assertEqual(drama["heat"], "9320万热度")
        movie = next(row for row in results if row["series_id"] == "film001")
        self.assertEqual(movie["heat"], "7890")
        self.assertEqual(movie["title"], "电影作品")

    def test_play_count_is_not_invented_as_heat(self):
        movie = next(x for x in parse_search(PAYLOAD) if x["series_id"] == "film002")
        self.assertEqual(movie["heat"], "")
        self.assertEqual(search_display_heat(movie), "热度：未返回")

    def test_heat_is_rendered_last_and_two_tabs_are_requested(self):
        with tempfile.TemporaryDirectory() as td:
            name = str(Path(td) / "pool.json")
            seen = []

            def search_request(_path, params):
                seen.append(params["tab_type"])
                if params["tab_type"] == "1":
                    return {"data": {"search_tabs": [PAYLOAD["data"]["search_tabs"][0]]}}
                return {"data": {"search_tabs": [PAYLOAD["data"]["search_tabs"][1]]}}

            with patch("drama_downloader.DirectPoolAdapter") as adapter:
                adapter.return_value.request.side_effect = search_request
                out = io.StringIO()
                with redirect_stdout(out):
                    status = main(["--pool", name, "search", "测试"])
            self.assertEqual(status, 0)
            self.assertEqual(seen, ["1", "11"])
            rendered = out.getvalue()
            self.assertIn("电影作品", rendered)
            self.assertIn("测试短剧", rendered)
            self.assertIn("9320万热度", rendered)
            for line in rendered.splitlines():
                if line.startswith(tuple(f"{i:02d}." for i in range(1, 10))):
                    self.assertIn("热度", line)
                    self.assertTrue(line.endswith(search_display_heat(
                        next(row for row in parse_search(PAYLOAD)
                             if str(row["series_id"]) in line))))

    def test_tab_override_keeps_original_single_tab(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("drama_downloader.DirectPoolAdapter") as adapter:
                adapter.return_value.request.return_value = {
                    "data": {"search_tabs": [PAYLOAD["data"]["search_tabs"][1]]}
                }
                with redirect_stdout(io.StringIO()):
                    status = main(["--pool", str(Path(td) / "devices.json"),
                                   "search", "剧名", "--tab", "11"])
            self.assertEqual(status, 0)
            self.assertEqual(adapter.return_value.request.call_count, 1)
            self.assertEqual(adapter.return_value.request.call_args.args[1]["tab_type"], "11")


if __name__ == "__main__":
    unittest.main()
