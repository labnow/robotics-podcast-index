# Robotics Podcast Collection

An episode-level Xiaoyuzhou collector for robotics and embodied AI. Discovery is deliberately high-recall keyword search; relevance scoring and keyword evolution are separate stages.

Runtime commands use the `robotcast-whisper-gpu` Conda environment. Service
installation, private data boundaries, scheduling, monitoring, and recovery are
documented in [`docs/operations.md`](docs/operations.md).

## Repository layout

- `robotcast/`: collector, matching, classification, quality, transcription, and site code.
- `scripts/`: manual and scheduled operational entry points.
- `packaging/systemd/`: portable user-service templates and timers.
- `config/`: non-secret configuration examples.
- `docs/`: architecture, rollout, migration, and operational documentation.
- `tests/`: database, matching, quality, export, and site regression tests.

## How discovery improves

1. Active seed terms retrieve candidate episodes.
2. Every episode and every keyword match is stored, so marginal yield is measurable.
3. `evolve` mines repeated Chinese/English phrases from the titles and show notes of relevant episodes.
4. Suggestions enter a review queue with supporting episode counts and examples.
5. A person promotes or rejects each suggestion. Promoted terms participate in later collection runs.

This avoids pretending keyword search is semantic search while still letting the vocabulary learn from the corpus. A future LLM/embedding classifier can populate `relevance_score`; the storage model already supports it.

## Quick start

```bash
python3 -m robotcast init
python3 -m robotcast collect --page-size 200 --request-interval 3
python3 -m robotcast classify --all --batch-size 5
python3 -m robotcast evolve
python3 -m robotcast curate-keywords --limit 40
python3 -m robotcast keywords --shortlisted
python3 -m robotcast probe-keywords --max-pages 1 --request-interval 3
python3 -m robotcast keywords --status candidate
python3 -m robotcast promote '端到端控制'
```

By default data is stored in `robotics_podcasts.db`. Acquisition uses public Apple Search,
RSS, and public episode webpages. Use conservative result limits and request delays.

Robotcast is authentication-free. It does not accept, store, or refresh a Xiaoyuzhou token.
Discovery uses Apple's public podcast-episode search; known Xiaoyuzhou episodes can be
refreshed from their public webpages, and discovered RSS feeds can be polled directly.

Every API request—not just every result page—is separated by an explicit timer. The default is two seconds and can be made more conservative:

```bash
python3 -m robotcast collect --request-interval 3 --max-pages 1
```

Use a single active term for a low-impact connectivity check:

```bash
python3 -m robotcast collect --keyword 机器人 --max-pages 1 --request-interval 3
```

HTTP `429` and transient `5xx` responses use bounded exponential backoff and respect `Retry-After` when supplied.

## Commands

- `init`: create the database and install curated seed terms.
- `collect`: search Apple's public podcast-episode catalog for all active terms and retain source provenance.
- `refresh-public`: refresh known public Xiaoyuzhou episode pages without credentials.
- `sync-feeds`: poll public RSS feeds learned from Apple results.
- `build-rss-registry`: resumably match historically relevant shows to Apple podcast entries and canonical public RSS feeds. Exact show-name matches are accepted; plausible collisions remain marked ambiguous for review.
- `resolve-rss-registry`: inspect ambiguous candidate feeds and accept only a unique candidate supported by historical episode-title or audio-URL overlap.
- `recover-rss-registry`: retry Apple feed discovery for name-search misses using
  distinctive known episode titles; accept only exact episode evidence plus a
  compatible show identity, or at least two exact episode-title identities.
