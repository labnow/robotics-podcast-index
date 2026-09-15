from __future__ import annotations

import tempfile
from pathlib import Path

from .api import PublicClient
from .transcripts import _write_transcript


def transcribe_public_audio(conn, directory: Path, max_episodes: int = 1,
                            episode_ids: list[str] | None = None,
                            model_name: str = "large-v3-turbo",
                            device: str = "auto", compute_type: str = "default",
                            request_interval: float = 4.0) -> dict[str, int]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install local ASR with: pip install faster-whisper") from exc
    params: list[object] = []
    where = ["e.relevance_score>=2", "s.audio_url IS NOT NULL", "t.episode_id IS NULL"]
    if episode_ids:
        where.append(f"e.episode_id IN ({','.join('?' for _ in episode_ids)})")
        params.extend(episode_ids)
    params.append(max_episodes)
    rows = conn.execute(f"""SELECT e.episode_id,s.audio_url FROM episodes e
      JOIN episode_sources s USING(episode_id) LEFT JOIN episode_transcripts t USING(episode_id)
      WHERE {' AND '.join(where)} ORDER BY e.quality_score DESC,e.published_at DESC LIMIT ?""", params).fetchall()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    client, root = PublicClient(request_interval=request_interval), directory.resolve()
    counts = {"attempted": 0, "available": 0, "errors": 0}
    for row in rows:
        counts["attempted"] += 1
        try:
            suffix = Path(row["audio_url"].split("?", 1)[0]).suffix or ".audio"
            with tempfile.TemporaryDirectory(prefix="robotcast-audio-") as temporary:
                audio = Path(temporary) / ("episode" + suffix)
                audio.write_bytes(client.get_bytes(row["audio_url"]))
                generated, _ = model.transcribe(str(audio), language="zh", vad_filter=True)
                segments = [{"text": part.text.strip(), "startMs": round(part.start * 1000),
                             "durationMs": round((part.end - part.start) * 1000)} for part in generated]
            path = root / f"{row['episode_id']}.json.gz"
            digest, segment_count, char_count = _write_transcript(path, segments)
            conn.execute("""INSERT INTO episode_transcripts
              (episode_id,status,transcript_path,content_hash,segment_count,char_count,fetched_at,error)
              VALUES(?,'available',?,?,?,?,CURRENT_TIMESTAMP,NULL)""",
              (row["episode_id"], str(path), digest, segment_count, char_count))
            counts["available"] += 1
        except Exception as exc:
            conn.execute("""INSERT INTO episode_transcripts(episode_id,status,error)
              VALUES(?,'error',?) ON CONFLICT(episode_id) DO UPDATE SET status='error',
              last_attempt_at=CURRENT_TIMESTAMP,error=excluded.error""", (row["episode_id"], str(exc)[:1000]))
            counts["errors"] += 1
        conn.commit()
    return counts
