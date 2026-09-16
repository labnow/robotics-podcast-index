from __future__ import annotations


def apple_recall_report(conn) -> dict:
    active = conn.execute(
        "SELECT count(*) FROM keywords WHERE status='active'").fetchone()[0]
    run = conn.execute("""SELECT * FROM collection_runs
      WHERE kind='routine' AND keywords_searched=? AND error IS NULL
      AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1""", (active,)).fetchone()
    if not run:
        return {"complete_replay": False, "active_keywords": active}
    known = conn.execute(
        "SELECT count(*) FROM episodes WHERE relevance_score>=2").fetchone()[0]
    recovered = conn.execute("""SELECT count(DISTINCT e.episode_id)
      FROM episodes e JOIN search_observations s USING(episode_id)
      WHERE s.collection_run_id=? AND e.relevance_score>=2""", (run["id"],)).fetchone()[0]
    older = conn.execute("""SELECT count(DISTINCT e.episode_id)
      FROM episodes e JOIN search_observations s USING(episode_id)
      WHERE s.collection_run_id=? AND e.relevance_score>=2
        AND julianday('now')-julianday(e.published_at)>1095""", (run["id"],)).fetchone()[0]
    return {
        "complete_replay": True, "run_id": run["id"],
        "active_keywords": active, "apple_hits": run["hits_seen"],
        "new_episodes": run["new_episodes"], "known_relevant": known,
        "recovered_relevant": recovered,
        "recall_percent": round(100 * recovered / known, 2) if known else 0.0,
        "recovered_older_than_3y": older,
    }
