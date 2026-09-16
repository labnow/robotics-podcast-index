from __future__ import annotations

import json
from pathlib import Path

from .quality_policy import active_quality_measure
from .registry import registry_coverage_report


def _scalar(conn, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchone()[0]


def health_report(conn, site_directory: Path = Path("site-dist")) -> dict:
    """Return a small, machine-readable operational snapshot."""
    transcript = {row["status"]: row["n"] for row in conn.execute(
        "SELECT status,count(*) n FROM episode_transcripts GROUP BY status")}
    registry = registry_coverage_report(conn)
    latest_run = conn.execute("""SELECT kind,started_at,finished_at,error,hits_seen,
      new_episodes,updated_episodes FROM collection_runs ORDER BY id DESC LIMIT 1""").fetchone()
    latest_error = conn.execute("""SELECT error FROM collection_runs
      WHERE error IS NOT NULL ORDER BY id DESC LIMIT 1""").fetchone()

    site = {"present": False}
    manifest_path = site_directory / "data" / "index.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            site = {"present": True, "episodes": manifest.get("count"),
                    "generated_at": manifest.get("generatedAt"),
                    "data_updated_at": manifest.get("dataUpdatedAt"),
                    "bytes": sum(p.stat().st_size for p in site_directory.rglob("*") if p.is_file())}
        except (OSError, ValueError):
            site = {"present": True, "error": "invalid manifest"}

    relevant = _scalar(conn, "SELECT count(*) FROM episodes WHERE relevance_score>=2")
    transcript_ready = _scalar(conn, """SELECT count(*) FROM episodes e
      JOIN episode_transcripts t USING(episode_id)
      WHERE e.relevance_score>=2 AND t.status='available'""")
    return {
        "quality_measure": active_quality_measure(conn),
        "corpus": {
            "episodes": _scalar(conn, "SELECT count(*) FROM episodes"),
            "classified": _scalar(conn, "SELECT count(*) FROM episodes WHERE relevance_score IS NOT NULL"),
            "unclassified": _scalar(conn, "SELECT count(*) FROM episodes WHERE relevance_score IS NULL"),
            "relevant": relevant,
        },
        "throughput_24h": {
            "classified": _scalar(conn, """SELECT count(*) FROM episode_classifications
              WHERE classified_at >= datetime('now','-1 day')"""),
            "transcripts_available": _scalar(conn, """SELECT count(*) FROM episode_transcripts
              WHERE status='available' AND last_attempt_at >= datetime('now','-1 day')"""),
        },
        "transcripts": {
            "available": transcript.get("available", 0),
            "no_subtitle": transcript.get("no_subtitle", 0),
            "errors": transcript.get("error", 0),
            "relevant_available": transcript_ready,
            "relevant_missing": relevant - transcript_ready,
        },
        "registry": registry,
        "reviews": {"pending_matches": _scalar(
            conn, "SELECT count(*) FROM match_review_queue WHERE status='pending'")},
        "latest_collection": dict(latest_run) if latest_run else None,
        "latest_collection_error": latest_error[0] if latest_error else None,
        "site": site,
    }


def format_health(report: dict) -> str:
    corpus, transcripts = report["corpus"], report["transcripts"]
    registry, throughput = report["registry"], report["throughput_24h"]
    site, run = report["site"], report["latest_collection"]
    lines = [
        f"Quality: {report['quality_measure']}",
        f"Corpus: {corpus['episodes']} episodes; {corpus['classified']} classified; {corpus['unclassified']} queued; {corpus['relevant']} relevant",
        f"Last 24h: {throughput['classified']} classified; {throughput['transcripts_available']} transcripts completed",
        f"Transcripts: {transcripts['available']} available; {transcripts['errors']} errors; {transcripts['relevant_missing']} relevant episodes remain",
        f"RSS registry: {registry['matched_shows']}/{registry['historical_shows']} shows matched ({registry['show_coverage_percent']}%); {registry['ambiguous_shows']} ambiguous",
        f"Match review queue: {report['reviews']['pending_matches']} pending",
    ]
    if run:
        state = "failed" if run["error"] else ("complete" if run["finished_at"] else "running")
        lines.append(f"Latest collection: {run['kind']} {state}; started {run['started_at']}")
    else:
        lines.append("Latest collection: none")
    if site.get("present") and not site.get("error"):
        lines.append(f"Local site: {site['episodes']} episodes; {site['bytes']} bytes; generated {site['generated_at']}")
    else:
        lines.append("Local site: missing or invalid")
    if report["latest_collection_error"]:
        lines.append(f"Latest collection error: {report['latest_collection_error']}")
    return "\n".join(lines)
