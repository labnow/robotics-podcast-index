from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .api import PublicClient
from .db import upsert_episode

NS = {"podcast": "https://podcastindex.org/namespace/1.0"}


def sync_feeds(conn, client: PublicClient, max_feeds: int = 20,
               max_episodes_per_feed: int = 20) -> dict[str, int]:
    feeds = conn.execute("""SELECT feed_url,count(*) n FROM episode_sources
      WHERE feed_url IS NOT NULL GROUP BY feed_url ORDER BY n DESC LIMIT ?""", (max_feeds,)).fetchall()
    report = {"feeds": 0, "episodes": 0, "new": 0, "errors": 0}
    for feed in feeds:
        try:
            root = ET.fromstring(client.get_bytes(feed["feed_url"]))
            channel = root.find("channel")
            podcast = channel.findtext("title") if channel is not None else None
            items = channel.findall("item") if channel is not None else []
            for item in items[:max_episodes_per_feed]:
                guid = item.findtext("guid") or item.findtext("link") or item.findtext("title") or ""
                enclosure = item.find("enclosure")
                audio = enclosure.get("url") if enclosure is not None else None
                stable = hashlib.sha256((feed["feed_url"] + "\0" + guid).encode()).hexdigest()[:24]
                pub = item.findtext("pubDate")
                try: pub = parsedate_to_datetime(pub).isoformat() if pub else None
                except (TypeError, ValueError): pass
                transcript = item.find("podcast:transcript", NS)
                ep = {"eid": "rss_" + stable, "source": "rss", "sourceEpisodeId": guid,
                    "title": item.findtext("title") or "(untitled)",
                    "description": item.findtext("description") or "", "pubDate": pub,
                    "podcast": {"pid": feed["feed_url"], "title": podcast},
                    "url": item.findtext("link") or audio, "audioUrl": audio,
                    "feedUrl": feed["feed_url"], "rssGuid": guid,
                    "transcriptUrl": transcript.get("url") if transcript is not None else None}
                report["new"] += int(upsert_episode(conn, ep, None))
                report["episodes"] += 1
            conn.commit(); report["feeds"] += 1
        except Exception:
            report["errors"] += 1
    return report
