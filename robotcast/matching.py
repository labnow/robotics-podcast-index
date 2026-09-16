from __future__ import annotations

import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone


_PREFIX = re.compile(
    r"^(?:ep(?:isode)?\.?|vol\.?|no\.?|第)\s*\d+\s*(?:期|集|回)?\s*[:：.、|｜_-]*\s*",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)


def normalize_title(value: str | None) -> str:
    """Normalize display variations without translating or reordering words."""
    value = unicodedata.normalize("NFKC", value or "").casefold().strip()
    value = _PREFIX.sub("", value)
    return _NON_WORD.sub("", value)


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urllib.parse.urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return value.strip()
    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    path = urllib.parse.unquote(parsed.path).rstrip("/") or "/"
    return urllib.parse.urlunsplit(("https", host + port, path, parsed.query, ""))


def duration_close(left: int | None, right: int | None) -> bool:
    if left is None or right is None:
        return True
    return abs(left - right) <= max(90, round(max(left, right) * 0.05))


def _date_distance_days(left: str | None, right: str | None) -> float | None:
    if not left or not right:
        return None
    try:
        def parse(value: str) -> datetime:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        return abs((parse(left) - parse(right)).total_seconds()) / 86400
    except ValueError:
        return None


@dataclass(frozen=True)
class MatchResult:
    episode_id: str | None
    status: str
    method: str
    score: float
    candidates: tuple[tuple[str, float], ...] = ()


def find_episode_match(conn, ep: dict) -> MatchResult:
    """Return only high-confidence links; uncertain collisions enter review."""
    media = ep.get("media") or {}
    audio = normalize_url(ep.get("audioUrl") or (media.get("source") or {}).get("url")
                          or (ep.get("enclosure") or {}).get("url"))
    guid = str(ep.get("rssGuid") or "").strip() or None
    feed = normalize_url(ep.get("feedUrl"))

    # Xiaoyuzhou-derived feeds commonly use the canonical episode ID as the RSS
    # GUID. Prefer that exact identity before audio matching because publishers
    # may intentionally reuse one audio file for two separately published items.
    if guid:
        row = conn.execute(
            "SELECT episode_id FROM episodes WHERE episode_id=?", (guid,)
        ).fetchone()
        if row:
            return MatchResult(row[0], "matched", "exact_episode_id", 1.0)
        rows = conn.execute(
            "SELECT DISTINCT episode_id FROM episode_sources WHERE source_episode_id=?",
            (guid,),
        ).fetchall()
        if len(rows) == 1:
            return MatchResult(rows[0][0], "matched", "exact_source_id", 1.0)

    if audio:
        rows = conn.execute(
            "SELECT DISTINCT episode_id FROM episode_sources WHERE normalized_audio_url=?",
            (audio,),
        ).fetchall()
        if len(rows) == 1:
            return MatchResult(rows[0][0], "matched", "audio_url", 1.0)
        if len(rows) > 1:
            return MatchResult(None, "ambiguous", "audio_url", 1.0,
                               tuple((row[0], 1.0) for row in rows))

    if guid:
        rows = conn.execute(
            "SELECT DISTINCT episode_id,feed_url FROM episode_sources WHERE rss_guid=?", (guid,)
        ).fetchall()
        if feed:
            aliases = {feed}
            aliases.update(row[0] for row in conn.execute(
                "SELECT alias_url FROM feed_aliases WHERE canonical_url=? UNION SELECT canonical_url FROM feed_aliases WHERE alias_url=?",
                (feed, feed),
            ))
            scoped = [row for row in rows if not row[1] or normalize_url(row[1]) in aliases]
            rows = scoped or rows
        if len(rows) == 1:
            return MatchResult(rows[0][0], "matched", "rss_guid", .99)
        if len(rows) > 1:
            return MatchResult(None, "ambiguous", "rss_guid", .99,
                               tuple((row[0], .99) for row in rows))

    title = normalize_title(ep.get("title"))
    podcast = ep.get("podcast") or {}
    show = normalize_title(podcast.get("title") or ep.get("podcastTitle"))
    if not title:
        return MatchResult(None, "unmatched", "none", 0)
    rows = conn.execute("""SELECT episode_id,published_at,duration_seconds,
      normalized_podcast_name FROM episodes WHERE normalized_title=?""", (title,)).fetchall()
    scored = []
    for row in rows:
        same_show = bool(show and row["normalized_podcast_name"] == show)
        date_days = _date_distance_days(row["published_at"], ep.get("pubDate") or ep.get("publishedAt"))
        close_duration = duration_close(row["duration_seconds"], ep.get("duration"))
        score = .58 + (.22 if same_show else 0) + (.12 if date_days is not None and date_days <= 1 else 0) + (.08 if close_duration else 0)
        if same_show and (date_days is None or date_days <= 1) and close_duration:
            scored.append((row["episode_id"], round(score, 3)))
    scored.sort(key=lambda item: (-item[1], item[0]))
    if len(scored) == 1 and scored[0][1] >= .88:
        return MatchResult(scored[0][0], "matched", "normalized_metadata", scored[0][1], tuple(scored))
    if scored:
        return MatchResult(None, "ambiguous", "normalized_metadata", scored[0][1], tuple(scored))
    return MatchResult(None, "unmatched", "normalized_metadata", 0)
