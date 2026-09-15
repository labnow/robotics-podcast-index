from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

TAG = re.compile(r"<[^>]+>")


def export_csv(conn, path: Path, min_relevance: int = 2,
               min_quality: int | None = None,
               zero_play_grace_days: int | None = 0,
               description_max_chars: int | None = None) -> int:
    rows = conn.execute("""SELECT e.*,c.topics_json,c.reason,c.quality_score AS classified_quality,
      c.quality_reason AS classified_quality_reason,
      (SELECT reason FROM classification_selections s WHERE s.episode_id=e.episode_id
       ORDER BY selected_at DESC LIMIT 1) AS selection_reason,
      e.age_popularity_percentile AS exported_age_popularity_percentile,
      group_concat(m.keyword,'; ') AS matched_keywords
      FROM episodes e JOIN episode_classifications c USING(episode_id)
      LEFT JOIN episode_matches m USING(episode_id)
      WHERE e.relevance_score>=? AND (? IS NULL OR c.quality_score>=?)
        AND (? IS NULL OR e.play_count IS NULL OR e.play_count>0
          OR e.published_at IS NULL OR julianday(?) - julianday(e.published_at) <= ?)
      GROUP BY e.episode_id
      ORDER BY c.quality_score DESC,e.published_at DESC,e.title""",
      (min_relevance, min_quality, min_quality, zero_play_grace_days,
       datetime.now(timezone.utc).isoformat(), zero_play_grace_days)).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["episode_id", "title", "podcast", "published_at", "duration_minutes",
              "play_count", "comment_count", "age_popularity_percentile",
              "relevance", "quality", "topics", "matched_keywords", "selection_reason",
              "url", "classification_reason", "quality_reason", "description"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            description = unescape(TAG.sub(" ", row["description"] or ""))
            description = " ".join(description.split())
            if description_max_chars is not None and len(description) > description_max_chars:
                description = description[:description_max_chars].rstrip() + "…"
            writer.writerow({
                "episode_id": row["episode_id"], "title": row["title"],
                "podcast": row["podcast_name"], "published_at": row["published_at"],
                "duration_minutes": round(row["duration_seconds"] / 60, 1) if row["duration_seconds"] else None,
                "play_count": row["play_count"], "comment_count": row["comment_count"],
                "age_popularity_percentile": round(row["exported_age_popularity_percentile"] * 100, 1)
                if row["exported_age_popularity_percentile"] is not None else None,
                "relevance": row["relevance_score"],
                "quality": row["classified_quality"],
                "topics": "; ".join(json.loads(row["topics_json"])),
                "matched_keywords": row["matched_keywords"], "url": row["url"],
                "selection_reason": row["selection_reason"],
                "classification_reason": row["reason"],
                "quality_reason": row["classified_quality_reason"], "description": description,
            })
    return len(rows)
