# Operations

## Service roles

- `robotcast-main@refresh`: monthly Apple discovery, RSS synchronization,
  public-page enrichment, and publisher transcript fetching.
- `robotcast-classify`: resumable Codex classification batches.
- `robotcast-transcribe`: OpenAI Whisper `large-v3`, FP16, physical GPU 1.
- `robotcast-main@publish`: weekly transcript-aware quality scoring, static-site
  validation, and GitHub Pages publication.

Install or refresh the user units with:

```bash
scripts/install_background_services.sh
```

Use the same entry points for ad-hoc operation:

```bash
scripts/robotcast_service.sh refresh
scripts/robotcast_service.sh publish
scripts/robotcast_service.sh status
scripts/robotcast_service.sh logs
```

Runtime configuration lives outside the repository at
`~/.config/robotcast/robotcast.env`. Start from
`config/robotcast.env.example`; never place tokens in the repository.

## Private and generated data

The following stay local and are ignored by Git:

- `robotics_podcasts.db` and SQLite sidecar files;
- `.robotcast/`, including transcript cache, locks, state, and quarantine;
- `site-dist/`, which is published separately to `gh-pages`;
- exports, spreadsheets, archives, caches, and package build metadata.

Repository cleanup artifacts are recoverable from
`.robotcast/quarantine/20260916-repo-clean/`. They can be deleted manually after
the source commit, services, and published site have been verified.

## Publication safety

`scripts/publish_github_pages.sh` validates the generated site before creating a
normal, non-force commit on `gh-pages`. It rejects non-GitHub remotes. The public
artifact must never contain the database, transcript cache, environment files,
locks, logs, or credentials.
