from __future__ import annotations

import tempfile
import time
from pathlib import Path

from .api import PublicClient
from .eligibility import claim_transcription_candidate, release_transcription_claim
from .transcripts import _write_transcript


def transcribe_public_audio(conn, directory: Path, max_episodes: int = 1,
                            episode_ids: list[str] | None = None,
                            model_name: str = "large-v3",
                            device: str = "cuda", fp16: bool = True,
                            language: str | None = None,
                            max_duration_seconds: int | None = None,
                            request_interval: float = 4.0,
                            worker_id: str = "default",
                            lease_minutes: int = 360,
                            backend: str = "openai-whisper",
                            batch_size: int = 4,
                            compute_type: str = "float16") -> dict[str, int]:
    counts = {"attempted": 0, "available": 0, "errors": 0}
    if backend == "faster-whisper":
        try:
            from faster_whisper import BatchedInferencePipeline, WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install faster-whisper in the selected runtime environment") from exc
        base_model = WhisperModel(model_name, device=device, compute_type=compute_type)
        model = BatchedInferencePipeline(model=base_model)
    else:
        try:
            import whisper
        except ImportError as exc:
            raise RuntimeError("Install local ASR with: pip install openai-whisper") from exc
        model = whisper.load_model(model_name, device=device)
    client, root = PublicClient(request_interval=request_interval), directory.resolve()
    for _ in range(max_episodes):
        row = claim_transcription_candidate(conn, worker_id, episode_ids,
                                            max_duration_seconds, lease_minutes)
        if row is None:
            break
        counts["attempted"] += 1
        try:
            suffix = Path(row["audio_url"].split("?", 1)[0]).suffix or ".audio"
            with tempfile.TemporaryDirectory(prefix="robotcast-audio-") as temporary:
                audio = Path(temporary) / ("episode" + suffix)
                audio.write_bytes(client.get_bytes(row["audio_url"]))
                started = time.monotonic()
                if backend == "faster-whisper":
                    generated_segments, _ = model.transcribe(
                        str(audio), language=language, batch_size=batch_size,
                        without_timestamps=False, word_timestamps=True)
                    raw_segments = list(generated_segments)
                    segments = [{"text": part.text.strip(),
                                 "startMs": round(part.start * 1000),
                                 "durationMs": round((part.end - part.start) * 1000)}
                                for part in raw_segments]
                else:
                    generated = model.transcribe(str(audio), language=language, fp16=fp16,
                                                 verbose=False)
                    segments = [{"text": part["text"].strip(),
                                 "startMs": round(part["start"] * 1000),
                                 "durationMs": round((part["end"] - part["start"]) * 1000)}
                                for part in generated["segments"]]
                elapsed = time.monotonic() - started
            path = root / f"{row['episode_id']}.json.gz"
            digest, segment_count, char_count = _write_transcript(path, segments)
            conn.execute("""INSERT INTO episode_transcripts
              (episode_id,status,transcript_path,content_hash,segment_count,char_count,
               transcription_seconds,audio_duration_seconds,realtime_factor,model_name,
               device,transcription_backend,compute_type,batch_size,fetched_at,error)
              VALUES(?,'available',?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,NULL)
              ON CONFLICT(episode_id) DO UPDATE SET status='available',
              transcript_path=excluded.transcript_path,content_hash=excluded.content_hash,
              segment_count=excluded.segment_count,char_count=excluded.char_count,
              transcription_seconds=excluded.transcription_seconds,
              audio_duration_seconds=excluded.audio_duration_seconds,
              realtime_factor=excluded.realtime_factor,model_name=excluded.model_name,
              device=excluded.device,transcription_backend=excluded.transcription_backend,
              compute_type=excluded.compute_type,batch_size=excluded.batch_size,
              fetched_at=CURRENT_TIMESTAMP,last_attempt_at=CURRENT_TIMESTAMP,error=NULL""",
              (row["episode_id"], str(path), digest, segment_count, char_count,
               elapsed, row["duration_seconds"],
               row["duration_seconds"] / elapsed if row["duration_seconds"] else None,
               model_name, device, backend, compute_type if backend == "faster-whisper" else "float16" if fp16 else "float32",
               batch_size if backend == "faster-whisper" else 1))
            counts["available"] += 1
        except Exception as exc:
            conn.execute("""INSERT INTO episode_transcripts(episode_id,status,error)
              VALUES(?,'error',?) ON CONFLICT(episode_id) DO UPDATE SET status='error',
              last_attempt_at=CURRENT_TIMESTAMP,error=excluded.error""", (row["episode_id"], str(exc)[:1000]))
            counts["errors"] += 1
        conn.commit()
        release_transcription_claim(conn, row["episode_id"], worker_id)
    return counts
