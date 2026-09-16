from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

from .matching import normalize_title, normalize_url


def seed_historical_registry(conn) -> int:
    """Create one resumable registry row per historically relevant show."""
    before = conn.total_changes
    conn.execute("""INSERT OR IGNORE INTO podcast_registry
      (podcast_key,podcast_name,normalized_name)
      SELECT 'name:' || normalized_podcast_name,podcast_name,normalized_podcast_name
      FROM episodes WHERE relevance_score>=2 AND podcast_name IS NOT NULL
      GROUP BY normalized_podcast_name
      HAVING normalized_podcast_name<>''""")
    seeded = conn.total_changes - before
    # Early versions keyed rows by source podcast ID. Consolidate those rows
    # into the normalized show key while retaining the strongest result.
    rank = {"matched": 5, "ambiguous": 4, "not_found": 3, "error": 2, "pending": 1}
    groups: dict[str, list] = {}
    for row in conn.execute("SELECT * FROM podcast_registry"):
        groups.setdefault(row["normalized_name"], []).append(row)
    for normalized, rows in groups.items():
        target_key = "name:" + normalized
        target = next((row for row in rows if row["podcast_key"] == target_key), None)
        if target is None:
            continue
        best = max(rows, key=lambda row: (rank[row["match_status"]],
                                          row["match_score"] or 0,
                                          row["last_attempt_at"] or ""))
        if best["podcast_key"] != target_key:
            conn.execute("""UPDATE podcast_registry SET apple_collection_id=?,apple_url=?,
              feed_url=?,match_status=?,match_score=?,candidates_json=?,last_attempt_at=?,
              error=?,updated_at=CURRENT_TIMESTAMP WHERE podcast_key=?""",
              (best["apple_collection_id"], best["apple_url"], best["feed_url"],
               best["match_status"], best["match_score"], best["candidates_json"],
               best["last_attempt_at"], best["error"], target_key))
        conn.execute("DELETE FROM podcast_registry WHERE normalized_name=? AND podcast_key<>?",
                     (normalized, target_key))
    conn.commit()
    return seeded


def _candidate_score(expected: str, candidate: dict) -> float:
    actual = normalize_title(candidate.get("collectionName"))
    if not actual:
        return 0
    if expected == actual:
        return 1.0
    ratio = SequenceMatcher(None, expected, actual).ratio()
    # Apple titles often expand a short brand (for example "18VC") with a
    # descriptive subtitle. A substantial contained brand is strong evidence.
    contained = min(len(expected), len(actual)) >= 4 and (expected in actual or actual in expected)
    return round(max(ratio, .9 if contained else 0), 3)


def _unique_candidates(pairs: list[tuple[dict, float]]) -> list[tuple[dict, float]]:
    """Collapse duplicate Apple listings that point at the same canonical feed."""
    unique: dict[str, tuple[dict, float]] = {}
    for item, score in pairs:
        feed = normalize_url(item.get("feedUrl"))
        key = feed or f"apple:{item.get('collectionId')}"
        previous = unique.get(key)
        if previous is None or score > previous[1]:
            unique[key] = (item, score)
    return sorted(unique.values(), key=lambda pair: pair[1], reverse=True)


