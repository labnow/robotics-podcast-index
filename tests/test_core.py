import tempfile
import unittest
from pathlib import Path
from unittest import mock

from robotcast.api import ApplePodcastClient, PublicClient, PublicXiaoyuzhouClient
from robotcast.collection import collect_terms
from robotcast.db import connect, install_seeds, upsert_episode
from robotcast.evolve import propose
from robotcast.intelligence import pending_batch, validate_and_import
from robotcast.quality import (RUBRIC_VERSION, calculate_quality,
                               pending_quality_batch,
                               validate_and_import as import_quality)
from robotcast.transcripts import fetch_public_transcripts


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.temp.name) / "test.db")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_deduplicates_and_retains_keyword_provenance(self):
        install_seeds(self.conn, {"core": ["机器人", "具身智能"]})
        episode = {"eid": "e1", "title": "机器人学习", "podcast": {"pid": "p1", "title": "Tech"}}
        self.assertTrue(upsert_episode(self.conn, episode, "机器人"))
        self.assertFalse(upsert_episode(self.conn, episode, "具身智能"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM episodes").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM episode_matches").fetchone()[0], 2)

    def test_evolution_creates_review_candidates(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        for eid, title in [("e1", "端到端控制的新进展"), ("e2", "端到端控制与数据")]:
            upsert_episode(self.conn, {"eid": eid, "title": title}, "机器人")
            self.conn.execute("UPDATE episodes SET relevance_score=3 WHERE episode_id=?", (eid,))
        self.assertGreater(propose(self.conn, min_episodes=2), 0)
        row = self.conn.execute("SELECT status,source FROM keywords WHERE term='端到端控制'").fetchone()
        self.assertEqual(tuple(row), ("candidate", "corpus_phrase"))

    def test_apple_search_normalizes_authless_episode(self):
        client = ApplePodcastClient(request_interval=0)
        client.get_json = lambda url: {"results": [{"trackId": 42, "trackName": "机器人",
            "collectionId": 7, "collectionName": "Tech", "episodeUrl": "https://audio/x.mp3",
            "feedUrl": "https://feed/rss", "trackViewUrl": "https://apple/episode"}]}
        episode = list(client.search_pages("机器人"))[0][0]
        self.assertEqual((episode["eid"], episode["source"]), ("apple_42", "apple"))
        self.assertEqual(episode["audioUrl"], "https://audio/x.mp3")

    def test_classification_import_and_content_hash_cache(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        upsert_episode(self.conn, {"eid": "e1", "title": "具身数据闭环"}, "机器人")
        batch = pending_batch(self.conn, 10)
        result = {"classifications": [{
            "episode_id": "e1", "relevance": 3, "quality": 3, "topics": ["robot_data"],
            "suggested_keywords": ["数据闭环"], "reason": "The episode is about robot data.",
            "quality_reason": "Substantive technical discussion.",
        }]}
        self.assertEqual(validate_and_import(self.conn, batch, result, "test"), 1)
        self.assertEqual(pending_batch(self.conn, 10), [])
        self.assertEqual(self.conn.execute("SELECT relevance_score FROM episodes WHERE episode_id='e1'").fetchone()[0], 3)
        self.assertEqual(self.conn.execute("SELECT status FROM keywords WHERE term='数据闭环'").fetchone()[0], "candidate")

    def test_request_interval_is_enforced(self):
        client = PublicClient(request_interval=2)
        with mock.patch("robotcast.api.time.monotonic", side_effect=[10.0, 10.5, 12.0]), \
             mock.patch("robotcast.api.time.sleep") as sleep:
            client._wait_for_slot()
            client._wait_for_slot()
        sleep.assert_called_once_with(1.5)

    def test_pending_batch_does_not_require_keyword_overlap(self):
        install_seeds(self.conn, {"core": ["机器人", "具身智能", "VLA"]})
        for eid in ("one", "three"):
            upsert_episode(self.conn, {"eid": eid, "title": eid}, "机器人")
        upsert_episode(self.conn, {"eid": "three", "title": "three"}, "具身智能")
        upsert_episode(self.conn, {"eid": "three", "title": "three"}, "VLA")
        batch = pending_batch(self.conn, 10, min_matches=99)
        self.assertEqual({item["episode_id"] for item in batch}, {"one", "three"})

    def test_direct_episode_selection_becomes_recall_audit(self):
        install_seeds(self.conn, {"core": ["具身智能"]})
        upsert_episode(self.conn, {"eid": "miss", "title": "具身访谈"}, "具身智能")
        batch = pending_batch(self.conn, 10, episode_ids=["miss"])
        self.assertEqual(batch[0]["selection_reason"], "recall_audit")

    def test_collection_records_overlap_rank_and_engagement(self):
        install_seeds(self.conn, {"core": ["具身智能"]})

        class Client:
            def search_pages(self, term, max_pages, page_size):
                yield [{"eid": "e1", "title": "具身访谈", "playCount": 42,
                        "commentCount": 3}]

        first = collect_terms(self.conn, Client(), ["具身智能"], 2, 20)
        second = collect_terms(self.conn, Client(), ["具身智能"], 2, 20)
        self.assertEqual((first["new"], second["new"]), (1, 0))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM episodes").fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM episode_observations").fetchone()[0], 2)
        ranks = self.conn.execute(
            "SELECT search_rank FROM search_observations ORDER BY collection_run_id"
        ).fetchall()
        self.assertEqual([row[0] for row in ranks], [1, 1])

    def test_quality_score_is_deterministic_and_confidence_capped(self):
        dimensions = {name: 2 for name in
                      ("depth", "specificity", "expertise", "originality", "structure")}
        self.assertEqual(calculate_quality(dimensions, [], 2), (10.0, 3))
        self.assertEqual(calculate_quality(dimensions, ["automated_roundup"], 2), (7.0, 2))
        self.assertEqual(calculate_quality(dimensions, [], 0), (4.0, 1))

    def test_versioned_quality_assessment_import(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        upsert_episode(self.conn, {"eid": "quality", "title": "机器人技术访谈",
                                    "playCount": 10}, "机器人")
        self.conn.execute("""UPDATE episodes SET relevance_score=3,quality_score=3,
          quality_reason='legacy' WHERE episode_id='quality'""")
        batch = pending_quality_batch(self.conn, 1)
        result = {"assessments": [{
            "request_id": "E001",
            "dimensions": {"depth": 2, "specificity": 2, "expertise": 2,
                           "originality": 1, "structure": 2},
            "flags": [], "confidence": 2, "reason": "Concrete expert discussion.",
        }]}
        self.assertEqual(import_quality(self.conn, batch, result, "test"), 1)
        row = self.conn.execute("""SELECT score_10,quality_tier,rubric_version
          FROM quality_assessments WHERE episode_id='quality'""").fetchone()
        self.assertEqual(tuple(row), (9.0, 3, RUBRIC_VERSION))
        legacy = self.conn.execute("SELECT quality_score,quality_reason FROM episodes WHERE episode_id='quality'").fetchone()
        self.assertEqual(tuple(legacy), (3, "legacy"))
        self.assertEqual(pending_quality_batch(self.conn, 1), [])

    def test_transcript_fetch_is_cached_and_resumable(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        upsert_episode(self.conn, {"eid": "spoken", "title": "机器人访谈",
                                    "playCount": 20}, "机器人")
        self.conn.execute("UPDATE episodes SET relevance_score=3,quality_score=3 WHERE episode_id='spoken'")

        self.conn.execute("""INSERT INTO episode_sources
          (episode_id,source,source_episode_id,transcript_url)
          VALUES('spoken','rss','spoken','https://feed/transcript.vtt')""")
        class Client:
            def get_bytes(self, url):
                return b"WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nrobotics discussion\n"

        directory = Path(self.temp.name) / "transcripts"
        first = fetch_public_transcripts(self.conn, Client(), directory, max_episodes=1)
        second = fetch_public_transcripts(self.conn, Client(), directory, max_episodes=1)
        self.assertEqual((first["available"], second["attempted"]), (1, 0))
        row = self.conn.execute("SELECT transcript_path,status FROM episode_transcripts").fetchone()
        self.assertEqual(row["status"], "available")
        self.assertTrue(Path(row["transcript_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
