from __future__ import annotations

import collections
import html
import json
import math
import re

ENGLISH = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*(?:\s+[A-Za-z][A-Za-z0-9+.-]*){0,2}")
CHINESE = re.compile(r"[\u4e00-\u9fff]{2,8}")
HTML = re.compile(r"<[^>]+>")
STOP = {"the", "and", "with", "from", "this", "that", "about", "what", "how", "episode",
        "一个", "我们", "他们", "什么", "如何", "为什么", "以及", "可以", "这个", "不是", "就是", "还是", "播客", "节目"}


def phrases(text: str):
    clean = html.unescape(HTML.sub(" ", text or ""))
    for match in ENGLISH.finditer(clean):
        value = " ".join(match.group().lower().split())
        if len(value) >= 3 and value not in STOP:
            yield value
    for block in CHINESE.findall(clean):
        for width in range(2, min(8, len(block)) + 1):
            for i in range(len(block) - width + 1):
                value = block[i:i + width]
                if value not in STOP:
                    yield value


def propose(conn, min_episodes: int = 2, limit: int = 100) -> int:
    rows = conn.execute("""SELECT episode_id,title,description FROM episodes
      WHERE relevance_score >= 2""").fetchall()
    active = {r[0].lower() for r in conn.execute("SELECT term FROM keywords")}
    docs: dict[str, set[str]] = collections.defaultdict(set)
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for row in rows:
        seen = set(phrases(f"{row['title']} {row['description'] or ''}"))
        for term in seen:
            docs[term].add(row["episode_id"])
            if len(examples[term]) < 3:
                examples[term].append(row["title"])
    ranked = sorted(docs, key=lambda t: (len(docs[t]) * math.log2(len(t) + 1), len(t)), reverse=True)
    added = 0
    for term in ranked:
        if len(docs[term]) < min_episodes or term.lower() in active:
            continue
        conn.execute("""INSERT OR IGNORE INTO keywords(term,category,status,source,evidence_count,evidence_json)
          VALUES(?,'evolved','candidate','corpus_phrase',?,?)""",
          (term, len(docs[term]), json.dumps(examples[term], ensure_ascii=False)))
        if conn.execute("SELECT changes()").fetchone()[0]:
            added += 1
        if added >= limit:
            break
    conn.commit()
    return added
