from __future__ import annotations

from .quality import RUBRIC_VERSION, calibration_report

SETTING = "primary_quality_measure"
HYBRID_VERSION = "quality-v2-hybrid"


def active_quality_measure(conn) -> str:
    row = conn.execute("SELECT value FROM system_settings WHERE key=?", (SETTING,)).fetchone()
    return row[0] if row else "legacy"


def set_quality_override(conn, episode_id: str, score_10: float, reason: str,
                         reviewer: str) -> None:
    if not 0 <= score_10 <= 10:
        raise ValueError("score_10 must be between 0 and 10")
    if not reason.strip() or not reviewer.strip():
        raise ValueError("reason and reviewer are required")
    exists = conn.execute("SELECT 1 FROM episodes WHERE episode_id=?", (episode_id,)).fetchone()
    if not exists:
        raise ValueError(f"unknown episode: {episode_id}")
    tier = 3 if score_10 >= 8 else 2 if score_10 >= 5 else 1 if score_10 >= 2 else 0
    conn.execute("""INSERT INTO quality_overrides
      (episode_id,rubric_version,score_10,quality_tier,reason,reviewer)
      VALUES(?,?,?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET
      rubric_version=excluded.rubric_version,score_10=excluded.score_10,
      quality_tier=excluded.quality_tier,reason=excluded.reason,
      reviewer=excluded.reviewer,reviewed_at=CURRENT_TIMESTAMP""",
      (episode_id, RUBRIC_VERSION, score_10, tier, reason.strip(), reviewer.strip()))
    conn.commit()


def quality_sql(measure: str) -> dict[str, str | int]:
    legacy_ten = "CASE c.quality_score WHEN 3 THEN 9.0 WHEN 2 THEN 6.0 WHEN 1 THEN 3.0 ELSE 0.0 END"
    if measure == HYBRID_VERSION:
        eligible = "q.transcript_used=1 AND q.confidence=2"
        return {
            "score": f"coalesce(o.score_10,CASE WHEN {eligible} THEN q.score_10 END,{legacy_ten})",
            "tier": f"coalesce(o.quality_tier,CASE WHEN {eligible} THEN q.quality_tier END,c.quality_score)",
            "reason": f"coalesce(o.reason,CASE WHEN {eligible} THEN q.reason END,c.quality_reason)",
            "confidence": f"CASE WHEN o.episode_id IS NOT NULL THEN 2 WHEN {eligible} THEN q.confidence END",
            "evidence": f"CASE WHEN o.episode_id IS NOT NULL THEN 'reviewed_override' WHEN {eligible} THEN 'transcript' ELSE 'legacy_fallback' END",
            "scale": 10, "minimum": 5,
        }
    if measure == RUBRIC_VERSION:
        return {
            "score": "coalesce(o.score_10,q.score_10)",
            "tier": "coalesce(o.quality_tier,q.quality_tier)",
            "reason": "coalesce(o.reason,q.reason)",
            "confidence": "CASE WHEN o.episode_id IS NOT NULL THEN 2 ELSE q.confidence END",
            "evidence": "CASE WHEN o.episode_id IS NOT NULL THEN 'reviewed_override' WHEN q.transcript_used=1 THEN 'transcript' ELSE 'metadata' END",
            "scale": 10, "minimum": 5,
        }
    return {
        "score": "c.quality_score", "tier": "c.quality_score",
        "reason": "c.quality_reason", "confidence": "NULL",
        "evidence": "'legacy'", "scale": 3, "minimum": 2,
    }


def rollout_status(conn, sample_name: str = "quality-v2-calibration",
                   mode: str = "strict") -> dict:
    calibration = calibration_report(conn, sample_name)
    retained = conn.execute("""SELECT count(*) FROM episodes e
      JOIN episode_classifications c USING(episode_id)
      WHERE e.relevance_score>=2 AND c.quality_score>=2
        AND (e.play_count IS NULL OR e.play_count>0)""").fetchone()[0]
    covered = conn.execute("""SELECT count(*) FROM episodes e
      JOIN episode_classifications c USING(episode_id)
      WHERE e.relevance_score>=2 AND c.quality_score>=2
        AND (e.play_count IS NULL OR e.play_count>0)
        AND (EXISTS(SELECT 1 FROM quality_assessments q
          WHERE q.episode_id=e.episode_id AND q.rubric_version=?
            AND q.transcript_used=1 AND q.confidence=2)
          OR EXISTS(SELECT 1 FROM quality_overrides o
          WHERE o.episode_id=e.episode_id AND o.rubric_version=?))""",
        (RUBRIC_VERSION, RUBRIC_VERSION)).fetchone()[0]
    blockers = []
    if calibration["assessed"] < 100:
        blockers.append("sample_incomplete")
    if calibration["paired_negative_controls"] < 5 or calibration["paired_boundaries"] < 5:
        blockers.append("insufficient_stratified_pairs")
    if mode == "strict":
        if calibration["unresolved_boundaries"]:
            blockers.append("unresolved_quality_boundaries")
        if covered < retained:
            blockers.append("retained_corpus_missing_evidence")
    elif mode != "hybrid":
        raise ValueError("mode must be strict or hybrid")
    return {
        "mode": mode, "target_measure": RUBRIC_VERSION if mode == "strict" else HYBRID_VERSION,
        "active_measure": active_quality_measure(conn), "retained": retained,
        "covered": covered, "missing": retained - covered,
        "ready": not blockers, "blockers": blockers,
    }


def activate_quality_v2(conn, sample_name: str = "quality-v2-calibration",
                        mode: str = "strict") -> dict:
    status = rollout_status(conn, sample_name, mode)
    if not status["ready"]:
        raise RuntimeError("quality-v2 rollout blocked: " + ", ".join(status["blockers"]))
    with conn:
        conn.execute("""INSERT INTO system_settings(key,value) VALUES(?,?)
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,
          updated_at=CURRENT_TIMESTAMP""", (SETTING, status["target_measure"]))
    return rollout_status(conn, sample_name, mode)


def rollback_quality(conn) -> None:
    with conn:
        conn.execute("""INSERT INTO system_settings(key,value) VALUES(?,'legacy')
          ON CONFLICT(key) DO UPDATE SET value='legacy',updated_at=CURRENT_TIMESTAMP""",
          (SETTING,))
