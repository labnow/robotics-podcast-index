from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS episodes (
  episode_id TEXT PRIMARY KEY,
  podcast_id TEXT,
  podcast_name TEXT,
  title TEXT NOT NULL,
  description TEXT,
  published_at TEXT,
  duration_seconds INTEGER,
  play_count INTEGER,
  comment_count INTEGER,
  url TEXT NOT NULL,
  relevance_score INTEGER CHECK (relevance_score BETWEEN 0 AND 3),
  age_popularity_percentile REAL,
  popularity_computed_at TEXT,
  raw_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS keywords (
  term TEXT PRIMARY KEY COLLATE NOCASE,
  category TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','candidate','rejected')),
  source TEXT NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS episode_matches (
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  keyword TEXT NOT NULL REFERENCES keywords(term),
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (episode_id, keyword)
);
CREATE TABLE IF NOT EXISTS collection_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  keywords_searched INTEGER NOT NULL DEFAULT 0,
  hits_seen INTEGER NOT NULL DEFAULT 0,
  new_episodes INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE TABLE IF NOT EXISTS episode_classifications (
  episode_id TEXT PRIMARY KEY REFERENCES episodes(episode_id) ON DELETE CASCADE,
  content_hash TEXT NOT NULL,
  relevance_score INTEGER NOT NULL CHECK(relevance_score BETWEEN 0 AND 3),
  topics_json TEXT NOT NULL,
  suggested_keywords_json TEXT NOT NULL,
  reason TEXT NOT NULL,
  classifier TEXT NOT NULL,
  classified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS keyword_shortlist (
  term TEXT PRIMARY KEY REFERENCES keywords(term) ON DELETE CASCADE,
  score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
  reason TEXT NOT NULL,
  curator TEXT NOT NULL,
  shortlisted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS keyword_probes (
  id INTEGER PRIMARY KEY,
  term TEXT NOT NULL REFERENCES keywords(term),
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  hits_seen INTEGER NOT NULL DEFAULT 0,
  new_episodes INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE TABLE IF NOT EXISTS episode_observations (
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  collection_run_id INTEGER REFERENCES collection_runs(id) ON DELETE CASCADE,
  observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  play_count INTEGER,
  comment_count INTEGER,
  PRIMARY KEY (episode_id, collection_run_id)
);
CREATE TABLE IF NOT EXISTS search_observations (
  collection_run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL REFERENCES keywords(term),
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  search_rank INTEGER NOT NULL,
  page_number INTEGER NOT NULL,
  collection_kind TEXT NOT NULL CHECK(collection_kind IN ('routine','backfill','probe')),
  observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (collection_run_id, keyword, episode_id)
);
CREATE TABLE IF NOT EXISTS keyword_collection_state (
  keyword TEXT PRIMARY KEY REFERENCES keywords(term) ON DELETE CASCADE,
  last_routine_at TEXT,
  last_backfill_at TEXT,
  deepest_page INTEGER NOT NULL DEFAULT 0,
  backfill_new_episodes INTEGER NOT NULL DEFAULT 0,
  backfill_hits INTEGER NOT NULL DEFAULT 0,
  consecutive_stale_pages INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS classification_selections (
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  selected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reason TEXT NOT NULL,
  priority_score REAL NOT NULL,
  age_popularity_percentile REAL,
  prefilter_reason TEXT,
  keyword_precision REAL,
  PRIMARY KEY (episode_id, selected_at)
);
CREATE TABLE IF NOT EXISTS quality_assessments (
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  rubric_version TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  score_10 REAL NOT NULL CHECK(score_10 BETWEEN 0 AND 10),
  quality_tier INTEGER NOT NULL CHECK(quality_tier BETWEEN 0 AND 3),
  dimensions_json TEXT NOT NULL,
  flags_json TEXT NOT NULL,
  confidence INTEGER NOT NULL CHECK(confidence BETWEEN 0 AND 2),
  reason TEXT NOT NULL,
  classifier TEXT NOT NULL,
  transcript_used INTEGER NOT NULL DEFAULT 0 CHECK(transcript_used IN (0,1)),
  assessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (episode_id, rubric_version)
);
CREATE TABLE IF NOT EXISTS quality_calibration_selections (
  sample_name TEXT NOT NULL,
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  stratum TEXT NOT NULL,
  selected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(sample_name,episode_id)
);
CREATE TABLE IF NOT EXISTS quality_assessment_runs (
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  rubric_version TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  score_10 REAL NOT NULL CHECK(score_10 BETWEEN 0 AND 10),
  quality_tier INTEGER NOT NULL CHECK(quality_tier BETWEEN 0 AND 3),
  dimensions_json TEXT NOT NULL,
  flags_json TEXT NOT NULL,
  confidence INTEGER NOT NULL CHECK(confidence BETWEEN 0 AND 2),
  reason TEXT NOT NULL,
  classifier TEXT NOT NULL,
  transcript_used INTEGER NOT NULL CHECK(transcript_used IN (0,1)),
  assessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(episode_id,rubric_version,content_hash)
);
CREATE TABLE IF NOT EXISTS system_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS quality_overrides (
  episode_id TEXT PRIMARY KEY REFERENCES episodes(episode_id) ON DELETE CASCADE,
  rubric_version TEXT NOT NULL,
  score_10 REAL NOT NULL CHECK(score_10 BETWEEN 0 AND 10),
  quality_tier INTEGER NOT NULL CHECK(quality_tier BETWEEN 0 AND 3),
  reason TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS episode_transcripts (
  episode_id TEXT PRIMARY KEY REFERENCES episodes(episode_id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK(status IN ('available','no_subtitle','error')),
  media_id TEXT,
  transcript_path TEXT,
  content_hash TEXT,
  segment_count INTEGER,
  char_count INTEGER,
  fetched_at TEXT,
  last_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  error TEXT
  ,transcription_seconds REAL
  ,audio_duration_seconds INTEGER
  ,realtime_factor REAL
  ,model_name TEXT
  ,device TEXT
);
CREATE TABLE IF NOT EXISTS transcription_claims (
  episode_id TEXT PRIMARY KEY REFERENCES episodes(episode_id) ON DELETE CASCADE,
  worker_id TEXT NOT NULL,
  claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  lease_until TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode_sources (
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  source_episode_id TEXT NOT NULL,
  source_url TEXT,
  audio_url TEXT,
  feed_url TEXT,
  rss_guid TEXT,
  transcript_url TEXT,
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(source,source_episode_id)
);
CREATE TABLE IF NOT EXISTS podcast_registry (
  podcast_key TEXT PRIMARY KEY,
  podcast_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  apple_collection_id TEXT,
  apple_url TEXT,
  feed_url TEXT,
  match_status TEXT NOT NULL DEFAULT 'pending' CHECK(match_status IN ('pending','matched','ambiguous','not_found','error')),
  match_score REAL,
  candidates_json TEXT NOT NULL DEFAULT '[]',
  last_attempt_at TEXT,
  error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS feed_aliases (
  alias_url TEXT PRIMARY KEY,
  canonical_url TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS match_review_queue (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_episode_id TEXT NOT NULL,
  proposed_episode_json TEXT NOT NULL,
  candidates_json TEXT NOT NULL,
  method TEXT NOT NULL,
  score REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TEXT,
  UNIQUE(source,source_episode_id,status)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive migrations to databases created by earlier releases."""
    episode_columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
    for name, definition in (
        ("quality_score", "INTEGER CHECK (quality_score BETWEEN 0 AND 3)"),
        ("quality_reason", "TEXT"),
        ("quality_score_10", "REAL CHECK (quality_score_10 BETWEEN 0 AND 10)"),
        ("quality_confidence", "INTEGER CHECK (quality_confidence BETWEEN 0 AND 2)"),
        ("quality_rubric_version", "TEXT"),
        ("age_popularity_percentile", "REAL"),
        ("popularity_computed_at", "TEXT"),
    ):
        if name not in episode_columns:
            conn.execute(f"ALTER TABLE episodes ADD COLUMN {name} {definition}")
    classification_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(episode_classifications)")
    }
    for name, definition in (
        ("quality_score", "INTEGER CHECK (quality_score BETWEEN 0 AND 3)"),
        ("quality_reason", "TEXT"),
    ):
        if name not in classification_columns:
            conn.execute(
                f"ALTER TABLE episode_classifications ADD COLUMN {name} {definition}"
            )
    run_columns = {row[1] for row in conn.execute("PRAGMA table_info(collection_runs)")}
    for name, definition in (
        ("kind", "TEXT NOT NULL DEFAULT 'routine'"),
        ("updated_episodes", "INTEGER NOT NULL DEFAULT 0"),
        ("new_matches", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in run_columns:
            conn.execute(f"ALTER TABLE collection_runs ADD COLUMN {name} {definition}")
    selection_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(classification_selections)")
    }
    for name, definition in (("prefilter_reason", "TEXT"),
                             ("keyword_precision", "REAL")):
        if name not in selection_columns:
            conn.execute(f"ALTER TABLE classification_selections ADD COLUMN {name} {definition}")
    source_columns = {row[1] for row in conn.execute("PRAGMA table_info(episode_sources)")}
    if "transcript_url" not in source_columns:
        conn.execute("ALTER TABLE episode_sources ADD COLUMN transcript_url TEXT")
    for name in ("normalized_audio_url",):
        if name not in source_columns:
            conn.execute(f"ALTER TABLE episode_sources ADD COLUMN {name} TEXT")
    for name in ("normalized_title", "normalized_podcast_name"):
        if name not in episode_columns:
            conn.execute(f"ALTER TABLE episodes ADD COLUMN {name} TEXT")
    transcript_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(episode_transcripts)")
    }
    for name, definition in (
        ("transcription_seconds", "REAL"),
        ("audio_duration_seconds", "INTEGER"),
        ("realtime_factor", "REAL"),
        ("model_name", "TEXT"),
        ("device", "TEXT"),
    ):
        if name not in transcript_columns:
            conn.execute(f"ALTER TABLE episode_transcripts ADD COLUMN {name} {definition}")
    from .matching import normalize_title, normalize_url
    for row in conn.execute("SELECT episode_id,title,podcast_name FROM episodes WHERE normalized_title IS NULL"):
        conn.execute("UPDATE episodes SET normalized_title=?,normalized_podcast_name=? WHERE episode_id=?",
                     (normalize_title(row["title"]), normalize_title(row["podcast_name"]), row["episode_id"]))
    for row in conn.execute("SELECT rowid,audio_url FROM episode_sources WHERE audio_url IS NOT NULL AND normalized_audio_url IS NULL"):
        conn.execute("UPDATE episode_sources SET normalized_audio_url=? WHERE rowid=?",
                     (normalize_url(row["audio_url"]), row["rowid"]))
    conn.execute("CREATE INDEX IF NOT EXISTS episodes_normalized_match ON episodes(normalized_title,normalized_podcast_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS sources_normalized_audio ON episode_sources(normalized_audio_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS sources_episode ON episode_sources(episode_id,source)")
    conn.execute("""INSERT OR IGNORE INTO quality_assessment_runs
      (episode_id,rubric_version,content_hash,score_10,quality_tier,dimensions_json,
       flags_json,confidence,reason,classifier,transcript_used,assessed_at)
      SELECT episode_id,rubric_version,content_hash,score_10,quality_tier,
       dimensions_json,flags_json,confidence,reason,classifier,transcript_used,assessed_at
      FROM quality_assessments""")
    conn.execute("""INSERT OR IGNORE INTO episode_sources
      (episode_id,source,source_episode_id,source_url,audio_url)
      SELECT episode_id,'xiaoyuzhou',episode_id,url,
        json_extract(raw_json,'$.enclosure.url') FROM episodes
      WHERE episode_id NOT LIKE 'apple_%' AND episode_id NOT LIKE 'rss_%'""")
    conn.commit()


def install_seeds(conn: sqlite3.Connection, groups: dict[str, list[str]]) -> int:
    before = conn.total_changes
    for category, terms in groups.items():
        conn.executemany(
            "INSERT OR IGNORE INTO keywords(term,category,status,source) VALUES(?,?,'active','seed')",
            ((term, category) for term in terms),
        )
    conn.commit()
    return conn.total_changes - before


def upsert_episode(conn: sqlite3.Connection, ep: dict, keyword: str | None,
                   collection_run_id: int | None = None, search_rank: int | None = None,
                   page_number: int | None = None,
                   collection_kind: str = "routine") -> bool:
    eid = ep.get("eid") or ep.get("episodeId") or ep.get("id")
    if not eid:
        return False
    media = ep.get("media") or {}
    audio_url = ep.get("audioUrl") or (media.get("source") or {}).get("url") or (ep.get("enclosure") or {}).get("url")
    rss_guid = ep.get("rssGuid")
    podcast = ep.get("podcast") or {}
    title = ep.get("title") or "(untitled)"
    published = ep.get("pubDate") or ep.get("publishedAt")
    from .matching import find_episode_match, normalize_title, normalize_url
    match = find_episode_match(conn, ep)
    if match.episode_id:
        eid = match.episode_id
    elif match.status == "ambiguous":
        conn.execute("""INSERT OR IGNORE INTO match_review_queue
          (source,source_episode_id,proposed_episode_json,candidates_json,method,score)
          VALUES(?,?,?,?,?,?)""", (ep.get("source") or "xiaoyuzhou",
          str(ep.get("sourceEpisodeId") or eid), json.dumps(ep, ensure_ascii=False),
          json.dumps(match.candidates), match.method, match.score))
    existed = conn.execute("SELECT 1 FROM episodes WHERE episode_id=?", (eid,)).fetchone() is not None
    description = ep.get("shownotes") or ep.get("description") or ep.get("brief") or ""
    conn.execute(
        """INSERT INTO episodes(episode_id,podcast_id,podcast_name,title,description,published_at,
        duration_seconds,play_count,comment_count,url,raw_json,normalized_title,
        normalized_podcast_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(episode_id) DO UPDATE SET podcast_id=excluded.podcast_id,
        podcast_name=excluded.podcast_name,title=excluded.title,description=excluded.description,
        published_at=COALESCE(excluded.published_at,episodes.published_at),
        duration_seconds=COALESCE(excluded.duration_seconds,episodes.duration_seconds),
        play_count=COALESCE(excluded.play_count,episodes.play_count),
        comment_count=COALESCE(excluded.comment_count,episodes.comment_count),
        normalized_title=excluded.normalized_title,
        normalized_podcast_name=excluded.normalized_podcast_name,
        raw_json=excluded.raw_json,updated_at=CURRENT_TIMESTAMP""",
        (eid, podcast.get("pid") or ep.get("pid"), podcast.get("title") or ep.get("podcastTitle"),
         title, description, published, ep.get("duration"),
         ep.get("playCount"), ep.get("commentCount"),
         ep.get("url") or f"https://www.xiaoyuzhoufm.com/episode/{eid}",
         json.dumps(ep, ensure_ascii=False), normalize_title(title),
         normalize_title(podcast.get("title") or ep.get("podcastTitle"))),
    )
    source = ep.get("source") or "xiaoyuzhou"
    source_id = str(ep.get("sourceEpisodeId") or eid)
    conn.execute("""INSERT INTO episode_sources
      (episode_id,source,source_episode_id,source_url,audio_url,feed_url,rss_guid,transcript_url,normalized_audio_url)
      VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_episode_id) DO UPDATE SET
      source_url=excluded.source_url,audio_url=excluded.audio_url,feed_url=excluded.feed_url,
      rss_guid=excluded.rss_guid,transcript_url=excluded.transcript_url,
      normalized_audio_url=excluded.normalized_audio_url,updated_at=CURRENT_TIMESTAMP""",
      (eid, source, source_id, ep.get("url"), audio_url, ep.get("feedUrl"), ep.get("rssGuid"), ep.get("transcriptUrl"), normalize_url(audio_url)))
    if keyword is not None:
        conn.execute("INSERT OR IGNORE INTO episode_matches(episode_id,keyword) VALUES(?,?)", (eid, keyword))
    if collection_run_id is not None:
        conn.execute("""INSERT INTO episode_observations
          (episode_id,collection_run_id,play_count,comment_count) VALUES(?,?,?,?)
          ON CONFLICT(episode_id,collection_run_id) DO UPDATE SET
          play_count=excluded.play_count,comment_count=excluded.comment_count""",
          (eid, collection_run_id, ep.get("playCount"), ep.get("commentCount")))
        if search_rank is not None and page_number is not None:
            conn.execute("""INSERT OR REPLACE INTO search_observations
              (collection_run_id,keyword,episode_id,search_rank,page_number,collection_kind)
              VALUES(?,?,?,?,?,?)""",
              (collection_run_id, keyword, eid, search_rank, page_number, collection_kind))
    return not existed


def active_keywords(conn: sqlite3.Connection) -> Iterable[str]:
    for row in conn.execute("SELECT term FROM keywords WHERE status='active' ORDER BY term COLLATE NOCASE"):
        yield row[0]
