# Result Site Architecture

## Status

Implemented local-service design with scheduled collection and GitHub Pages publishing.

## Goals

- Publish the curated robotics and embodied-AI podcast corpus as a simple result
  browser.
- Host the generated site on GitHub Pages.
- Run auth-less discovery monthly while retaining ad-hoc triggers.
- Keep the initial page download small and fast, including on mobile networks.
- Keep credentials, the SQLite database, collection history, and classifier
  internals private.

## System boundary

The website is a read-only static projection of the local database. It does not
call Xiaoyuzhou, run an LLM, or access SQLite at runtime.

```text
Xiaoyuzhou API
      |
      v
Apple/RSS/public web ---> robotics_podcasts.db
                              |
Codex classifier ------------+
                              |
                              v
                       build-result-site
                              |
                              v
                          site-dist/
                              |
                              v
                         GitHub Pages
```

This separation makes the public site inexpensive, cacheable, and independent
of API availability.

## Recommended technology

Use generated HTML, CSS, and a small amount of browser JavaScript. A React or
server-rendered application is unnecessary for the first version.

Benefits:

- no application server or public database;
- little JavaScript and no client framework bundle;
- straightforward cache behavior;
- a deployable directory that EdgeOne Pages can publish directly;
- few dependencies to maintain.

The existing Python package should own site-data generation because it already
understands the database schema and filtering rules.

## Published files

```text
site-dist/
├── index.html
├── assets/
│   ├── site.css
│   └── site.js
└── data/
    ├── index.json
    └── details/
        ├── <episode-id>.json
        └── ...
```

`index.json` is the compact browse-and-search index. It should contain only:

- episode ID;
- title and podcast name;
- publication date and duration;
- play and comment counts;
- age-adjusted popularity percentile;
- relevance and quality scores;
- topics;
- Xiaoyuzhou URL.

Longer fields are stored in one detail file per episode and fetched only when a
visitor expands that result:

- cleaned description;
- classification reason;
- quality reason;
- matched keywords, if we decide they are useful to public readers.

This avoids downloading hundreds of descriptions during the initial visit.
Static assets and episode details should use content hashes or cache-friendly
filenames when implemented. `index.html` and `index.json` should have shorter
cache lifetimes so a newly deployed corpus is discovered promptly.

## Public data policy

The generated site should use the same clean-corpus policy as the current XLSX:

- relevance score at least 2;
- quality score at least 2;
- positive play count when engagement is known; unknown is not treated as zero;
- sorted by quality descending, then publication date descending.

Only explicitly selected fields are copied into `site-dist/`. The following
must never be published:

- `.robotcast/` and Xiaoyuzhou authentication tokens;
- `robotics_podcasts.db` and its observation/history tables;
- Codex prompts, temporary responses, locks, or logs;
- unpublished low-quality and irrelevant records.

The site generator should fail if output paths escape `site-dist/` or if a
credential/database file appears in the deployable directory.

## Page experience

The first screen is a working results view, not a marketing landing page. It
contains:

- corpus title, last-updated time, and curated episode count;
- a local text-search field;
- topic, quality, and publication-date filters;
- quality/date sorting;
- the first useful episode results.

Each result shows its title, podcast, date, duration, engagement, popularity,
topics, quality, and a direct Xiaoyuzhou link. Description and classifier
rationale appear in an expandable detail region and are loaded on demand.

Desktop should use a compact readable list rather than a 17-column spreadsheet.
Mobile should use stacked cards with touch-friendly controls. The first version
does not require accounts, analytics, comments, a backend, or an online semantic
search service.

Search is intentionally local and lexical across title, podcast name, and
topics. With hundreds or a few thousand curated records, a dedicated search
library is not necessary. This website search is for browsing the already
classified corpus; it is separate from the collector's discovery mechanism.

## Update and deployment workflow

Three local service roles share SQLite as the durable coordination boundary:

```text
1. main             monthly discovery/RSS/enrichment and periodic site publication
2. classifier       bounded, resumable Codex classification batches
3. transcript       one OpenAI Whisper large-v3 FP16 process on GPU 1
```

The same paths support ad-hoc operation:

```bash
scripts/robotcast_service.sh refresh
scripts/robotcast_service.sh publish
```

The exact `update-data` orchestration can reuse `collect`, `sync-feeds`,
`refresh-public`, `classify`, and keyword-evolution commands. Existing request timers,
retry behavior, database deduplication, content hashes, and classifier queue
resumption remain in effect.

After the individual stages are reliable, add a small agent-facing harness such
as `scripts/update_result_site.sh`. It should stop before production deployment
unless deployment is explicitly requested. Generation must be safe to rerun;
overlapping collection results update existing records rather than duplicating
them.

## GitHub Pages deployment

The local main service builds and validates `site-dist/`, then copies only those
static artifacts into a temporary checkout of the target repository's `gh-pages`
branch. A normal non-force push publishes changed content. The 533 MB SQLite database,
transcript cache, locks, logs, Codex execution, and GPU processing never enter GitHub.

The repository URL is read from the private user-service environment as
`ROBOTCAST_PAGES_REMOTE`. If it is absent, the scheduled pass still scores, builds,
and validates locally but safely skips the external publication.

## Validation gates

Before a production deployment, the build should verify:

1. every index record has a corresponding detail file;
2. no episode violates the relevance, quality, or play-count policy;
3. ordering is quality descending and then publication date descending;
4. episode IDs and Xiaoyuzhou URLs are valid and unique;
5. JSON, HTML, and referenced assets are present and parseable;
6. the initial index and total generated size are reported;
7. no database, token, log, or temporary classifier file is present;
8. the site works without network access except for opening external episode
   links and fetching its own static files.

Deployment should happen only after these checks pass. A failed deployment must
not affect the local database or the previously published production version.

## Suggested first implementation slice

1. Add a deterministic `build-site` command and its tests.
2. Generate the compact index and lazy detail files.
3. Build the single responsive result page with search, filters, and expandable
   details.
4. Add output-size and data-policy validation.
5. Preview and visually verify desktop and mobile layouts.
6. Configure the GitHub repository to serve its `gh-pages` branch.
7. Add the optional agent-facing update harness after the individual commands
   have been exercised successfully.

## Deferred decisions

These do not block the first implementation:

- custom domain versus the default GitHub Pages domain;
- whether matched discovery keywords should be public;
- analytics or privacy-preserving traffic measurement;
- URL-addressable episode detail pages for sharing and search-engine indexing.
