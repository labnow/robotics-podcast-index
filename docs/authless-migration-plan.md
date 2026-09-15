# Auth-less Acquisition Migration

## Status

Implemented on 2026-09-15. Authenticated Xiaoyuzhou code and the saved local
refresh token were removed. Smoke tests completed successfully:

- anonymous refresh of episode `69c07cc2719b26db81d9720a`;
- Apple search for `具身智能`: 10 hits, 6 linked to historical records, 4 new;
- one public RSS feed: 5 recent items, 2 linked, 3 new;
- 4,077 historical records backfilled with Xiaoyuzhou source provenance;
- faster-whisper 1.2.1 installed in the requested uv environment and
  `large-v3-turbo` confirmed as an available model name.

The model weights are intentionally not downloaded until an explicit episode
transcription is requested.

## Decision

Robotcast must not use Xiaoyuzhou account credentials or authenticated private
APIs. Existing collected data remains valid historical input; all future
acquisition is through public webpages, Apple Search, public RSS, and public
audio/transcript URLs.

## Migration phases

1. **Safety boundary** — remove authentication commands and private API calls;
   retain no runtime path that reads `.robotcast/refresh_token`.
2. **Public enrichment** — parse the `__NEXT_DATA__` payload of a known public
   Xiaoyuzhou episode page for metadata, engagement, shownotes, and audio.
3. **Auth-less discovery** — replace keyword search with Apple podcast-episode
   search and retain feed/audio/Apple identifiers and provenance.
4. **Source-neutral identity** — retain legacy Xiaoyuzhou IDs, introduce source
   records, and use stable Apple/RSS IDs for newly discovered episodes.
5. **RSS monitoring** — register feeds discovered by Apple and poll them for
   new episodes. Prefer publisher-provided `podcast:transcript` links.
6. **Eligibility** — preserve age-adjusted popularity where anonymous
   Xiaoyuzhou engagement exists; treat missing engagement as unknown and use
   recency, keyword discovery, and relevant-show history instead.
7. **Transcription** — download only public audio for shortlisted relevant
   episodes and transcribe locally with faster-whisper `large-v3-turbo`.
8. **Validation** — compare auth-less discovery against the historical corpus,
   test deduplication, and keep requests conservatively rate limited.

## Rollout rules

- No scheduled collection and no implicit production deployment.
- No private or paid media.
- Never map missing play counts to zero.
- Audio and transcripts remain private local cache files and are not published.
- Existing database rows and classification results are migrated additively.
- Each network stage is bounded, resumable, and explicitly invoked.

## Command surface

```text
collect              Apple auth-less episode keyword search
refresh-public       Refresh known Xiaoyuzhou public episode pages
sync-feeds           Poll registered public RSS feeds
fetch-transcripts    Fetch public RSS transcript files only
transcribe           Transcribe selected public audio locally
classify             Existing relevance classifier
score-quality        Existing versioned 0-10 quality classifier
build-site           Existing static result site
```

## Acceptance criteria

- No production module imports or resolves a Xiaoyuzhou access/refresh token.
- A keyword collection smoke test succeeds without credentials.
- A known Xiaoyuzhou episode can be enriched anonymously.
- New Apple/RSS episodes retain source provenance and public media URLs.
- Existing unit tests and new auth-less acquisition tests pass.
- Local Whisper is optional at installation time; invoking it without its
  dependency gives a clear installation instruction.