- `classify`: invoke local `codex exec` for one read-only batch. A cheap empirical gate admits candidates from trusted shows or active keywords with at least 50% smoothed historical precision, while reserving 10% of every ordinary batch for deterministic low-evidence exploration. Raw keyword-overlap count is not treated as relevance. A JSON Schema constrains the response; unchanged episodes are skipped using a content hash.
- `fetch-transcripts`: fetch publisher-provided public RSS transcripts.
- `transcribe`: download selected public audio and transcribe locally with OpenAI Whisper; the default configuration is `large-v3` on CUDA with FP16.
- `score-quality`: apply the versioned `quality-v2.0` rubric to relevant episodes. Codex returns five evidence dimensions, penalty flags, and confidence; Python deterministically calculates the 0–10 score and derived tier. During calibration, this does not replace the production 0–3 tier.
- `calibrate-quality`: create and resume a persisted 100-episode stratified sample (30 legacy Q3, 40 Q2, 20 Q1, and 10 roundup/promotional edge cases), preserving metadata-only and transcript-informed assessment runs for comparison.
- `quality-rollout-status`: report transcript/review coverage and every blocker without changing production. `--mode hybrid` evaluates the safe staged policy that falls back to mapped legacy scores.
- `quality-override`: record a versioned, attributed manual score and review reason.
- `activate-quality-v2`: dry-run by default; `--apply` atomically switches consumers only when every gate passes. Choose `--mode strict` (complete v2 evidence) or `--mode hybrid` (reviewed/transcript v2, otherwise legacy fallback).
- `rollback-quality`: atomically restores legacy quality sorting, filtering, and display.
- `transcription-queue`: preview explainable ASR priorities before spending GPU time.
- `discovery-recall-report`: measure known-relevant recovery from the latest complete active-keyword Apple replay.
- `evolve`: propose repeated phrases only from episodes with `relevance_score >= 2`.
- `curate-keywords`: use a schema-constrained Luna pass to rank a small candidate shortlist by expected marginal discovery value; it does not activate terms.
- `probe-keywords`: search shortlisted terms without activating them and record their hits and marginal new-episode yield.

The shortlist report shows both marginal yield and observed classification precision. A conservative promotion policy is: at least five classified hits, at least 80% with relevance ≥2, and at least three new episodes.

Evaluate a bounded sample of probe discoveries before promotion:

```bash
python3 -m robotcast classify --all --match-source shortlist \
  --max-episodes 80 --batch-size 5
```
- `keywords`: inspect active/candidate/rejected terms and evidence.
- `promote TERM` / `reject TERM`: review an evolved term.
- `stats`: show corpus and keyword counts.
- `export-csv`: export classified episodes as UTF-8 CSV for analysis or sharing. The clean view excludes zero-play episodes by default without deleting them; use `--zero-play-grace-days -1` to include them. `--min-quality 2` creates a stricter view once legacy quality scoring is complete.
- `build-site`: deterministically generate the static result browser in `site-dist/`.
- `validate-site`: verify the generated site, public-data policy, links, ordering, and deployable files.
- `classify --min-popularity-percentile 50` limits records with known engagement to the upper half of each age/show-adjusted popularity distribution; recent episodes, unknown-engagement Apple/RSS discoveries, and explicit recall audits use source-aware exceptions.

Diagnose and classify a known collected episode directly. Reported misses use the
`recall_audit` scheduler lane and should also become regression cases:

```bash
python3 -m robotcast classify --episode-id 69c07cc2719b26db81d9720a
```

Backfill quality for the clean retained corpus with a bounded, inexpensive gate:

```bash
python3 -m robotcast classify --all --only-relevant --min-plays 1 \
  --max-episodes 290 --batch-size 5 --model gpt-5.6-luna --reasoning-effort low
```

Fetch a publisher-provided RSS transcript, or transcribe one selected public episode locally:

```bash
python3 -m robotcast fetch-transcripts --max-episodes 1 --request-interval 4
CUDA_VISIBLE_DEVICES=1 conda run -n robotcast-whisper-gpu python -m robotcast \
  transcribe --episode-id EPISODE_ID --model large-v3 --fp16 --language zh
python3 -m robotcast score-quality --max-episodes 1 --batch-size 1
```

Build the historical feed registry in conservative, resumable batches:

```bash
python3 -m robotcast build-rss-registry --max-shows 20 --request-interval 3
python3 -m robotcast resolve-rss-registry --max-shows 10 --request-interval 3
python3 -m robotcast sync-feeds --max-feeds 20 --max-episodes-per-feed 1000
```

For GPU transcription, use the isolated Conda environment:

```bash
conda activate robotcast-whisper-gpu
CUDA_VISIBLE_DEVICES=1 python -m robotcast transcribe --episode-id EPISODE_ID --device cuda --fp16
```

Transcripts are stored as compressed JSON under `.robotcast/transcripts/` and
are never included in site or spreadsheet exports. Not every episode has an
publisher transcript. Audio is downloaded only to a temporary directory for local ASR.
Equal-priority ASR candidates are ordered by shortest duration first. Use
`--max-duration-seconds` to impose a GPU budget; completed local runs record model,
device, inference seconds, audio duration, and effective realtime factor in SQLite.

