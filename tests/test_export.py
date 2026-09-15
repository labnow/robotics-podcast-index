import csv
import tempfile
import unittest
from pathlib import Path

from robotcast.db import connect, install_seeds, upsert_episode
from robotcast.export import export_csv
from robotcast.intelligence import pending_batch, validate_and_import
from robotcast.site import build_site, validate_site


class ExportTests(unittest.TestCase):
    def test_exports_only_retained_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "test.db")
            install_seeds(conn, {"core": ["机器人"]})
            upsert_episode(conn, {"eid": "keep", "title": "机器人", "duration": 120}, "机器人")
            upsert_episode(conn, {"eid": "drop", "title": "普通节目"}, "机器人")
            batch = pending_batch(conn, 10)
            result = {"classifications": [
                {"episode_id": "keep", "relevance": 3, "quality": 3, "topics": ["hardware"], "suggested_keywords": [], "reason": "Robotics.", "quality_reason": "Substantive."},
                {"episode_id": "drop", "relevance": 0, "quality": 1, "topics": [], "suggested_keywords": [], "reason": "Unrelated.", "quality_reason": "Thin."},
            ]}
            validate_and_import(conn, batch, result, "test")
            output = Path(directory) / "episodes.csv"
            self.assertEqual(export_csv(conn, output), 1)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["episode_id"], "keep")
            self.assertEqual(rows[0]["duration_minutes"], "2.0")

    def test_builds_and_validates_static_site(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "test.db")
            install_seeds(conn, {"core": ["机器人"]})
            upsert_episode(conn, {
                "eid": "episode-1", "title": "具身智能访谈", "description": "<p>深入讨论。</p>",
                "duration": 3600, "playCount": 42, "commentCount": 3,
                "pubDate": "2026-09-10T00:00:00Z",
            }, "机器人")
            batch = pending_batch(conn, 10)
            validate_and_import(conn, batch, {"classifications": [{
                "episode_id": "episode-1", "relevance": 3, "quality": 3,
                "topics": ["humanoid"], "suggested_keywords": [],
                "reason": "Directly relevant.", "quality_reason": "Substantive.",
            }]}, "test")
            output = Path(directory) / "site-dist"
            report = build_site(conn, output)
            self.assertEqual(report["episodes"], 1)
            payload = __import__("json").loads((output / "data/index.json").read_text())
            self.assertNotIn("description", payload["episodes"][0])
            detail = __import__("json").loads((output / "data/details/episode-1.json").read_text())
            self.assertEqual(detail["description"], "深入讨论。")
            self.assertEqual(validate_site(output)["episodes"], 1)