def build_historical_registry(conn, client, max_shows: int = 20,
                              retry_errors: bool = False,
                              retry_not_found: bool = False) -> dict[str, int]:
    seed_historical_registry(conn)
    selected = ["pending"]
    if retry_errors:
        selected.append("error")
    if retry_not_found:
        selected.append("not_found")
    statuses = "(" + ",".join("?" for _ in selected) + ")"
    rows = conn.execute(f"""SELECT * FROM podcast_registry
      WHERE match_status IN {statuses} ORDER BY podcast_name COLLATE NOCASE LIMIT ?""",
      (*selected, max_shows)).fetchall()
    report = {"attempted": 0, "matched": 0, "ambiguous": 0, "not_found": 0, "errors": 0}
    for row in rows:
        report["attempted"] += 1
        try:
            candidates = client.search_podcasts(row["podcast_name"], 10)
            has_plausible = any(_candidate_score(row["normalized_name"], item) >= .72
                                for item in candidates)
            if getattr(client, "country", "us") != "us" and not has_plausible:
                us = client.search_podcasts(row["podcast_name"], 10, country="us")
                known = {item.get("collectionId") for item in candidates}
                candidates.extend(item for item in us if item.get("collectionId") not in known)
            ranked = sorted(((item, _candidate_score(row["normalized_name"], item))
                             for item in candidates), key=lambda pair: pair[1], reverse=True)
            plausible = _unique_candidates(
                [(item, score) for item, score in ranked if score >= .72])
            exact = [(item, score) for item, score in plausible if score == 1]
            chosen = exact[0] if len(exact) == 1 else (plausible[0] if len(plausible) == 1 else None)
            status = "matched" if chosen else ("ambiguous" if plausible else "not_found")
            values = chosen[0] if chosen else {}
            score = chosen[1] if chosen else (plausible[0][1] if plausible else None)
            compact = [{"collectionId": item.get("collectionId"),
                        "collectionName": item.get("collectionName"),
                        "feedUrl": item.get("feedUrl"), "score": candidate_score}
                       for item, candidate_score in plausible[:5]]
            feed = normalize_url(values.get("feedUrl"))
            conn.execute("""UPDATE podcast_registry SET apple_collection_id=?,apple_url=?,
              feed_url=?,match_status=?,match_score=?,candidates_json=?,last_attempt_at=CURRENT_TIMESTAMP,
              error=NULL,updated_at=CURRENT_TIMESTAMP WHERE podcast_key=?""",
              (str(values.get("collectionId")) if values.get("collectionId") else None,
               values.get("collectionViewUrl"), feed, status, score,
               json.dumps(compact, ensure_ascii=False), row["podcast_key"]))
            if feed:
                conn.execute("INSERT OR IGNORE INTO feed_aliases(alias_url,canonical_url,source) VALUES(?,?,?)",
                             (feed, feed, "apple_registry"))
            report[status] += 1
        except Exception as exc:
            conn.execute("""UPDATE podcast_registry SET match_status='error',error=?,
              last_attempt_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE podcast_key=?""",
              (str(exc)[:1000], row["podcast_key"]))
            report["errors"] += 1
        conn.commit()
    return report


def _feed_evidence(payload: bytes, historical_titles: set[str],
                   historical_audio: set[str], max_items: int) -> dict:
    root = ET.fromstring(payload)
    channel = root.find("channel")
    if channel is None:
        channel = root.find("{http://www.w3.org/2005/Atom}channel")
    items = channel.findall("item") if channel is not None else []
    title_hits: list[str] = []
    audio_hits = 0
    for item in items[:max_items]:
        title = normalize_title(item.findtext("title"))
        if title and title in historical_titles:
            title_hits.append(item.findtext("title") or title)
        enclosure = item.find("enclosure")
        audio = normalize_url(enclosure.get("url") if enclosure is not None else None)
        audio_hits += int(bool(audio and audio in historical_audio))
    return {"titleHits": len(set(title_hits)), "audioHits": audio_hits,
            "examples": list(dict.fromkeys(title_hits))[:3], "itemsChecked": min(len(items), max_items)}


