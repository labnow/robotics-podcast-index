from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from .quality import RUBRIC_VERSION
from .quality_policy import active_quality_measure, quality_sql

TAG = re.compile(r"<[^>]+>")
INVALID_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def export_csv(conn, path: Path, min_relevance: int = 2,
               min_quality: float | None = None,
               zero_play_grace_days: int | None = 0,
               description_max_chars: int | None = None) -> int:
    measure = active_quality_measure(conn)
    policy = quality_sql(measure)
    active_score, active_reason = policy["score"], policy["reason"]
    rows = conn.execute(f"""SELECT e.*,c.topics_json,c.reason,
      {active_score} AS active_quality,{policy['tier']} AS active_quality_tier,
      {active_reason} AS active_quality_reason,
      {policy['confidence']} AS active_quality_confidence,
      {policy['evidence']} AS active_quality_evidence,
      q.score_10 AS assessed_score_10,q.quality_tier AS assessed_quality_tier,
      q.confidence AS quality_confidence,q.transcript_used,q.rubric_version,
      o.score_10 AS override_score_10,
      (SELECT reason FROM classification_selections s WHERE s.episode_id=e.episode_id
       ORDER BY selected_at DESC LIMIT 1) AS selection_reason,
      e.age_popularity_percentile AS exported_age_popularity_percentile,
      group_concat(m.keyword,'; ') AS matched_keywords
      FROM episodes e JOIN episode_classifications c USING(episode_id)
      LEFT JOIN quality_assessments q ON q.episode_id=e.episode_id AND q.rubric_version=?
      LEFT JOIN quality_overrides o ON o.episode_id=e.episode_id AND o.rubric_version=?
      LEFT JOIN episode_matches m USING(episode_id)
      WHERE e.relevance_score>=? AND (? IS NULL OR {active_score}>=?)
        AND (? IS NULL OR e.play_count IS NULL OR e.play_count>0
          OR e.published_at IS NULL OR julianday(?) - julianday(e.published_at) <= ?)
      GROUP BY e.episode_id
      ORDER BY {active_score} DESC,e.published_at DESC,e.title""",
      (RUBRIC_VERSION, RUBRIC_VERSION, min_relevance, min_quality, min_quality, zero_play_grace_days,
       datetime.now(timezone.utc).isoformat(), zero_play_grace_days)).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["episode_id", "title", "podcast", "published_at", "duration_minutes",
              "play_count", "comment_count", "age_popularity_percentile",
              "relevance", "quality", "quality_score_10", "quality_tier",
              "quality_confidence", "quality_evidence", "quality_rubric_version",
              "topics", "matched_keywords", "selection_reason",
              "url", "classification_reason", "quality_reason", "description"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        # Some public metadata contains control/quote combinations that require
        # an explicit fallback escape even under the Excel dialect.
        writer = csv.DictWriter(stream, fieldnames=fields, escapechar="\\")
        writer.writeheader()
        for row in rows:
            description = unescape(TAG.sub(" ", row["description"] or ""))
            description = " ".join(description.split())
            if description_max_chars is not None and len(description) > description_max_chars:
                description = description[:description_max_chars].rstrip() + "…"
            record = {
                "episode_id": row["episode_id"], "title": row["title"],
                "podcast": row["podcast_name"], "published_at": row["published_at"],
                "duration_minutes": round(row["duration_seconds"] / 60, 1) if row["duration_seconds"] else None,
                "play_count": row["play_count"], "comment_count": row["comment_count"],
                "age_popularity_percentile": round(row["exported_age_popularity_percentile"] * 100, 1)
                if row["exported_age_popularity_percentile"] is not None else None,
                "relevance": row["relevance_score"],
                "quality": row["active_quality"],
                "quality_score_10": row["active_quality"] if policy["scale"] == 10 else None,
                "quality_tier": row["active_quality_tier"],
                "quality_confidence": row["active_quality_confidence"],
                "quality_evidence": row["active_quality_evidence"],
                "quality_rubric_version": measure,
                "topics": "; ".join(json.loads(row["topics_json"])),
                "matched_keywords": row["matched_keywords"], "url": row["url"],
                "selection_reason": row["selection_reason"],
                "classification_reason": row["reason"],
                "quality_reason": row["active_quality_reason"], "description": description,
            }
            writer.writerow({key: INVALID_CONTROL.sub("", value) if isinstance(value, str)
                             else value for key, value in record.items()})
    return len(rows)
