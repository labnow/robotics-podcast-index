from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import sys
from pathlib import Path

from .api import ApiError, ApplePodcastClient, PublicXiaoyuzhouClient
from .collection import collect_terms
from .db import active_keywords, connect, install_seeds, upsert_episode
from .evolve import propose
from .eligibility import transcription_candidates
from .export import export_csv
from .intelligence import curate_keywords, run_codex_parallel
from .quality import (RUBRIC_VERSION, calibration_report,
                      ensure_calibration_sample, run_quality)
from .quality_policy import (activate_quality_v2, rollback_quality, rollout_status,
                             set_quality_override)
from .scheduler import refresh_popularity_scores
from .seeds import SEED_KEYWORDS
from .site import build_site, validate_site
from .transcripts import fetch_public_transcripts
from .transcribe import transcribe_public_audio
from .rss import sync_feeds
from .registry import (build_historical_registry, resolve_ambiguous_registry,
                       recover_registry_from_episodes, registry_coverage_report,
                       seed_historical_registry)
from .recall import apple_recall_report
from .health import format_health, health_report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="robotcast")
    p.add_argument("--db", default="robotics_podcasts.db")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    collect = sub.add_parser("collect")
    collect.add_argument("--max-pages", type=int, default=1, help="Compatibility option; Apple search has one bounded result page")
    collect.add_argument("--page-size", type=int, default=200)
    collect.add_argument("--country", default="cn")
    collect.add_argument("--request-interval", type=float, default=2.0,
                         help="Minimum seconds between every API request (default: 2.0)")
    collect.add_argument("--max-retries", type=int, default=3)
    collect.add_argument("--keyword", action="append",
                         help="Search only this active keyword; repeatable (useful for smoke tests)")
    evolve = sub.add_parser("evolve")
    evolve.add_argument("--min-episodes", type=int, default=2)
    evolve.add_argument("--limit", type=int, default=100)
    classify = sub.add_parser("classify")
    classify.add_argument("--batch-size", type=int, default=5,
                          help="Episodes per Codex call (default: 5 for reliable structured output)")
    classify.add_argument("--model", default="gpt-5.6-luna",
                          help="Codex model (default: gpt-5.6-luna)")
    classify.add_argument("--reasoning-effort", default="low",
                          choices=["minimal", "low", "medium", "high", "xhigh", "max"],
                          help="Codex reasoning effort (default: low)")
    classify.add_argument("--all", action="store_true", help="Run batches until the backlog is empty")
    classify.add_argument("--min-matches", type=int, default=1,
                          help="Deprecated compatibility option; keyword count is ignored")
    classify.add_argument("--match-source", choices=["active", "shortlist"], default="active",
                          help="Deprecated compatibility option; keyword count is ignored")
    classify.add_argument("--max-episodes", type=int,
                          help="Stop after at most this many episodes in this invocation")
    classify.add_argument("--validation-retries", type=int, default=2,
                          help="Retry malformed structured responses (default: 2)")
    classify.add_argument("--workers", type=int, default=2,
                          help="Concurrent Codex calls (default: 2)")
    classify.add_argument("--launch-interval", type=float, default=2.0,
                          help="Seconds between launching concurrent calls (default: 2.0)")
    classify.add_argument("--episode-id", action="append",
                          help="Classify/diagnose a specific collected episode ID; repeatable")
    classify.add_argument("--only-relevant", action="store_true",
                          help="Quality-backfill only episodes already scored relevance 2 or 3")
    classify.add_argument("--min-plays", type=int,
                          help="Cheap pre-LLM eligibility floor; use 1 to skip zero-play episodes")
    classify.add_argument("--min-popularity-percentile", type=float,
                          help="Skip older episodes below this age-adjusted percentile (0-100)")
    classify.add_argument("--popularity-grace-days", type=int, default=14,
                          help="Always classify episodes this recent despite low popularity (default: 14)")
    transcripts = sub.add_parser("fetch-transcripts",
                                 help="Fetch publisher-provided public RSS transcripts")
    transcripts.add_argument("--max-episodes", type=int, default=10)
    transcripts.add_argument("--episode-id", action="append")
    transcripts.add_argument("--request-interval", type=float, default=4.0,
                             help="Minimum seconds between every API/CDN request (default: 4.0)")
    transcripts.add_argument("--directory", type=Path,
                             default=Path(".robotcast/transcripts"))
    quality = sub.add_parser("score-quality",
                             help="Apply the versioned 0-10 intrinsic-quality rubric")
    quality.add_argument("--batch-size", type=int, default=5)
    quality.add_argument("--workers", type=int, default=1,
                         help="Concurrent quality requests, each with batch-size episodes")
    quality.add_argument("--model", default="gpt-5.6-luna")
    quality.add_argument("--reasoning-effort", default="low",
                         choices=["minimal", "low", "medium", "high", "xhigh", "max"])
    quality.add_argument("--all", action="store_true")
    quality.add_argument("--max-episodes", type=int)
    quality.add_argument("--episode-id", action="append")
    quality.add_argument("--transcripts-only", action="store_true",
                         help="Assess only episodes with an available transcript")
    calibration = sub.add_parser("calibrate-quality",
                                 help="Run the persisted stratified quality-v2 sample")
    calibration.add_argument("--batch-size", type=int, default=5)
    calibration.add_argument("--model", default="gpt-5.6-luna")
    calibration.add_argument("--reasoning-effort", default="low",
                             choices=["minimal", "low", "medium", "high", "xhigh", "max"])
    calibration.add_argument("--max-episodes", type=int, default=10)
    calibration.add_argument("--sample-name", default="quality-v2-calibration")
    rollout = sub.add_parser("quality-rollout-status",
                             help="Check quality-v2 activation gates")
    rollout.add_argument("--mode", choices=["strict", "hybrid"], default="strict")
    activate_quality = sub.add_parser("activate-quality-v2",
                                      help="Atomically activate quality-v2 when ready")
    activate_quality.add_argument("--apply", action="store_true",
                                  help="Required confirmation; otherwise report only")
    activate_quality.add_argument("--mode", choices=["strict", "hybrid"],
                                  default="strict")
    sub.add_parser("rollback-quality", help="Atomically restore legacy quality")
    override = sub.add_parser("quality-override", help="Record a reviewed v2 score")
    override.add_argument("episode_id")
    override.add_argument("score_10", type=float)
    override.add_argument("--reason", required=True)
    override.add_argument("--reviewer", required=True)
    transcribe = sub.add_parser("transcribe", help="Transcribe selected public audio locally")
    transcribe.add_argument("--episode-id", action="append")
    transcribe.add_argument("--max-episodes", type=int, default=1)
    transcribe.add_argument("--model", default="large-v3")
    transcribe.add_argument("--device", default="cuda")
    transcribe.add_argument("--fp16", action=argparse.BooleanOptionalAction, default=True)
    transcribe.add_argument("--language", help="ISO language code; omit for auto-detection")
    transcribe.add_argument("--max-duration-seconds", type=int,
                            help="Skip episodes longer than this duration")
    transcribe.add_argument("--request-interval", type=float, default=4.0)
    transcribe.add_argument("--directory", type=Path, default=Path(".robotcast/transcripts"))
    transcribe.add_argument("--worker-id", default=f"{socket.gethostname()}-{os.getpid()}")
    transcribe.add_argument("--lease-minutes", type=int, default=360)
    transcribe.add_argument("--backend", choices=["openai-whisper", "faster-whisper"],
                            default="openai-whisper")
    transcribe.add_argument("--batch-size", type=int, default=4)
    transcribe.add_argument("--compute-type", default="float16")
    release_claims = sub.add_parser("release-transcription-claims",
                                    help="Release leases owned by a stopped worker")
    release_claims.add_argument("--worker-id", required=True)
    queue = sub.add_parser("transcription-queue",
                           help="Preview prioritized transcript-aware eligibility")
    queue.add_argument("--limit", type=int, default=20)
    queue.add_argument("--episode-id", action="append")
    queue.add_argument("--max-duration-seconds", type=int)
    curate = sub.add_parser("curate-keywords")
    curate.add_argument("--limit", type=int, default=40)
    curate.add_argument("--model", default="gpt-5.6-luna")
    curate.add_argument("--reasoning-effort", default="low",
                        choices=["minimal", "low", "medium", "high", "xhigh", "max"])
    probe = sub.add_parser("probe-keywords")
    probe.add_argument("--limit", type=int, default=40)
    probe.add_argument("--max-pages", type=int, default=1)
    probe.add_argument("--page-size", type=int, default=20)
    probe.add_argument("--request-interval", type=float, default=3.0)
    probe.add_argument("--max-retries", type=int, default=3)
    probe.add_argument("--country", default="cn")
    public = sub.add_parser("refresh-public", help="Refresh known public Xiaoyuzhou episode pages")
    public.add_argument("--episode-id", action="append")
    public.add_argument("--max-episodes", type=int, default=10)
    public.add_argument("--request-interval", type=float, default=4.0)
    feeds = sub.add_parser("sync-feeds", help="Poll public RSS feeds discovered through Apple")
    feeds.add_argument("--max-feeds", type=int, default=20)
    feeds.add_argument("--max-episodes-per-feed", type=int, default=20)
    feeds.add_argument("--request-interval", type=float, default=4.0)
    registry = sub.add_parser("build-rss-registry",
                              help="Match historically relevant shows to Apple and RSS")
    registry.add_argument("--max-shows", type=int, default=20)
    registry.add_argument("--country", default="cn")
    registry.add_argument("--request-interval", type=float, default=3.0)
    registry.add_argument("--retry-errors", action="store_true")
    registry.add_argument("--retry-not-found", action="store_true")
    resolve_registry = sub.add_parser("resolve-rss-registry",
        help="Resolve ambiguous show matches using historical episode evidence")
    resolve_registry.add_argument("--max-shows", type=int, default=10)
    resolve_registry.add_argument("--max-items", type=int, default=100)
    resolve_registry.add_argument("--request-interval", type=float, default=3.0)
    recover_registry = sub.add_parser("recover-rss-registry",
        help="Recover name-search misses using known episode titles")
    recover_registry.add_argument("--max-shows", type=int, default=20)
    recover_registry.add_argument("--queries-per-show", type=int, default=2)
    recover_registry.add_argument("--page-size", type=int, default=50)
    recover_registry.add_argument("--country", default="cn")
    recover_registry.add_argument("--request-interval", type=float, default=3.0)
    sub.add_parser("registry-report",
                   help="Report historical RSS and episode-identity coverage")
    sub.add_parser("discovery-recall-report",
                   help="Measure the latest complete Apple keyword replay")
    words = sub.add_parser("keywords")
    words.add_argument("--status", choices=["active", "candidate", "rejected"])
    words.add_argument("--shortlisted", action="store_true")
    for name in ("promote", "reject"):
        cmd = sub.add_parser(name)
        cmd.add_argument("term")
    sub.add_parser("stats")
    health = sub.add_parser("health-report", help="Report queues, throughput, errors, registry, and site state")
    health.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    health.add_argument("--site-directory", type=Path, default=Path("site-dist"))
    export = sub.add_parser("export-csv")
    export.add_argument("--out", type=Path, default=Path("robotics_episodes.csv"))
    export.add_argument("--min-relevance", type=int, choices=[0, 1, 2, 3], default=2)
    export.add_argument("--min-quality", type=float,
                        help="Exclude episodes below the active quality measure")
    export.add_argument("--zero-play-grace-days", type=int, default=0,
                        help="Export grace period for zero-play episodes (default: 0; negative disables filter)")
    export.add_argument("--description-max-chars", type=int,
                        help="Optionally truncate long descriptions in the exported view")
    build = sub.add_parser("build-site", help="Build the static curated-results website")
    build.add_argument("--output", type=Path, default=Path("site-dist"))
    validate = sub.add_parser("validate-site", help="Validate a generated result website")
    validate.add_argument("--directory", type=Path, default=Path("site-dist"))
    return p