Quality v2 scores five 0–2 dimensions—depth, specificity, expertise,
originality, and structure—then applies explicit penalties for automated
roundups, promotion, broad news bundles, repackaging, insufficient evidence,
extreme brevity, unsupported sensationalism, and duplicates. A confidence-zero
assessment is capped at 4/10. The rubric and assessments are versioned so a
calibration run does not reopen or overwrite the completed relevance queue.
The paired calibration currently blocks a metadata-only rollout because boundary
scores are strongly transcript-sensitive. See
[`docs/quality-v2-rollout.md`](docs/quality-v2-rollout.md) for the evidence contract,
atomic rollout, and rollback design.

Inspect rollout readiness without changing production:

```bash
python3 -m robotcast quality-rollout-status
python3 -m robotcast quality-rollout-status --mode hybrid
python3 -m robotcast activate-quality-v2
python3 -m robotcast activate-quality-v2 --mode hybrid
```

Activation requires the explicit `--apply` flag and still refuses while any gate is
open. Reviewed exceptions require both attribution and a reason:

```bash
python3 -m robotcast quality-override EPISODE_ID 9 \
  --reviewer REVIEWER --reason 'Transcript reviewed manually.'
python3 -m robotcast activate-quality-v2 --mode hybrid --apply
python3 -m robotcast rollback-quality
```

The hybrid command above is shown as the eventual staged activation path; production
remains on `legacy` until the explicit `--apply` command is run.

Search observations retain query, rank, page, run, and collection kind. Engagement
observations retain the play/comment count and observation time, so overlapping monthly
runs refresh evidence without duplicating episodes or repeating unchanged LLM work.

Useful options are available with `python3 -m robotcast --help` and per-command `--help`.

Run the resumable auth-less update pipeline (production deployment remains separate):

```bash
scripts/manual_update.sh
scripts/manual_update.sh --with-quality --with-transcription
```

## Background queues

Install user-level services for the monthly main pipeline, unattended
transcription/classification, transcript-aware scoring, and site publication:

```bash
scripts/install_background_services.sh
```

The main refresh runs monthly and performs Apple discovery, full RSS synchronization,
public-page enrichment, and publisher-transcript fetching. The transcription worker
runs `large-v3` with FP16 on physical GPU 1 and processes
up to 50 queued episodes per activation. The classifier processes bounded chunks of
100 episodes with two concurrent Codex calls. SQLite eligibility and content hashes
are the durable queues; command-level locks prevent duplicate workers. Timers rerun
idle or completed queues every 10 and 15 minutes. A weekly main publication pass
scores newly available transcripts, rebuilds and validates the static site, and
publishes it when a GitHub remote is configured. Ad-hoc publication remains
available at any time.

Monitor or stop them with:

```bash
systemctl --user status robotcast-transcribe.timer robotcast-classify.timer
scripts/robotcast_service.sh status
scripts/robotcast_service.sh logs
scripts/robotcast_service.sh refresh  # ad-hoc full auth-less refresh
scripts/robotcast_service.sh publish  # ad-hoc score/build/publish
```

For this workload, one Whisper process per GPU is the recommended default. Running
multiple `large-v3` models on one 24 GB GPU adds memory pressure and usually provides
little throughput benefit. A second GPU is useful only as a separate worker after
adding atomic job claims; the current single-worker lock intentionally favors simple,
reliable overnight operation.

Successful stages are recorded in `.robotcast/manual-update.state`; rerunning after a
failure resumes at the first unfinished stage. `ROBOTCAST_CLASSIFY_LIMIT` bounds the
classification stage and defaults to 100 episodes.

## Result site deployment

Set the target repository in the private service environment file:

```bash
echo 'ROBOTCAST_PAGES_REMOTE=git@github.com:OWNER/REPOSITORY.git' \
  >> ~/.config/robotcast/robotcast.env
```

Configure GitHub Pages once to deploy from the `gh-pages` branch, then rebuild,
validate, and publish ad hoc with:

```bash
scripts/update_result_site.sh
```

The same publication path is invoked automatically once per week. It commits only
when generated content changed. Collection, SQLite, Codex, and Whisper remain local;
only validated static files are pushed to `gh-pages`.
