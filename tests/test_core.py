import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest import mock

from robotcast.api import ApplePodcastClient, PublicClient, PublicXiaoyuzhouClient
from robotcast.collection import collect_terms
from robotcast.db import connect, install_seeds, upsert_episode
from robotcast.evolve import propose
from robotcast.eligibility import transcription_candidates
from robotcast.intelligence import pending_batch, validate_and_import
from robotcast.quality import (RUBRIC_VERSION, calculate_quality,
                               calibration_report, ensure_calibration_sample,
                               pending_quality_batch,
                               validate_and_import as import_quality)
from robotcast.transcripts import fetch_public_transcripts
from robotcast.matching import duration_close, normalize_title
from robotcast.registry import (build_historical_registry,
                                recover_registry_from_episodes,
                                registry_coverage_report,
                                resolve_ambiguous_registry)
from robotcast.recall import apple_recall_report


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

    def test_public_xiaoyuzhou_page_parses_without_credentials(self):
        client = PublicXiaoyuzhouClient(request_interval=0)
        payload = {"props": {"pageProps": {"episode": {"eid": "public", "title": "公开单集"}}}}
        client.get_bytes = lambda url: (
            '<script id="__NEXT_DATA__" type="application/json">' +
            __import__("json").dumps(payload, ensure_ascii=False) + '</script>').encode()
        episode = client.get_episode("public")
        self.assertEqual((episode["eid"], episode["source"]), ("public", "xiaoyuzhou"))

    def test_cross_source_title_date_deduplication(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        first = {"eid": "xyz", "title": "同一期", "pubDate": "2026-09-01T00:00:00Z",
                 "podcast": {"title": "Tech"}, "playCount": 99}
        second = {"eid": "apple_1", "source": "apple", "sourceEpisodeId": "1",
                  "title": "同一期", "pubDate": "2026-09-01T08:00:00Z",
                  "podcast": {"title": "Tech"}, "audioUrl": "https://audio/1.mp3"}
        self.assertTrue(upsert_episode(self.conn, first, "机器人"))
        self.assertFalse(upsert_episode(self.conn, second, "机器人"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM episodes").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT play_count FROM episodes").fetchone()[0], 99)
        sources = self.conn.execute("SELECT source FROM episode_sources ORDER BY source").fetchall()
        self.assertEqual([row[0] for row in sources], ["apple", "xiaoyuzhou"])

    def test_cross_source_normalized_title_and_duration_matching(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        first = {"eid": "legacy", "title": "EP. 42：机器人，如何学习？",
                 "pubDate": "2026-09-01T00:00:00Z", "duration": 3600,
                 "podcast": {"title": "Tech Talk"}}
        second = {"eid": "apple_42", "source": "apple", "sourceEpisodeId": "42",
                  "title": "机器人如何学习", "pubDate": "2026-09-01T12:00:00Z",
                  "duration": 3670, "podcast": {"title": "Ｔｅｃｈ　Ｔａｌｋ"}}
        self.assertTrue(upsert_episode(self.conn, first, "机器人"))
        self.assertFalse(upsert_episode(self.conn, second, "机器人"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM episodes").fetchone()[0], 1)
        self.assertEqual(normalize_title("EP. 42：机器人，如何学习？"), "机器人如何学习")
        self.assertTrue(duration_close(3600, 3670))

    def test_ambiguous_normalized_match_enters_review_queue(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        upsert_episode(self.conn, {"eid": "one", "title": "机器人访谈",
            "pubDate": "2026-09-01T00:00:00Z", "duration": 1800,
            "podcast": {"title": "同名播客"}}, "机器人")
        self.conn.execute("""INSERT INTO episodes
          (episode_id,podcast_name,title,published_at,duration_seconds,url,raw_json,
           normalized_title,normalized_podcast_name)
          VALUES('two','同名播客','机器人访谈','2026-09-01T00:00:00Z',1800,'https://two','{}',
                 '机器人访谈','同名播客')""")
        upsert_episode(self.conn, {"eid": "apple_x", "source": "apple",
            "sourceEpisodeId": "x", "title": "机器人访谈",
            "pubDate": "2026-09-01T01:00:00Z", "duration": 1801,
            "podcast": {"title": "同名播客"}}, "机器人")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM match_review_queue WHERE status='pending'").fetchone()[0], 1)

    def test_rss_guid_matching_precedes_reused_audio(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        shared = "https://audio/shared.mp3"
        upsert_episode(self.conn, {"eid": "canonical-a", "title": "同一音频",
            "audioUrl": shared, "podcast": {"title": "日报"}}, "机器人")
        self.conn.execute("""INSERT INTO episodes
          (episode_id,podcast_name,title,url,raw_json,normalized_title,
           normalized_podcast_name) VALUES
          ('canonical-b','日报','同一音频','https://canonical-b','{}','同一音频','日报')""")
        incoming = {"eid": "rss-new", "source": "rss",
            "sourceEpisodeId": "canonical-b", "rssGuid": "canonical-b",
            "title": "同一音频", "audioUrl": shared,
            "podcast": {"title": "日报"}}
        self.assertFalse(upsert_episode(self.conn, incoming, "机器人"))
        source = self.conn.execute("""SELECT episode_id FROM episode_sources
          WHERE source='rss' AND source_episode_id='canonical-b'""").fetchone()
        self.assertEqual(source[0], "canonical-b")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM match_review_queue").fetchone()[0], 0)

    def test_historical_registry_is_resumable(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        upsert_episode(self.conn, {"eid": "legacy", "title": "机器人访谈",
            "podcast": {"pid": "show1", "title": "机器人电台"}}, "机器人")
        self.conn.execute("UPDATE episodes SET relevance_score=3")
        class Client:
            def search_podcasts(self, name, limit):
                return [{"collectionId": 7, "collectionName": "机器人电台",
                         "feedUrl": "http://example.com/feed.xml",
                         "collectionViewUrl": "https://apple/show/7"}]
        first = build_historical_registry(self.conn, Client(), 10)
        second = build_historical_registry(self.conn, Client(), 10)
        self.assertEqual((first["matched"], second["attempted"]), (1, 0))
        row = self.conn.execute("SELECT match_status,feed_url FROM podcast_registry").fetchone()
        self.assertEqual(tuple(row), ("matched", "https://example.com/feed.xml"))

    def test_registry_collapses_duplicate_apple_listings_for_one_feed(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        upsert_episode(self.conn, {"eid": "legacy", "title": "机器人访谈",
            "podcast": {"title": "机器人电台"}}, "机器人")
        self.conn.execute("UPDATE episodes SET relevance_score=3")
        client = type("Search", (), {"search_podcasts": lambda self, name, limit: [
            {"collectionId": 7, "collectionName": "机器人电台",
             "feedUrl": "http://example.com/feed.xml"},
            {"collectionId": 8, "collectionName": "机器人电台",
             "feedUrl": "https://example.com/feed.xml"},
        ]})()
        report = build_historical_registry(self.conn, client, 1)
        self.assertEqual((report["matched"], report["ambiguous"]), (1, 0))

    def test_ambiguous_registry_resolves_from_episode_evidence(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        for eid, title in (("a", "机器人的触觉"), ("b", "灵巧手训练")):
            upsert_episode(self.conn, {"eid": eid, "title": title,
                "podcast": {"pid": "show", "title": "DeepTalk"}}, "机器人")
            self.conn.execute("UPDATE episodes SET relevance_score=3 WHERE episode_id=?", (eid,))
        build_historical_registry(self.conn, type("Search", (), {
            "search_podcasts": lambda self, name, limit: [
                {"collectionId": 1, "collectionName": "Deep Talk", "feedUrl": "https://wrong"},
                {"collectionId": 2, "collectionName": "deep talk", "feedUrl": "https://right"}],
        })(), 1)
        class Feeds:
            def get_bytes(self, url):
                titles = ["别的节目"] if url.endswith("wrong") else ["机器人的触觉", "灵巧手训练"]
                items = "".join(f"<item><title>{title}</title></item>" for title in titles)
                return f"<rss><channel>{items}</channel></rss>".encode()
        report = resolve_ambiguous_registry(self.conn, Feeds(), 1)
        self.assertEqual(report["resolved"], 1)
        row = self.conn.execute("SELECT match_status,apple_collection_id,feed_url FROM podcast_registry").fetchone()
        self.assertEqual(tuple(row), ("matched", "2", "https://right/"))

    def test_registry_accepts_strongly_evidenced_feed_mirrors(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        for eid, title in (("a", "机器人的触觉"), ("b", "灵巧手训练")):
            upsert_episode(self.conn, {"eid": eid, "title": title,
                "podcast": {"title": "DeepTalk"}}, "机器人")
            self.conn.execute("UPDATE episodes SET relevance_score=3 WHERE episode_id=?", (eid,))
        build_historical_registry(self.conn, type("Search", (), {
            "search_podcasts": lambda self, name, limit: [
                {"collectionId": 1, "collectionName": "Deep Talk",
                 "feedUrl": "https://mirror.example/feed"},
                {"collectionId": 2, "collectionName": "deep talk",
                 "feedUrl": "https://feed.xyzfm.space/canonical"}],
        })(), 1)
        class Feeds:
            def get_bytes(self, url):
                return ("<rss><channel><item><title>机器人的触觉</title></item>"
                        "<item><title>灵巧手训练</title></item></channel></rss>").encode()
        self.assertEqual(resolve_ambiguous_registry(self.conn, Feeds(), 1)["resolved"], 1)
        row = self.conn.execute("SELECT match_status,feed_url FROM podcast_registry").fetchone()
        self.assertEqual(tuple(row), ("matched", "https://feed.xyzfm.space/canonical"))
        aliases = self.conn.execute("""SELECT count(*) FROM feed_aliases
          WHERE canonical_url='https://feed.xyzfm.space/canonical'""").fetchone()[0]
        self.assertEqual(aliases, 2)

    def test_registry_recovers_name_miss_from_episode_titles(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        for eid, title in (("a", "机器人的触觉原理"), ("b", "灵巧手训练方法")):
            upsert_episode(self.conn, {"eid": eid, "title": title,
                "podcast": {"title": "DeepTalk"}}, "机器人")
            self.conn.execute("UPDATE episodes SET relevance_score=3 WHERE episode_id=?", (eid,))
        build_historical_registry(self.conn, type("Shows", (), {
            "search_podcasts": lambda self, name, limit: []})(), 1)
        class Episodes:
            def search_pages(self, title, max_pages, page_size):
                yield [{"title": title, "feedUrl": "https://feed.example/rss",
                    "podcast": {"pid": "7", "title": "Deep Talk"}}]
        report = recover_registry_from_episodes(self.conn, Episodes(), 1, 2)
        self.assertEqual((report["matched"], report["unresolved"]), (1, 0))
        row = self.conn.execute("SELECT match_status,feed_url FROM podcast_registry").fetchone()
        self.assertEqual(tuple(row), ("matched", "https://feed.example/rss"))

    def test_registry_coverage_report(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        upsert_episode(self.conn, {"eid": "legacy", "title": "机器人访谈",
            "podcast": {"pid": "show", "title": "机器人电台"}}, "机器人")
        self.conn.execute("UPDATE episodes SET relevance_score=3")
        build_historical_registry(self.conn, type("Search", (), {
            "search_podcasts": lambda self, name, limit: [{
                "collectionId": 7, "collectionName": "机器人电台",
                "feedUrl": "https://example.com/feed.xml"}],
        })(), 1)
        self.conn.execute("""INSERT INTO episode_sources
          (episode_id,source,source_episode_id)
          VALUES('legacy','rss','rss-legacy')""")
        report = registry_coverage_report(self.conn)
        self.assertEqual(report["matched_shows"], 1)
        self.assertEqual(report["show_coverage_percent"], 100.0)
        self.assertEqual(report["episode_identity_coverage_percent"], 100.0)

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

    def test_empirical_prefilter_defers_weak_lane_but_keeps_exploration(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        for number in range(20):
            eid = f"negative-{number}"
            upsert_episode(self.conn, {"eid": eid, "title": eid,
                                       "podcast": {"title": "broad"}}, "机器人")
            self.conn.execute("UPDATE episodes SET relevance_score=0 WHERE episode_id=?",
                              (eid,))
        deferred = next(f"deferred-{n}" for n in range(100)
            if int(hashlib.sha256(f"deferred-{n}".encode()).hexdigest()[:8], 16) % 10)
        explore = next(f"explore-{n}" for n in range(100)
            if int(hashlib.sha256(f"explore-{n}".encode()).hexdigest()[:8], 16) % 10 == 0)
        for eid in (deferred, explore):
            upsert_episode(self.conn, {"eid": eid, "title": eid,
                                       "podcast": {"title": "new show"}}, "机器人")
        batch = pending_batch(self.conn, 100)
        selected = {item["episode_id"] for item in batch}
        self.assertNotIn(deferred, selected)
        self.assertIn(explore, selected)
        self.assertEqual(next(item for item in batch if item["episode_id"] == explore)
                         ["prefilter_reason"], "exploration")

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

    def test_apple_recall_requires_complete_active_keyword_replay(self):
        install_seeds(self.conn, {"core": ["机器人", "具身智能"]})
        self.assertFalse(apple_recall_report(self.conn)["complete_replay"])
        for eid in ("known", "other"):
            upsert_episode(self.conn, {"eid": eid, "title": eid}, "机器人")
        self.conn.execute("UPDATE episodes SET relevance_score=3 WHERE episode_id='known'")
        run = self.conn.execute("""INSERT INTO collection_runs
          (kind,keywords_searched,hits_seen,new_episodes,finished_at)
          VALUES('routine',2,2,1,CURRENT_TIMESTAMP)""").lastrowid
        self.conn.execute("""INSERT INTO search_observations
          (collection_run_id,keyword,episode_id,search_rank,page_number,collection_kind)
          VALUES(?, '机器人','known',1,1,'routine')""", (run,))
        report = apple_recall_report(self.conn)
        self.assertEqual((report["known_relevant"], report["recovered_relevant"],
                          report["recall_percent"]), (1, 1, 100.0))

    def test_quality_score_is_deterministic_and_confidence_capped(self):
        dimensions = {name: 2 for name in
                      ("depth", "specificity", "expertise", "originality", "structure")}
        self.assertEqual(calculate_quality(dimensions, [], 2), (10.0, 3))
        self.assertEqual(calculate_quality(dimensions, ["automated_roundup"], 2), (7.0, 2))
        self.assertEqual(calculate_quality(dimensions, [], 0), (4.0, 1))

    def test_quality_calibration_sample_is_persisted_and_resumable(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        for number, tier in enumerate((1, 2, 3)):
            upsert_episode(self.conn, {"eid": f"q{tier}", "title": f"episode {number}",
                "playCount": 10}, "机器人")
            self.conn.execute("UPDATE episodes SET relevance_score=2,quality_score=? WHERE episode_id=?",
                              (tier, f"q{tier}"))
        first = ensure_calibration_sample(self.conn, "test")
        second = ensure_calibration_sample(self.conn, "test")
        self.assertEqual((first, second), (3, 3))
        self.assertEqual(calibration_report(self.conn, "test")["selected"], 3)

    def test_versioned_quality_assessment_import(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        upsert_episode(self.conn, {"eid": "quality", "title": "机器人技术访谈",
                                    "playCount": 10}, "机器人")
        self.conn.execute("""UPDATE episodes SET relevance_score=3,quality_score=3,
          quality_reason='legacy' WHERE episode_id='quality'""")
        self.assertEqual(pending_quality_batch(
            self.conn, 1, transcripts_only=True), [])
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
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM quality_assessment_runs").fetchone()[0], 1)
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

    def test_transcription_queue_prioritizes_low_confidence_then_relevance(self):
        install_seeds(self.conn, {"core": ["机器人"]})
        for eid in ("high", "uncertain"):
            upsert_episode(self.conn, {"eid": eid, "title": eid,
                "audioUrl": f"https://audio/{eid}.mp3"}, "机器人")
        self.conn.execute("""UPDATE episodes SET relevance_score=3,quality_score=3
          WHERE episode_id='high'""")
        self.conn.execute("""UPDATE episodes SET relevance_score=2,quality_score=2,
          quality_score_10=5,quality_confidence=0 WHERE episode_id='uncertain'""")
        rows = transcription_candidates(self.conn, 10)
        self.assertEqual([row["episode_id"] for row in rows], ["uncertain", "high"])
        self.assertEqual(rows[0]["reason"], "uncertain_q2_q3_boundary")
        manual = transcription_candidates(self.conn, 1, ["high"])
        self.assertEqual((manual[0]["priority"], manual[0]["reason"]),
                         (100, "manual_request"))


if __name__ == "__main__":
    unittest.main()