def resolve_ambiguous_registry(conn, client, max_shows: int = 10,
                               max_items: int = 100) -> dict[str, int]:
    """Resolve show-name collisions only with unique historical episode evidence."""
    rows = conn.execute("""SELECT * FROM podcast_registry WHERE match_status='ambiguous'
      ORDER BY podcast_name COLLATE NOCASE LIMIT ?""", (max_shows,)).fetchall()
    report = {"attempted": 0, "resolved": 0, "unresolved": 0, "errors": 0}
    for row in rows:
        report["attempted"] += 1
        historical_titles = {item[0] for item in conn.execute("""SELECT normalized_title
          FROM episodes WHERE normalized_podcast_name=? AND normalized_title<>''""",
          (row["normalized_name"],))}
        historical_audio = {normalize_url(item[0]) for item in conn.execute("""SELECT s.audio_url
          FROM episode_sources s JOIN episodes e USING(episode_id)
          WHERE e.normalized_podcast_name=? AND s.audio_url IS NOT NULL""",
          (row["normalized_name"],))}
        candidates = json.loads(row["candidates_json"])
        unique_candidates = [item for item, _ in _unique_candidates(
            [(item, float(item.get("score") or 0)) for item in candidates])]
        scored = []
        for candidate in unique_candidates:
            feed = candidate.get("feedUrl")
            if not feed:
                continue
            try:
                evidence = _feed_evidence(client.get_bytes(feed), historical_titles,
                                          historical_audio, max_items)
                candidate["evidence"] = evidence
                strength = evidence["audioHits"] * 1000 + evidence["titleHits"]
                scored.append((strength, candidate))
            except Exception as exc:
                candidate["evidenceError"] = str(exc)[:300]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        best = scored[0] if scored else None
        runner_up = scored[1][0] if len(scored) > 1 else 0
        tied = [pair for pair in scored if best and pair[0] == best[0]]
        mirrored = bool(len(tied) > 1 and best and best[0] > 0 and all(
            item[1]["evidence"]["audioHits"] >= 1 or
            item[1]["evidence"]["titleHits"] >= 2 for item in tied))
        # One audio identity or two exact episode-title identities are enough;
        # a single title is accepted only when every other candidate has none.
        resolved = mirrored or bool(best and best[0] > runner_up and
            (best[1]["evidence"]["audioHits"] >= 1 or
             best[1]["evidence"]["titleHits"] >= 2 or
             (best[1]["evidence"]["titleHits"] == 1 and runner_up == 0)))
        if resolved:
            candidate = (next((item[1] for item in tied
                               if "feed.xyzfm.space" in item[1].get("feedUrl", "")),
                              best[1]) if mirrored else best[1])
            feed = normalize_url(candidate["feedUrl"])
            conn.execute("""UPDATE podcast_registry SET apple_collection_id=?,feed_url=?,
              match_status='matched',match_score=1.0,candidates_json=?,error=NULL,
              updated_at=CURRENT_TIMESTAMP WHERE podcast_key=?""",
              (str(candidate.get("collectionId")), feed,
               json.dumps(candidates, ensure_ascii=False), row["podcast_key"]))
            conn.execute("INSERT OR IGNORE INTO feed_aliases(alias_url,canonical_url,source) VALUES(?,?,?)",
                         (feed, feed, "episode_evidence"))
            if mirrored:
                for _, mirror in tied:
                    alias = normalize_url(mirror.get("feedUrl"))
                    if alias:
                        conn.execute("""INSERT OR IGNORE INTO feed_aliases
                          (alias_url,canonical_url,source) VALUES(?,?,?)""",
                          (alias, feed, "mirrored_episode_evidence"))
            report["resolved"] += 1
        else:
            conn.execute("""UPDATE podcast_registry SET candidates_json=?,
              updated_at=CURRENT_TIMESTAMP WHERE podcast_key=?""",
              (json.dumps(candidates, ensure_ascii=False), row["podcast_key"]))
            report["unresolved"] += 1
            report["errors"] += int(bool(candidates) and not scored)
        conn.commit()
    return report


def registry_coverage_report(conn) -> dict[str, int | float]:
    """Summarize historical-show and episode recovery without network access."""
    shows = conn.execute("SELECT count(*) FROM podcast_registry").fetchone()[0]
    statuses = dict(conn.execute(
        "SELECT match_status,count(*) FROM podcast_registry GROUP BY match_status"))
    relevant = conn.execute(
        "SELECT count(*) FROM episodes WHERE relevance_score>=2").fetchone()[0]
    relevant_with_rss = conn.execute("""SELECT count(*) FROM episodes e
      WHERE e.relevance_score>=2 AND EXISTS (
        SELECT 1 FROM episode_sources s
        WHERE s.episode_id=e.episode_id AND s.source='rss')""").fetchone()[0]
    rss_episodes = conn.execute(
        "SELECT count(DISTINCT episode_id) FROM episode_sources WHERE source='rss'"
    ).fetchone()[0]
    rss_unclassified = conn.execute("""SELECT count(DISTINCT e.episode_id)
      FROM episodes e JOIN episode_sources s USING(episode_id)
      WHERE s.source='rss' AND e.relevance_score IS NULL""").fetchone()[0]
    pending_reviews = conn.execute(
        "SELECT count(*) FROM match_review_queue WHERE status='pending'"
    ).fetchone()[0]
    return {
        "historical_shows": shows,
        "matched_shows": statuses.get("matched", 0),
        "ambiguous_shows": statuses.get("ambiguous", 0),
        "not_found_shows": statuses.get("not_found", 0),
        "show_coverage_percent": round(100 * statuses.get("matched", 0) / shows, 1)
        if shows else 0.0,
        "historical_relevant_episodes": relevant,
        "relevant_episodes_with_rss": relevant_with_rss,
        "episode_identity_coverage_percent": round(100 * relevant_with_rss / relevant, 1)
        if relevant else 0.0,
        "rss_episodes": rss_episodes,
        "rss_unclassified": rss_unclassified,
        "pending_match_reviews": pending_reviews,
    }
