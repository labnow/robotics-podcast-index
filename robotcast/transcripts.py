from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .api import ApiError, PublicClient


def _write_transcript(path: Path, segments: list[dict]) -> tuple[str, int, int]:
    cleaned = []
    chars = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        item = {"text": text}
        for key in ("startMs", "durationMs"):
            if isinstance(segment.get(key), (int, float)):
                item[key] = segment[key]
        cleaned.append(item)
        chars += len(text)
    encoded = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            stream.write(encoded)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return digest, len(cleaned), chars


def transcript_excerpt(path: str | None, max_chars: int = 14000,
                       samples: int = 8) -> str | None:
    if not path or not Path(path).is_file():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        segments = json.load(stream)
    if not segments:
        return None
    bucket = max(1, len(segments) // samples)
    allowance = max_chars // samples
    excerpts = []
    for start in range(0, len(segments), bucket):
        selected = segments[start:start + bucket]
        text = "".join(item.get("text", "") for item in selected)
        timestamp = selected[0].get("startMs") if selected else None
        prefix = f"[{int(timestamp) // 60000}m] " if isinstance(timestamp, (int, float)) else ""
        excerpts.append(prefix + text[:allowance])
        if len(excerpts) >= samples:
            break
    return "\n".join(excerpts)[:max_chars]


def fetch_public_transcripts(conn, client: PublicClient, directory: Path,
                             max_episodes: int = 10,
                             episode_ids: list[str] | None = None) -> dict[str, int]:
    params: list[object] = []
    where = ["s.transcript_url IS NOT NULL", "t.episode_id IS NULL"]
    if episode_ids:
        where.append(f"e.episode_id IN ({','.join('?' for _ in episode_ids)})")
        params.extend(episode_ids)
    params.append(max_episodes)
    rows = conn.execute(f"""SELECT e.episode_id,s.transcript_url FROM episodes e
      JOIN episode_sources s USING(episode_id) LEFT JOIN episode_transcripts t USING(episode_id)
      WHERE {' AND '.join(where)} ORDER BY e.relevance_score DESC,e.published_at DESC LIMIT ?""", params).fetchall()
    report = {"attempted": 0, "available": 0, "errors": 0}
    for row in rows:
        report["attempted"] += 1
        try:
            raw = client.get_bytes(row["transcript_url"])
            text = raw.decode("utf-8", errors="replace")
            lines = [line.strip() for line in text.splitlines() if line.strip()
                     and "-->" not in line and not line.strip().isdigit()
                     and not line.startswith(("WEBVTT", "NOTE", "{"))]
            path = directory.resolve() / f"{row['episode_id']}.json.gz"
            digest, count, chars = _write_transcript(path, [{"text": line} for line in lines])
            conn.execute("""INSERT INTO episode_transcripts
              (episode_id,status,transcript_path,content_hash,segment_count,char_count,fetched_at,error)
              VALUES(?,'available',?,?,?,?,CURRENT_TIMESTAMP,NULL)""",
              (row["episode_id"], str(path), digest, count, chars))
            report["available"] += 1
        except Exception as exc:
            conn.execute("""INSERT INTO episode_transcripts(episode_id,status,error)
              VALUES(?,'error',?) ON CONFLICT(episode_id) DO UPDATE SET status='error',
              last_attempt_at=CURRENT_TIMESTAMP,error=excluded.error""", (row["episode_id"], str(exc)[:1000]))
            report["errors"] += 1
        conn.commit()
    return report
