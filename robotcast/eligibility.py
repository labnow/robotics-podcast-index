from __future__ import annotations


def transcription_candidates(conn, limit: int = 20,
                             episode_ids: list[str] | None = None,
                             max_duration_seconds: int | None = None):
    """Return a bounded, explainable ASR queue ordered by expected value."""
    params: list[object] = []
    where = ["e.relevance_score>=2", "s.audio_url IS NOT NULL",
             "(t.episode_id IS NULL OR (t.status='error' AND "
             "t.last_attempt_at<datetime('now','-1 day')))" ]
    if episode_ids:
        where.append(f"e.episode_id IN ({','.join('?' for _ in episode_ids)})")
        params.extend(episode_ids)
    if max_duration_seconds is not None:
        where.append("e.duration_seconds<=?")
        params.append(max_duration_seconds)
    params.append(limit)
    manual = 1 if episode_ids else 0
    return conn.execute(f"""SELECT e.episode_id,e.title,e.podcast_name,e.relevance_score,
      e.quality_score,e.quality_score_10,e.quality_confidence,e.duration_seconds,
      max(s.audio_url) AS audio_url,
      CASE
        WHEN {manual}=1 THEN 100
        WHEN e.quality_score_10 BETWEEN 4 AND 8
          AND coalesce(e.quality_confidence,0)<=1 THEN 95
        WHEN e.relevance_score=3 AND e.quality_score=3 THEN 85
        WHEN e.relevance_score=3 THEN 80
        WHEN e.quality_score=2 THEN 70
        WHEN e.quality_score=3 THEN 65
        ELSE 50 END AS priority,
      CASE
        WHEN {manual}=1 THEN 'manual_request'
        WHEN e.quality_score_10 BETWEEN 4 AND 8
          AND coalesce(e.quality_confidence,0)<=1 THEN 'uncertain_q2_q3_boundary'
        WHEN e.relevance_score=3 AND e.quality_score=3 THEN 'high_relevance_legacy_q3'
        WHEN e.relevance_score=3 THEN 'high_relevance'
        WHEN e.quality_score=2 THEN 'legacy_q2_boundary'
        WHEN e.quality_score=3 THEN 'legacy_q3_boundary'
        ELSE 'relevant_fallback' END AS reason
      FROM episodes e JOIN episode_sources s USING(episode_id)
      LEFT JOIN episode_transcripts t USING(episode_id)
      WHERE {' AND '.join(where)}
      GROUP BY e.episode_id
      ORDER BY priority DESC,e.duration_seconds ASC,e.age_popularity_percentile DESC,
        e.published_at DESC,e.episode_id LIMIT ?""", params).fetchall()
