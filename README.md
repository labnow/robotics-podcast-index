# Robotics Podcast Index

An authentication-free pipeline that discovers Chinese robotics and embodied-AI
podcast episodes, classifies relevance and quality, transcribes selected audio,
and publishes a compact static index.

Public site: <https://labnow.github.io/robotics-podcast-index/>

## What runs

- Monthly: Apple discovery, historical RSS recovery, feed synchronization, and public-page enrichment.
- Continuously: resumable classification and lease-safe multi-GPU Whisper transcription queues.
- Weekly: transcript-aware scoring, static-site validation, and GitHub Pages publication.
- On demand: the same refresh and publication paths can be triggered manually.

All private/runtime data stays local. GitHub Pages receives only the validated static site.

## Quick start

Use the repository Conda environment for every Python command:

```bash
conda activate robotcast-whisper-gpu
python -m robotcast init
python -m robotcast health-report
```

Run a bounded discovery pass:

```bash
python -m robotcast collect --max-pages 1 --request-interval 3
python -m robotcast classify --all --max-episodes 100
```

Run the complete resumable update or install the background services:

```bash
scripts/manual_update.sh
scripts/install_background_services.sh
scripts/robotcast_service.sh status
```

Production transcription uses faster-whisper `large-v3`, CUDA, FP16, batch 4,
and physical GPU 1 during daytime. OpenAI Whisper remains available as a fallback:

```bash
CUDA_VISIBLE_DEVICES=1 python -m robotcast transcribe --max-episodes 1
```

Switch between daytime GPU 1 and nighttime dual-GPU operation with
`scripts/transcription_mode.sh one|two`; see the operations guide for setup.

Build and validate locally; publication remains explicit:

```bash
python -m robotcast build-site
python -m robotcast validate-site
scripts/robotcast_service.sh publish
```

## Repository

- `robotcast/` — discovery, matching, classification, quality, transcription, and site code
- `scripts/` — resumable manual and service entry points
- `packaging/systemd/` — portable user-service templates and timers
- `config/` — non-secret configuration examples
- `docs/` — operations and design decisions
- `tests/` — regression tests

The local database is `robotics_podcasts.db`. Runtime state, transcripts, locks,
and quarantine live under `.robotcast/`; generated site files live under
`site-dist/`. These paths are intentionally ignored by Git.

## Documentation

- [Operations, scheduling, configuration, and recovery](docs/operations.md)
- [Result-site architecture](docs/result-site-architecture.md)
- [Quality v2 rollout and rollback](docs/quality-v2-rollout.md)
- [Auth-less migration design](docs/authless-migration-plan.md)

Use `python -m robotcast --help` and command-level `--help` for the complete CLI.
