from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request


class ApiError(RuntimeError):
    pass


class PublicClient:
    """Rate-limited client for public, authentication-free resources."""
    def __init__(self, request_interval: float = 2.0, timeout: float = 30, max_retries: int = 3):
        self.request_interval, self.timeout, self.max_retries = request_interval, timeout, max_retries
        self._last_request_at: float | None = None

    def _wait_for_slot(self):
        if self._last_request_at is not None:
            remaining = self.request_interval - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def get_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "robotcast/0.1 (+public-feed-reader)"})
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.max_retries:
                    raise ApiError(f"public HTTP {exc.code}: {url}") from exc
                time.sleep(max(self.request_interval, min(60, 2 ** (attempt + 1))))
            except urllib.error.URLError as exc:
                if attempt == self.max_retries:
                    raise ApiError(f"public network error: {exc.reason}") from exc
                time.sleep(min(60, 2 ** (attempt + 1)))
        raise AssertionError("unreachable")

    def get_json(self, url: str):
        return json.loads(self.get_bytes(url))


class ApplePodcastClient(PublicClient):
    def __init__(self, request_interval: float = 2.0, timeout: float = 30,
                 max_retries: int = 3, country: str = "cn"):
        super().__init__(request_interval, timeout, max_retries)
        self.country = country

    def search_pages(self, keyword: str, max_pages: int = 1, page_size: int = 200):
        query = urllib.parse.urlencode({"term": keyword, "media": "podcast",
            "entity": "podcastEpisode", "country": self.country, "limit": min(200, page_size)})
        response = self.get_json("https://itunes.apple.com/search?" + query)
        yield [normalize_apple_episode(item) for item in response.get("results", [])]

    def search_podcasts(self, name: str, limit: int = 10,
                        country: str | None = None) -> list[dict]:
        query = urllib.parse.urlencode({"term": name, "media": "podcast",
            "entity": "podcast", "country": country or self.country,
            "limit": min(25, limit)})
        response = self.get_json("https://itunes.apple.com/search?" + query)
        return response.get("results", [])


class PublicXiaoyuzhouClient(PublicClient):
    NEXT_DATA = re.compile(rb'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

    def get_episode(self, episode_id: str) -> dict:
        url = f"https://www.xiaoyuzhoufm.com/episode/{urllib.parse.quote(episode_id)}"
        match = self.NEXT_DATA.search(self.get_bytes(url))
        if not match:
            raise ApiError("public episode page did not contain __NEXT_DATA__")
        payload = json.loads(html.unescape(match.group(1).decode()))
        episode = payload.get("props", {}).get("pageProps", {}).get("episode")
        if not isinstance(episode, dict):
            raise ApiError("public episode page did not contain episode metadata")
        episode["source"], episode["sourceEpisodeId"] = "xiaoyuzhou", episode_id
        return episode


def normalize_apple_episode(item: dict) -> dict:
    track_id = str(item.get("trackId") or item.get("episodeGuid") or "")
    stable = re.sub(r"[^A-Za-z0-9_-]", "_", track_id)
    return {"eid": f"apple_{stable}", "source": "apple", "sourceEpisodeId": track_id,
        "title": item.get("trackName") or "(untitled)",
        "description": item.get("description") or item.get("shortDescription") or "",
        "pubDate": item.get("releaseDate"),
        "duration": (item.get("trackTimeMillis") or 0) // 1000 or None,
        "podcast": {"pid": str(item.get("collectionId") or ""), "title": item.get("collectionName")},
        "url": item.get("trackViewUrl"), "audioUrl": item.get("episodeUrl"),
        "feedUrl": item.get("feedUrl"), "rssGuid": item.get("episodeGuid")}
