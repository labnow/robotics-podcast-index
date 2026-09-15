from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone


AGE_BUCKETS = (2, 7, 30, 90, 365, 1095)


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_days(value: str | None, now: datetime) -> float | None:
    published = _date(value)
    if published is None:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 86400)


def age_bucket(days: float | None) -> int:
    if days is None:
        return len(AGE_BUCKETS)
    for index, boundary in enumerate(AGE_BUCKETS):
        if days <= boundary:
            return index
    return len(AGE_BUCKETS)


def _percentiles(rows, now: datetime, by_show: bool = False) -> dict[str, float]:
    cohorts: dict[int, list[tuple[str, float]]] = {}
    for row in rows:
        # Auth-less Apple/RSS discovery has no engagement metric. Unknown is not
        # zero and must not enter Xiaoyuzhou popularity cohorts.
        if row["play_count"] is None:
            continue
        plays = max(0, row["play_count"] or 0)
        bucket = (row["podcast_name"], age_bucket(age_days(row["published_at"], now))) \
            if by_show else age_bucket(age_days(row["published_at"], now))
        cohorts.setdefault(bucket, []).append((row["episode_id"], math.log1p(plays)))
    result = {}
    for cohort in cohorts.values():
        ordered = sorted(cohort, key=lambda item: item[1])
        if len(ordered) == 1:
            result[ordered[0][0]] = 0.5
            continue
        denominator = len(ordered) - 1
        start = 0
        while start < len(ordered):
            end = start + 1
            while end < len(ordered) and ordered[end][1] == ordered[start][1]:
                end += 1
            percentile = ((start + end - 1) / 2) / denominator
            for episode_id, _ in ordered[start:end]:
                result[episode_id] = percentile
            start = end
    return result


def ranked_candidates(conn, now: datetime | None = None,
                      episode_ids: list[str] | None = None) -> list[dict]:
    """Rank LLM candidates without using keyword-overlap as a relevance proxy."""
    now = now or datetime.now(timezone.utc)
    all_rows = conn.execute("SELECT * FROM episodes").fetchall()
    popularity = _percentiles(all_rows, now)
    show_popularity = _percentiles(all_rows, now, by_show=True)
    show_stats = {
        row["podcast_name"]: row
        for row in conn.execute("""SELECT podcast_name,count(*) classified,
          sum(CASE WHEN relevance_score>=2 THEN 1 ELSE 0 END) relevant
          FROM episodes WHERE relevance_score IS NOT NULL AND podcast_name IS NOT NULL
          GROUP BY podcast_name""")
    }
    wanted = set(episode_ids or [])
    candidates = []
    for row in all_rows:
        if wanted and row["episode_id"] not in wanted:
            continue
        days = age_days(row["published_at"], now)
        pct = max(popularity.get(row["episode_id"], 0.5),
                  show_popularity.get(row["episode_id"], 0.5))
        stats = show_stats.get(row["podcast_name"])
        trusted = bool(stats and stats["classified"] >= 2 and
                       stats["relevant"] / stats["classified"] >= 0.6)
        exploration = int(hashlib.sha256(row["episode_id"].encode()).hexdigest()[:8], 16) % 10 == 0
        if wanted:
            reason, priority = "recall_audit", 200.0
        elif days is not None and days <= 2:
            reason, priority = "recent", 120.0 + pct
        elif pct >= 0.75:
            reason, priority = "popular_for_age", 100.0 + pct
        elif trusted:
            reason, priority = "trusted_show", 80.0 + pct
        elif exploration:
            reason, priority = "exploration", 60.0 + pct
        else:
            reason, priority = "backlog", pct
        candidates.append({
            "row": row, "selection_reason": reason,
            "priority_score": priority, "age_popularity_percentile": pct,
        })
    candidates.sort(key=lambda item: (
        item["priority_score"], item["row"]["published_at"] or ""
    ), reverse=True)
    return candidates


def refresh_popularity_scores(conn, now: datetime | None = None) -> int:
    """Persist a complete, reproducible popularity snapshot for every episode."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("SELECT * FROM episodes").fetchall()
    popularity = _percentiles(rows, now)
    show_popularity = _percentiles(rows, now, by_show=True)
    computed_at = now.isoformat()
    values = [
        (max(popularity.get(row["episode_id"], 0.5),
             show_popularity.get(row["episode_id"], 0.5)),
         computed_at, row["episode_id"])
        for row in rows
    ]
    conn.executemany("""UPDATE episodes
      SET age_popularity_percentile=?,popularity_computed_at=? WHERE episode_id=?""", values)
    conn.commit()
    return len(values)
