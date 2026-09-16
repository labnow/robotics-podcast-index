from __future__ import annotations

import tempfile
import time
from pathlib import Path

from .api import PublicClient
from .eligibility import transcription_candidates
from .transcripts import _write_transcript


def transcribe_public_audio(conn, directory: Path, max_episodes: int = 1,
                            episode_ids: list[str] | None = None,
                            model_name: str = "large-v3",
                            device: str = "cuda", fp16: bool = True,
                            language: str | None = None,
                            max_duration_seconds: int | None = None,
                            request_interval: float = 4.0) -> dict[str, int]:
    rows = transcription_candidates(conn, max_episodes, episode_ids,
                                    max_duration_seconds)
    counts = {"attempted": 0, "available": 0, "errors": 0}
    if not rows:
        return counts
    try:
        import whisper
    except ImportError as exc:
        raise RuntimeError("Install local ASR with: pip install openai-whisper") from exc
    model = whisper.load_model(model_name, device=device)
    client, root = PublicClient(request_interval=request_interval), directory.resolve()
    for row in rows:
        counts["attempted"] += 1
        try:
            suffix = Path(row["audio_url"].split("?", 1)[0]).suffix or ".audio"
            with tempfile.TemporaryDirectory(prefix="robotcast-audio-") as temporary:
                audio = Path(temporary) / ("episode" + suffix)
                audio.write_bytes(client.get_bytes(row["audio_url"]))
                started = time.monotonic()
                generated = model.transcribe(str(audio), language=language, fp16=fp16,
                                             verbose=False)
                elapsed = time.monotonic() - started
                segments = [{"text": part["text"].strip(),
                             "startMs": round(part["start"] * 1000),
                             "durationMs": round((part["end"] - part["start"]) * 1000)}
                            for part in generated["segments"]]
            path = root / f"{row['episode_id']}.json.gz"
            digest, segment_count, char_count = _write_transcript(path, segments)
            conn.execute("""INSERT INTO episode_transcripts
              (episode_id,status,transcript_path,content_hash,segment_count,char_count,
               transcription_seconds,audio_duration_seconds,realtime_factor,model_name,
               device,fetched_at,error)
              VALUES(?,'available',?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,NULL)
              ON CONFLICT(episode_id) DO UPDATE SET status='available',
              transcript_path=excluded.transcript_path,content_hash=excluded.content_hash,
              segment_count=excluded.segment_count,char_count=excluded.char_count,
              transcription_seconds=excluded.transcription_seconds,
              audio_duration_seconds=excluded.audio_duration_seconds,
              realtime_factor=excluded.realtime_factor,model_name=excluded.model_name,
              device=excluded.device,
              fetched_at=CURRENT_TIMESTAMP,last_attempt_at=CURRENT_TIMESTAMP,error=NULL""",
              (row["episode_id"], str(path), digest, segment_count, char_count,
               elapsed, row["duration_seconds"],
               row["duration_seconds"] / elapsed if row["duration_seconds"] else None,
               model_name, device))
            counts["available"] += 1
        except Exception as exc:
            conn.execute("""INSERT INTO episode_transcripts(episode_id,status,error)
              VALUES(?,'error',?) ON CONFLICT(episode_id) DO UPDATE SET status='error',
              last_attempt_at=CURRENT_TIMESTAMP,error=excluded.error""", (row["episode_id"], str(exc)[:1000]))
            counts["errors"] += 1
        conn.commit()
    return counts