def main() -> None:
    args = parser().parse_args()
    conn = connect(args.db)
    install_seeds(conn, SEED_KEYWORDS)
    if args.command == "init":
        print(f"Initialized {args.db} with {sum(map(len, SEED_KEYWORDS.values()))} curated terms")
    elif args.command == "collect":
        client = ApplePodcastClient(args.request_interval, max_retries=args.max_retries, country=args.country)
        terms = list(active_keywords(conn))
        if args.keyword:
            active = {term.casefold(): term for term in terms}
            unknown = [term for term in args.keyword if term.casefold() not in active]
            if unknown:
                sys.exit(f"Not an active keyword: {', '.join(unknown)}")
            terms = [active[term.casefold()] for term in args.keyword]
        result = collect_terms(conn, client, terms, args.max_pages, args.page_size)
        print(f"Collected {result['hits']} hits; {result['new']} new episodes; "
              f"{result['new_matches']} new keyword matches")
    elif args.command == "evolve":
        print(f"Added {propose(conn, args.min_episodes, args.limit)} candidate keywords for review")
    elif args.command == "classify":
        Path(".robotcast").mkdir(exist_ok=True)
        classifier_lock = Path(".robotcast/classify.lock").open("w")
        try:
            fcntl.flock(classifier_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("Another robotcast classifier is already running")
        refreshed = refresh_popularity_scores(conn)
        print(f"Refreshed popularity scores for {refreshed} episodes", file=sys.stderr)
        total = 0
        adaptive_batch_size = args.batch_size
        while True:
            batch_size = adaptive_batch_size
            if args.max_episodes is not None:
                remaining = args.max_episodes - total
                if remaining <= 0:
                    break
                batch_size = min(batch_size, remaining)
            count = None
            for attempt in range(args.validation_retries + 1):
                try:
                    count = run_codex_parallel(
                        conn, batch_size, args.workers, args.model,
                        args.reasoning_effort, args.episode_id,
                        args.only_relevant, args.min_plays,
                        None if args.min_popularity_percentile is None else
                        args.min_popularity_percentile / 100,
                        args.popularity_grace_days, args.launch_interval)
                    break
                except ValueError as exc:
                    if attempt == args.validation_retries:
                        if batch_size <= 1:
                            raise
                        adaptive_batch_size = max(1, batch_size - 2)
                        print(f"Batch of {batch_size} exhausted validation retries; "
                              f"reducing subsequent batches to {adaptive_batch_size}",
                              file=sys.stderr)
                        break
                    print(f"Malformed classification response; retrying batch ({attempt + 1}/{args.validation_retries}): {exc}",
                          file=sys.stderr)
            if count is None:
                continue
            total += count
            if args.all and count:
                print(f"Classified batch of {count}; total this run: {total}", file=sys.stderr)
            if not args.all or count == 0:
                break
        print(f"Classified {total} episodes" if total else "No episodes need classification")
    elif args.command == "fetch-transcripts":
        report = fetch_public_transcripts(conn, ApplePodcastClient(args.request_interval),
                                          args.directory, args.max_episodes, args.episode_id)
        print("Public transcript fetch: " + ", ".join(f"{k}={v}" for k, v in report.items()))
    elif args.command == "score-quality":
        Path(".robotcast").mkdir(exist_ok=True)
        quality_lock = Path(".robotcast/quality.lock").open("w")
        try:
            fcntl.flock(quality_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("Another robotcast quality scorer is already running")
        total = 0
        while True:
            batch_size = args.batch_size
            if args.max_episodes is not None:
                remaining = args.max_episodes - total
                if remaining <= 0:
                    break
                batch_size = min(batch_size, remaining)
            count = run_quality(conn, batch_size, args.model, args.reasoning_effort,
                                args.episode_id, args.transcripts_only, args.workers,
                                None if args.max_episodes is None else remaining)
            total += count
            if args.all and count:
                print(f"Scored quality batch of {count}; total this run: {total}",
                      file=sys.stderr)
            if not args.all or count == 0:
                break
        print(f"Scored {total} episodes with {RUBRIC_VERSION}"
              if total else f"No episodes need {RUBRIC_VERSION} assessment")
    elif args.command == "calibrate-quality":
        ensure_calibration_sample(conn, args.sample_name)
        ids = [row[0] for row in conn.execute("""SELECT episode_id
          FROM quality_calibration_selections WHERE sample_name=?
          ORDER BY stratum,episode_id""", (args.sample_name,))]
        total = 0
        while total < args.max_episodes:
            count = run_quality(conn, min(args.batch_size, args.max_episodes - total),
                                args.model, args.reasoning_effort, ids)
            if not count:
                break
            total += count
            print(f"Calibrated batch of {count}; total this run: {total}", file=sys.stderr)
        report = calibration_report(conn, args.sample_name)
        print("Quality calibration: " + ", ".join(f"{k}={v}" for k, v in report.items()))
    elif args.command == "quality-rollout-status":
        report = rollout_status(conn, mode=args.mode)
        print("Quality rollout: " + ", ".join(f"{k}={v}" for k, v in report.items()))
    elif args.command == "activate-quality-v2":
        report = rollout_status(conn, mode=args.mode)
        if not args.apply:
            print("Quality rollout dry run: " + ", ".join(f"{k}={v}" for k, v in report.items()))
        else:
            try:
                report = activate_quality_v2(conn, mode=args.mode)
            except RuntimeError as exc:
                sys.exit(str(exc))
            print("Quality rollout activated: " + ", ".join(f"{k}={v}" for k, v in report.items()))
    elif args.command == "rollback-quality":
        rollback_quality(conn)
        print("Quality measure restored to legacy")
    elif args.command == "quality-override":
        set_quality_override(conn, args.episode_id, args.score_10,
                             args.reason, args.reviewer)
        print(f"Quality override recorded for {args.episode_id}")
    elif args.command == "transcribe":
        Path(".robotcast").mkdir(exist_ok=True)
        safe_worker_id = "".join(c if c.isalnum() or c in "-_" else "_"
                                 for c in args.worker_id)
        transcription_lock = Path(f".robotcast/transcribe-{safe_worker_id}.lock").open("w")
        try:
            fcntl.flock(transcription_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("Another robotcast transcription worker is already running")
        report = transcribe_public_audio(conn, args.directory, args.max_episodes,
            args.episode_id, args.model, args.device, args.fp16, args.language,
            args.max_duration_seconds, args.request_interval, args.worker_id,
            args.lease_minutes, args.backend, args.batch_size, args.compute_type)
        print("Local transcription: " + ", ".join(f"{k}={v}" for k, v in report.items()))
    elif args.command == "release-transcription-claims":
        cursor = conn.execute("DELETE FROM transcription_claims WHERE worker_id=?",
                              (args.worker_id,))
        conn.commit()
        print(f"Released {cursor.rowcount} transcription claim(s) for {args.worker_id}")
    elif args.command == "transcription-queue":
        rows = transcription_candidates(conn, args.limit, args.episode_id,
                                        args.max_duration_seconds)
        for row in rows:
            print(f"{row['priority']:>3}  {row['reason']:<24}  "
                  f"{row['episode_id']}  {row['podcast_name']} — {row['title']}")
    elif args.command == "curate-keywords":
        print(f"Shortlisted {curate_keywords(conn, args.limit, args.model, args.reasoning_effort)} keywords")
    elif args.command == "probe-keywords":
        client = ApplePodcastClient(args.request_interval, max_retries=args.max_retries, country=args.country)
        terms = conn.execute("""SELECT s.term FROM keyword_shortlist s
          ORDER BY s.score DESC,s.term COLLATE NOCASE LIMIT ?""", (args.limit,)).fetchall()
        total_hits = total_new = 0
        for number, row in enumerate(terms, 1):
            term = row["term"]
            print(f"[{number}/{len(terms)}] probing {term}", file=sys.stderr)
            probe_id = conn.execute("INSERT INTO keyword_probes(term) VALUES(?)", (term,)).lastrowid
            hits = new = 0
            try:
                for page in client.search_pages(term, args.max_pages, args.page_size):
                    for episode in page:
                        hits += 1
                        new += int(upsert_episode(conn, episode, term))
                conn.execute("""UPDATE keyword_probes SET finished_at=CURRENT_TIMESTAMP,
                  hits_seen=?,new_episodes=? WHERE id=?""", (hits, new, probe_id))
                conn.commit()
            except (ApiError, OSError) as exc:
                conn.execute("""UPDATE keyword_probes SET finished_at=CURRENT_TIMESTAMP,error=?
                  WHERE id=?""", (str(exc), probe_id))
                conn.commit()
                raise
            total_hits += hits
            total_new += new
        print(f"Probed {len(terms)} keywords: {total_hits} hits, {total_new} new episodes")
    elif args.command == "refresh-public":
        client = PublicXiaoyuzhouClient(args.request_interval)
        ids = args.episode_id or [row[0] for row in conn.execute("""SELECT episode_id FROM episodes
          WHERE episode_id NOT LIKE 'apple_%' ORDER BY updated_at LIMIT ?""", (args.max_episodes,))]
        updated = errors = 0
        for episode_id in ids[:args.max_episodes]:
            try:
                episode = client.get_episode(episode_id)
                keyword = conn.execute("SELECT keyword FROM episode_matches WHERE episode_id=? LIMIT 1", (episode_id,)).fetchone()
                upsert_episode(conn, episode, keyword[0] if keyword else next(active_keywords(conn)))
                conn.commit(); updated += 1
            except (ApiError, OSError, ValueError):
                errors += 1
        print(f"Public refresh: updated={updated}, errors={errors}")
    elif args.command == "sync-feeds":
        report = sync_feeds(conn, ApplePodcastClient(args.request_interval), args.max_feeds,
                            args.max_episodes_per_feed)
        print("RSS sync: " + ", ".join(f"{k}={v}" for k, v in report.items()))
    elif args.command == "build-rss-registry":
        seeded = seed_historical_registry(conn)
        client = ApplePodcastClient(args.request_interval, country=args.country)
        report = build_historical_registry(conn, client, args.max_shows,
                                           args.retry_errors, args.retry_not_found)
        print(f"Historical RSS registry: seeded={seeded}, " +
              ", ".join(f"{k}={v}" for k, v in report.items()))
    elif args.command == "resolve-rss-registry":
        report = resolve_ambiguous_registry(conn, ApplePodcastClient(args.request_interval),
                                            args.max_shows, args.max_items)
        print("RSS registry review: " + ", ".join(f"{k}={v}" for k, v in report.items()))
    elif args.command == "recover-rss-registry":
        report = recover_registry_from_episodes(conn,
            ApplePodcastClient(args.request_interval, country=args.country),
            args.max_shows, args.queries_per_show, args.page_size)
        print("RSS registry episode recovery: " + ", ".join(
            f"{k}={v}" for k, v in report.items()))
    elif args.command == "registry-report":
        report = registry_coverage_report(conn)
        print("RSS registry coverage: " + ", ".join(
            f"{key}={value}" for key, value in report.items()))
    elif args.command == "discovery-recall-report":
        report = apple_recall_report(conn)
        print("Apple discovery recall: " + ", ".join(
            f"{key}={value}" for key, value in report.items()))
    elif args.command == "keywords":
        if args.shortlisted:
            for row in conn.execute("""SELECT s.score,s.term,s.reason,k.evidence_count,
              (SELECT hits_seen FROM keyword_probes p WHERE p.term=s.term AND p.error IS NULL ORDER BY p.id DESC LIMIT 1) probe_hits,
              (SELECT new_episodes FROM keyword_probes p WHERE p.term=s.term AND p.error IS NULL ORDER BY p.id DESC LIMIT 1) probe_new,
              (SELECT count(*) FROM episode_matches m JOIN episodes e ON e.episode_id=m.episode_id
                WHERE m.keyword=s.term AND e.relevance_score IS NOT NULL) classified_hits,
              (SELECT count(*) FROM episode_matches m JOIN episodes e ON e.episode_id=m.episode_id
                WHERE m.keyword=s.term AND e.relevance_score>=2) relevant_hits
              FROM keyword_shortlist s JOIN keywords k USING(term)
              ORDER BY s.score DESC,k.evidence_count DESC,s.term COLLATE NOCASE"""):
                probe = "not-probed" if row["probe_hits"] is None else f"probe={row['probe_new']}/{row['probe_hits']} new"
                precision = (f"precision={row['relevant_hits']}/{row['classified_hits']}"
                             if row["classified_hits"] else "precision=untested")
                print(f"{row['score']}  {row['term']:<40} evidence={row['evidence_count']} {probe} {precision}  {row['reason']}")
            return
        where, params = ("WHERE status=?", (args.status,)) if args.status else ("", ())
        for row in conn.execute(f"SELECT term,status,source,evidence_count,evidence_json FROM keywords {where} ORDER BY status, evidence_count DESC, term", params):
            print(f"{row['status']:9} {row['term']:<30} evidence={row['evidence_count']} {row['evidence_json']}")
    elif args.command in ("promote", "reject"):
        status = "active" if args.command == "promote" else "rejected"
        cur = conn.execute("UPDATE keywords SET status=?,reviewed_at=CURRENT_TIMESTAMP WHERE term=?", (status, args.term))
        conn.commit()
        if not cur.rowcount:
            sys.exit(f"Unknown keyword: {args.term}")
        print(f"{args.term}: {status}")
    elif args.command == "stats":
        print(f"episodes\t{conn.execute('SELECT count(*) FROM episodes').fetchone()[0]}")
        for row in conn.execute("SELECT status,count(*) n FROM keywords GROUP BY status ORDER BY status"):
            print(f"keywords.{row['status']}\t{row['n']}")
    elif args.command == "health-report":
        report = health_report(conn, args.site_directory)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
              else format_health(report))
    elif args.command == "export-csv":
        grace = None if args.zero_play_grace_days < 0 else args.zero_play_grace_days
        print(f"Exported {export_csv(conn, args.out, args.min_relevance, args.min_quality, grace, args.description_max_chars)} episodes to {args.out}")
    elif args.command == "build-site":
        report = build_site(conn, args.output)
        print(f"Built {report['episodes']} episodes in {report['directory']} "
              f"(index {report['indexBytes']} bytes; total {report['totalBytes']} bytes)")
    elif args.command == "validate-site":
        report = validate_site(args.directory)
        print(f"Validated {report['episodes']} episodes in {report['directory']} "
              f"(index {report['indexBytes']} bytes; total {report['totalBytes']} bytes)")
