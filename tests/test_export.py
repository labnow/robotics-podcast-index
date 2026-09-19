import csv
import tempfile
import unittest
from pathlib import Path

from robotcast.db import connect, install_seeds, upsert_episode
from robotcast.export import export_csv
from robotcast.intelligence import pending_batch, validate_and_import
from robotcast.site import SITE_EPISODE_LIMIT, build_site, validate_site
from robotcast.quality_policy import (HYBRID_VERSION, activate_quality_v2,
                                      quality_sql, rollback_quality,
                                      set_quality_override)


class ExportTests(unittest.TestCase):
    def test_exports_only_retained_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "test.db")
            install_seeds(conn, {"core": ["机器人"]})
            upsert_episode(conn, {"eid": "keep", "title": "机器\x00人", "duration": 120}, "机器人")
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
            self.assertEqual(rows[0]["title"], "机器人")
            self.assertEqual(rows[0]["duration_minutes"], "2.0")
            self.assertEqual(rows[0]["quality_evidence"], "legacy")
            self.assertIn("quality_score_10", rows[0])

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
            episodes = __import__("json").loads((output / payload["chunks"][0]).read_text())
            self.assertNotIn("description", episodes[0])
            self.assertEqual((payload["chunkSize"], len(payload["recentEpisodes"])), (50, 1))
            detail = __import__("json").loads((output / "data/details/episode-1.json").read_text())
            self.assertEqual(detail["description"], "深入讨论。")
            self.assertEqual(validate_site(output)["episodes"], 1)

    def test_site_upgrades_legacy_http_episode_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "test.db")
            install_seeds(conn, {"core": ["机器人"]})
            upsert_episode(conn, {"eid": "legacy-http", "title": "机器人访谈",
                                  "playCount": 10}, "机器人")
            conn.execute("""UPDATE episodes SET relevance_score=3,quality_score=3,
              url='http://example.com/episode' WHERE episode_id='legacy-http'""")
            conn.execute("""INSERT INTO episode_classifications
              (episode_id,content_hash,relevance_score,topics_json,
               suggested_keywords_json,reason,classifier,quality_score,quality_reason)
              VALUES('legacy-http','hash',3,'[]','[]','Relevant','test',3,'Good')""")
            conn.commit()
            output = Path(directory) / "site"
            build_site(conn, output)
            payload = __import__("json").loads((output / "data/index.json").read_text())
            episodes = __import__("json").loads((output / payload["chunks"][0]).read_text())
            self.assertEqual(episodes[0]["url"], "https://example.com/episode")

    def test_site_episode_limit_is_one_thousand(self):
        self.assertEqual(SITE_EPISODE_LIMIT, 1000)

    def test_quality_measure_switch_and_rollback_are_versioned(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "test.db")
            install_seeds(conn, {"core": ["机器人"]})
            upsert_episode(conn, {"eid": "episode", "title": "机器人访谈",
                                  "playCount": 10}, "机器人")
            upsert_episode(conn, {"eid": "fallback", "title": "机器人圆桌",
                                  "playCount": 10}, "机器人")
            batch = pending_batch(conn, 2)
            validate_and_import(conn, batch, {"classifications": [{
                "episode_id": "episode", "relevance": 3, "quality": 2,
                "topics": ["hardware"], "suggested_keywords": [],
                "reason": "Relevant.", "quality_reason": "Legacy.",
            }, {
                "episode_id": "fallback", "relevance": 3, "quality": 2,
                "topics": ["hardware"], "suggested_keywords": [],
                "reason": "Relevant.", "quality_reason": "Legacy fallback.",
            }]}, "test")
            set_quality_override(conn, "episode", 9, "Reviewed transcript.", "tester")
            conn.execute("INSERT INTO system_settings(key,value) VALUES"
                         "('primary_quality_measure','quality-v2-hybrid')")
            output = Path(directory) / "site"
            build_site(conn, output)
            payload = __import__("json").loads((output / "data/index.json").read_text())
            episodes = __import__("json").loads((output / payload["chunks"][0]).read_text())
            self.assertEqual((payload["qualityScale"], payload["qualityMeasure"],
                              episodes[0]["quality"]),
                             (10, HYBRID_VERSION, 9.0))
            fallback = next(item for item in episodes if item["id"] == "fallback")
            self.assertEqual((fallback["quality"], fallback["qualityEvidence"]),
                             (6.0, "legacy_fallback"))
            rollback_quality(conn)
            build_site(conn, output)
            payload = __import__("json").loads((output / "data/index.json").read_text())
            episodes = __import__("json").loads((output / payload["chunks"][0]).read_text())
            self.assertEqual((payload["qualityScale"], episodes[0]["quality"]),
                             (3, 2))
            with self.assertRaises(RuntimeError):
                activate_quality_v2(conn)

    def test_hybrid_policy_uses_ten_point_legacy_fallback(self):
        policy = quality_sql(HYBRID_VERSION)
        self.assertEqual((policy["scale"], policy["minimum"]), (10, 5))
        self.assertIn("legacy_fallback", policy["evidence"])
        self.assertIn("WHEN 3 THEN 9.0", policy["score"])
