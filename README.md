# Robotics Podcast Collection

An episode-level Xiaoyuzhou collector for robotics and embodied AI. Discovery is deliberately high-recall keyword search; relevance scoring and keyword evolution are separate stages.

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
- `classify`: invoke local `codex exec` for one read-only batch. Selection uses available age-adjusted popularity, recency, trusted-show history, and a deterministic exploration lane—not keyword-overlap count. A JSON Schema constrains the response; unchanged episodes are skipped using a content hash.
- `fetch-transcripts`: fetch publisher-provided public RSS transcripts.
- `transcribe`: download selected public audio and transcribe locally with faster-whisper; the default model is `large-v3-turbo`.
- `score-quality`: apply the versioned `quality-v2.0` rubric to relevant episodes. Codex returns five evidence dimensions, penalty flags, and confidence; Python deterministically calculates the 0–10 score and derived tier. During calibration, this does not replace the production 0–3 tier.
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
python3 -m robotcast transcribe --episode-id EPISODE_ID --model large-v3-turbo
python3 -m robotcast score-quality --max-episodes 1 --batch-size 1
```

Transcripts are stored as compressed JSON under `.robotcast/transcripts/` and
are never included in site or spreadsheet exports. Not every episode has an
publisher transcript. Audio is downloaded only to a temporary directory for local ASR.

Quality v2 scores five 0–2 dimensions—depth, specificity, expertise,
originality, and structure—then applies explicit penalties for automated
roundups, promotion, broad news bundles, repackaging, insufficient evidence,
extreme brevity, unsupported sensationalism, and duplicates. A confidence-zero
assessment is capped at 4/10. The rubric and assessments are versioned so a
calibration run does not reopen or overwrite the completed relevance queue.

Search observations retain query, rank, page, run, and collection kind. Engagement
observations retain the play/comment count and observation time, so overlapping monthly
runs refresh evidence without duplicating episodes or repeating unchanged LLM work.

Useful options are available with `python3 -m robotcast --help` and per-command `--help`.

## Result site deployment

Authenticate once with EdgeOne's CLI credential store:

```bash
npx --yes edgeone@1.6.40 login
```

Then rebuild, validate, and create an isolated preview deployment:

```bash
scripts/update_result_site.sh
```

Publish the same workflow explicitly to production:

```bash
scripts/update_result_site.sh --production
```

For CI, set `EDGEONE_API_TOKEN` in the CI secret store. The script passes it directly
to EdgeOne and never writes it into the repository or generated site. Override the
default project with `EDGEONE_PROJECT_NAME` or `--project` when needed.
