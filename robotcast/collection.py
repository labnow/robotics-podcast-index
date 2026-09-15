from __future__ import annotations

from .db import upsert_episode


def collect_terms(conn, client, terms: list[str], max_pages: int, page_size: int,
                  kind: str = "routine", stale_page_limit: int = 3) -> dict:
    run = conn.execute(
        "INSERT INTO collection_runs(keywords_searched,kind) VALUES(?,?)",
        (len(terms), kind),
    ).lastrowid
    totals = {"run_id": run, "hits": 0, "new": 0, "updated": 0, "new_matches": 0}
    try:
        for number, term in enumerate(terms, 1):
            print(f"[{number}/{len(terms)}] {kind} {term}")
            rank = 0
            term_new = 0
            stale_pages = 0
            deepest = 0
            for page_number, page in enumerate(
                    client.search_pages(term, max_pages, page_size), 1):
                deepest = page_number
                page_new = 0
                for episode in page:
                    rank += 1
                    eid = episode.get("eid") or episode.get("episodeId") or episode.get("id")
                    previous = conn.execute(
                        "SELECT raw_json FROM episodes WHERE episode_id=?", (eid,)
                    ).fetchone() if eid else None
                    had_match = bool(eid and conn.execute(
                        "SELECT 1 FROM episode_matches WHERE episode_id=? AND keyword=?",
                        (eid, term),
                    ).fetchone())
                    is_new = upsert_episode(
                        conn, episode, term, run, rank, page_number, kind
                    )
                    totals["hits"] += 1
                    totals["new"] += int(is_new)
                    term_new += int(is_new)
                    page_new += int(is_new)
                    totals["new_matches"] += int(not had_match)
                    if previous and previous["raw_json"] != conn.execute(
                            "SELECT raw_json FROM episodes WHERE episode_id=?", (eid,)
                    ).fetchone()["raw_json"]:
                        totals["updated"] += 1
                conn.commit()
                stale_pages = stale_pages + 1 if page_new == 0 else 0
                if kind == "backfill" and stale_pages >= stale_page_limit:
                    break
            column = "last_backfill_at" if kind == "backfill" else "last_routine_at"
            conn.execute(f"""INSERT INTO keyword_collection_state(keyword,{column},deepest_page,
              backfill_new_episodes,backfill_hits,consecutive_stale_pages)
              VALUES(?,CURRENT_TIMESTAMP,?,?,?,?)
              ON CONFLICT(keyword) DO UPDATE SET {column}=CURRENT_TIMESTAMP,
              deepest_page=max(deepest_page,excluded.deepest_page),
              backfill_new_episodes=CASE WHEN ?='backfill' THEN excluded.backfill_new_episodes ELSE backfill_new_episodes END,
              backfill_hits=CASE WHEN ?='backfill' THEN excluded.backfill_hits ELSE backfill_hits END,
              consecutive_stale_pages=excluded.consecutive_stale_pages""",
              (term, deepest, term_new, rank, stale_pages, kind, kind))
        conn.execute("""UPDATE collection_runs SET finished_at=CURRENT_TIMESTAMP,hits_seen=?,
          new_episodes=?,updated_episodes=?,new_matches=? WHERE id=?""",
          (totals["hits"], totals["new"], totals["updated"], totals["new_matches"], run))
        conn.commit()
        return totals
    except Exception as exc:
        conn.execute("UPDATE collection_runs SET finished_at=CURRENT_TIMESTAMP,error=? WHERE id=?",
                     (str(exc), run))
        conn.commit()
        raise
