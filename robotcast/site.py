from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from pathlib import Path
from urllib.parse import urlparse

from .export import TAG
from .quality import RUBRIC_VERSION
from .quality_policy import active_quality_measure, quality_sql

EPISODE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
BLOCKED_NAMES = {"robotics_podcasts.db", ".robotcast", "refresh_token",
                 "classify.lock", "transcribe.lock"}
CHUNK_SIZE = 50


def _clean_html(value: str | None, max_chars: int | None = None) -> str:
    cleaned = " ".join(unescape(TAG.sub(" ", value or "")).split())
    if max_chars is not None and len(cleaned) > max_chars:
        return cleaned[:max_chars].rstrip() + "…"
    return cleaned


def _inside(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Output path escapes site directory: {candidate}")
    return candidate


def _rows(conn):
    measure = active_quality_measure(conn)
    policy = quality_sql(measure)
    score, reason = policy["score"], policy["reason"]
    return conn.execute(f"""SELECT e.episode_id,e.title,e.podcast_name,e.description,
      e.published_at,e.duration_seconds,e.play_count,e.comment_count,e.url,
      e.relevance_score,e.age_popularity_percentile,{score} AS active_quality,
      c.topics_json,c.reason,{reason} AS active_quality_reason,
      {policy['confidence']} AS active_quality_confidence,
      {policy['evidence']} AS active_quality_evidence,
      group_concat(m.keyword,'; ') AS matched_keywords
      FROM episodes e JOIN episode_classifications c USING(episode_id)
      LEFT JOIN quality_assessments q ON q.episode_id=e.episode_id AND q.rubric_version=?
      LEFT JOIN quality_overrides o ON o.episode_id=e.episode_id AND o.rubric_version=?
      LEFT JOIN episode_matches m USING(episode_id)
      WHERE e.relevance_score>=2 AND {score}>={policy['minimum']}
        AND (e.play_count IS NULL OR e.play_count>0)
      GROUP BY e.episode_id
      ORDER BY {score} DESC,e.published_at DESC,e.title""",
      (RUBRIC_VERSION, RUBRIC_VERSION)).fetchall()


def build_site(conn, output: Path) -> dict:
    output = output.resolve()
    if output.name in {"", ".", ".."}:
        raise ValueError("Choose a dedicated site output directory")
    if output.exists():
        shutil.rmtree(output)
    (output / "assets").mkdir(parents=True)
    (output / "data" / "details").mkdir(parents=True)

    rows = list(_rows(conn))
    measure = active_quality_measure(conn)
    policy = quality_sql(measure)
    updated = conn.execute("SELECT max(updated_at) FROM episodes").fetchone()[0]
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    items = []
    for row in rows:
        episode_id = row["episode_id"]
        if not EPISODE_ID.fullmatch(episode_id):
            raise ValueError(f"Unsafe episode ID: {episode_id!r}")
        topics = json.loads(row["topics_json"] or "[]")
        items.append({
            "id": episode_id, "title": row["title"], "podcast": row["podcast_name"],
            "published": row["published_at"], "duration": row["duration_seconds"],
            "plays": row["play_count"], "comments": row["comment_count"],
            "popularity": round(row["age_popularity_percentile"] * 100, 1)
            if row["age_popularity_percentile"] is not None else None,
            "relevance": row["relevance_score"], "quality": row["active_quality"],
            "qualityConfidence": row["active_quality_confidence"],
            "qualityEvidence": row["active_quality_evidence"],
            "topics": topics, "url": row["url"],
        })
        detail = {
            "description": _clean_html(row["description"], 500),
            "classificationReason": row["reason"] or "",
            "qualityReason": row["active_quality_reason"] or "",
            "qualityConfidence": row["active_quality_confidence"],
            "qualityEvidence": row["active_quality_evidence"],
            "matchedKeywords": [x.strip() for x in (row["matched_keywords"] or "").split(";") if x.strip()],
        }
        detail_path = _inside(output, output / "data" / "details" / f"{episode_id}.json")
        detail_path.write_text(json.dumps(detail, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    (output / "data" / "chunks").mkdir()
    chunks = []
    for offset in range(0, len(items), CHUNK_SIZE):
        name = f"episodes-{offset // CHUNK_SIZE:03d}.json"
        chunk = items[offset:offset + CHUNK_SIZE]
        (output / "data" / "chunks" / name).write_text(
            json.dumps(chunk, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        chunks.append(f"data/chunks/{name}")

    recent_floor = datetime.fromisoformat(generated).astimezone(timezone.utc) - timedelta(days=90)
    recent = []
    for item in items:
        try:
            published = datetime.fromisoformat((item["published"] or "").replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published >= recent_floor:
                recent.append(item)
        except ValueError:
            continue
    recent.sort(key=lambda item: item["id"])
    recent.sort(key=lambda item: item["published"] or "", reverse=True)
    recent.sort(key=lambda item: item["popularity"] or 0, reverse=True)
    recent.sort(key=lambda item: item["quality"] or 0, reverse=True)
    recent = recent[:50]
    topics = sorted({topic for item in items for topic in item["topics"]})
    payload = {"generatedAt": generated, "dataUpdatedAt": updated, "count": len(items),
               "qualityMeasure": measure, "qualityScale": policy["scale"],
               "recentWindowDays": 90, "recentEpisodes": recent,
               "chunkSize": CHUNK_SIZE, "chunks": chunks, "topics": topics}
    (output / "data" / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (output / "assets" / "site.css").write_text(SITE_CSS, encoding="utf-8")
    (output / "assets" / "site.js").write_text(SITE_JS, encoding="utf-8")
    (output / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return validate_site(output)


def validate_site(directory: Path) -> dict:
    root = directory.resolve()
    required = [root / "index.html", root / "assets/site.css", root / "assets/site.js", root / "data/index.json"]
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"Missing site files: {', '.join(missing)}")
    payload = json.loads((root / "data/index.json").read_text(encoding="utf-8"))
    episodes = []
    for relative in payload.get("chunks", []):
        path = _inside(root, root / relative)
        if not path.is_file():
            raise ValueError(f"Missing episode chunk: {relative}")
        chunk = json.loads(path.read_text(encoding="utf-8"))
        if len(chunk) > CHUNK_SIZE:
            raise ValueError(f"Episode chunk exceeds {CHUNK_SIZE}: {relative}")
        episodes.extend(chunk)
    if len(episodes) != payload.get("count"):
        raise ValueError("Manifest count does not match episode chunks")
    minimum_quality = 5 if payload.get("qualityScale") == 10 else 2
    seen = set()
    previous = None
    for item in episodes:
        episode_id = item.get("id", "")
        if not EPISODE_ID.fullmatch(episode_id) or episode_id in seen:
            raise ValueError(f"Invalid or duplicate episode ID: {episode_id!r}")
        seen.add(episode_id)
        if (item.get("relevance", 0) < 2 or item.get("quality", 0) < minimum_quality
                or item.get("plays") == 0):
            raise ValueError(f"Episode violates publication policy: {episode_id}")
        parsed = urlparse(item.get("url", ""))
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"Invalid episode URL: {episode_id}")
        key = (-item["quality"], item.get("published") or "")
        if previous and (key[0] < previous[0] or (key[0] == previous[0] and key[1] > previous[1])):
            raise ValueError("Episodes are not sorted by quality and publication date")
        previous = key
        if not (root / "data" / "details" / f"{episode_id}.json").is_file():
            raise ValueError(f"Missing detail file: {episode_id}")
    recent = payload.get("recentEpisodes", [])
    if len(recent) > 50 or any(item.get("id") not in seen for item in recent):
        raise ValueError("Invalid recent episode selection")
    for path in root.rglob("*"):
        if any(part in BLOCKED_NAMES for part in path.relative_to(root).parts):
            raise ValueError(f"Private file in site output: {path.relative_to(root)}")
    total_size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return {"episodes": len(episodes), "indexBytes": (root / "data/index.json").stat().st_size,
            "totalBytes": total_size, "directory": str(root)}


INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>机器人与具身智能播客精选</title><meta name="description" content="浏览经过相关性与内容质量筛选的机器人、具身智能播客单集。">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%230b1728'/%3E%3Cpath d='M10 12h12v10H10zM13 8v4m6-4v4M7 16h3m12 0h3M14 17h4' stroke='%234de1c1' stroke-width='2' stroke-linecap='round' fill='none'/%3E%3C/svg%3E">
<link rel="stylesheet" href="assets/site.css"></head><body>
<header><div><p class="eyebrow">ROBOTICS AUDIO INDEX</p><h1>机器人与具身智能播客精选</h1><p id="summary" class="summary">正在读取精选节目…</p></div><nav class="views" aria-label="榜单视图"><button data-view="recent" class="active" aria-pressed="true">近期 Top 50</button><button data-view="overall" aria-pressed="false">全部节目</button></nav></header>
<main><section class="controls" aria-label="筛选节目"><label class="search"><span>搜索</span><input id="search" type="search" placeholder="标题、播客或主题" autocomplete="off"></label><label><span>主题</span><select id="topic"><option value="">全部主题</option></select></label><label><span>质量</span><select id="quality"><option value="2">2 分以上</option><option value="3">仅 3 分</option></select></label><label><span>发布时间</span><select id="date"><option value="all">全部时间</option><option value="30">近 30 天</option><option value="180">近半年</option><option value="365">近一年</option></select></label><label><span>排序</span><select id="sort"><option value="quality">质量优先</option><option value="date">最新发布</option></select></label></section>
<div class="result-bar"><strong id="result-count">—</strong><button id="clear" type="button">清除筛选</button></div><section id="results" class="results" aria-live="polite"></section><p id="empty" class="empty" hidden>没有符合当前条件的节目，试试减少筛选条件。</p><button id="more" class="more" type="button" hidden>显示更多</button></main>
<template id="episode-template"><article class="episode"><div class="score" aria-label="质量评分"><b></b><span>质量</span></div><div class="episode-main"><div class="episode-top"><div><h2></h2><p class="podcast"></p></div><a class="listen" target="_blank" rel="noopener">去小宇宙收听 <span>↗</span></a></div><div class="meta"></div><div class="topics"></div><details><summary>内容简介与入选理由</summary><div class="detail-body"><p class="loading">正在载入…</p></div></details></div></article></template>
<script src="assets/site.js" defer></script></body></html>"""

SITE_CSS = """@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&family=Noto+Sans+SC:wght@400;500;600;700&display=swap');
:root{--ink:#eaf3ff;--muted:#8ca0b8;--bg:#07101d;--panel:#0c1929;--line:#20344a;--accent:#4de1c1;--accent2:#f6c95c;--danger:#ff8b78;color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 82% -5%,#153756 0,transparent 34rem),var(--bg);color:var(--ink);font-family:'Noto Sans SC',system-ui,sans-serif;font-size:16px;line-height:1.6}body:before{content:'';position:fixed;inset:0;pointer-events:none;opacity:.12;background-image:linear-gradient(#fff1 1px,transparent 1px),linear-gradient(90deg,#fff1 1px,transparent 1px);background-size:32px 32px;mask-image:linear-gradient(to bottom,#000,transparent 55%)}header,main{position:relative;width:min(1180px,calc(100% - 40px));margin:auto}header{min-height:190px;display:flex;align-items:center;justify-content:space-between;gap:30px;padding:38px 0 28px;border-bottom:1px solid var(--line)}.eyebrow{margin:0 0 8px;color:var(--accent);font:500 .75rem 'IBM Plex Mono',monospace;letter-spacing:.16em}h1{font-size:clamp(2rem,4.5vw,3.7rem);line-height:1.08;letter-spacing:-.045em;margin:0;max-width:800px}.summary{color:var(--muted);margin:11px 0 0}.views{align-self:flex-start;display:flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:10px;background:#091524}.views button{white-space:nowrap;border:0;border-radius:7px;padding:9px 14px;background:transparent;color:var(--muted);font:500 .84rem inherit;cursor:pointer}.views button.active{background:var(--accent);color:#06201b}.views button:focus-visible{outline:2px solid var(--accent2);outline-offset:2px}main{padding:22px 0 70px}.controls{display:grid;grid-template-columns:2fr repeat(4,1fr);gap:10px}.controls label{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:.76rem}.controls input,.controls select{width:100%;border:1px solid var(--line);background:#091524;color:var(--ink);border-radius:7px;padding:9px 11px;font:inherit;font-size:.87rem;outline:none}.controls input:focus,.controls select:focus{border-color:var(--accent);box-shadow:0 0 0 3px #4de1c122}.result-bar{display:flex;align-items:center;justify-content:space-between;margin:21px 0 8px;color:var(--muted)}#result-count{color:var(--ink)}#clear{border:0;background:none;color:var(--accent);font:inherit;cursor:pointer}.results{border-top:1px solid var(--line)}.episode{display:grid;grid-template-columns:64px 1fr;gap:18px;padding:20px 6px;border-bottom:1px solid var(--line);transition:background .2s}.episode:hover{background:#ffffff05}.score{text-align:center;border-right:1px solid var(--line);align-self:stretch;display:flex;flex-direction:column;justify-content:center}.score b{font:500 1.5rem 'IBM Plex Mono',monospace;color:var(--accent2)}.score span{color:var(--muted);font-size:.7rem}.episode-top{display:flex;justify-content:space-between;gap:22px;align-items:flex-start}h2{font-size:1.1rem;line-height:1.42;margin:0 0 3px}.podcast{margin:0;color:var(--muted)}.listen{flex:0 0 auto;color:var(--accent);text-decoration:none;font-size:.86rem;padding:4px 0}.meta{display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0 9px;color:var(--muted);font:500 .76rem 'IBM Plex Mono','Noto Sans SC',monospace}.topics{display:flex;gap:6px;flex-wrap:wrap}.topic{border:1px solid #2b5261;color:#a9eade;background:#10252d;border-radius:999px;padding:1px 8px;font-size:.72rem}details{margin-top:11px}summary{cursor:pointer;color:var(--muted);font-size:.83rem;list-style:none}summary:before{content:'＋';color:var(--accent);margin-right:7px}details[open] summary:before{content:'−'}.detail-body{margin-top:10px;padding:12px 14px;border-left:2px solid var(--accent);background:#0a1624;color:#c7d4e2}.detail-body p{margin:0 0 8px}.detail-body p:last-child{margin-bottom:0}.detail-label{color:var(--accent);font-size:.76rem}.empty{text-align:center;color:var(--muted);padding:60px 10px}.more{display:block;margin:26px auto 0;border:1px solid var(--line);border-radius:8px;padding:9px 22px;background:var(--panel);color:var(--ink);font:inherit;cursor:pointer}.error{color:var(--danger)}@media(max-width:900px){header{min-height:175px;align-items:flex-end}.controls{grid-template-columns:1fr 1fr}.search{grid-column:1/-1}.episode{grid-template-columns:46px 1fr;gap:11px}.episode-top{display:block}.listen{display:inline-block;margin-top:8px}}@media(max-width:620px){header{display:block;padding-top:30px}.views{margin-top:22px;width:max-content}.controls{grid-template-columns:1fr 1fr}header,main{width:min(100% - 24px,1180px)}.episode{padding-left:0;padding-right:0}.episode-top h2{font-size:1.02rem}.meta{gap:5px 10px}.listen{padding:7px 0}.score b{font-size:1.3rem}}"""

SITE_JS = """const $=s=>document.querySelector(s),state={view:'recent',manifest:null,recent:[],all:[],nextChunk:0,shown:50,details:new Map(),loading:null};
const fmt=n=>new Intl.NumberFormat('zh-CN',{notation:n>=10000?'compact':'standard',maximumFractionDigits:1}).format(n||0);
const date=s=>s?new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'short',day:'numeric'}).format(new Date(s)):'日期未知';
const duration=s=>s?`${Math.round(s/60)} 分钟`:'时长未知';
function setView(view){state.view=view;document.querySelectorAll('.views button').forEach(button=>{const active=button.dataset.view===view;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active))})}
function filtered(){const recent=state.view==='recent',q=$('#search').value.trim().toLocaleLowerCase(),topic=$('#topic').value,quality=Number($('#quality').value),days=$('#date').value;let rows=(recent?state.recent:state.all).filter(e=>(!q||[e.title,e.podcast,...e.topics].join(' ').toLocaleLowerCase().includes(q))&&(!topic||e.topics.includes(topic))&&e.quality>=quality);if(days!=='all'){const floor=Date.now()-Number(days)*864e5;rows=rows.filter(e=>new Date(e.published).getTime()>=floor)}if($('#sort').value==='date')rows.sort((a,b)=>(b.published||'').localeCompare(a.published||''));else if(!recent)rows.sort((a,b)=>b.quality-a.quality||(b.published||'').localeCompare(a.published||''));return rows}
function render(){const recent=state.view==='recent',rows=filtered(),root=$('#results');root.replaceChildren();$('#result-count').textContent=recent?`近期榜单 ${rows.length} 期`:`已载入 ${state.all.length}/${state.manifest.count} 期 · 符合 ${rows.length} 期`;$('#empty').hidden=rows.length>0;for(const ep of rows.slice(0,state.shown)){const node=$('#episode-template').content.cloneNode(true);node.querySelector('.score b').textContent=ep.quality;node.querySelector('h2').textContent=ep.title;node.querySelector('.podcast').textContent=ep.podcast||'未知播客';const link=node.querySelector('.listen');link.href=ep.url;node.querySelector('.meta').textContent=`${date(ep.published)} · ${duration(ep.duration)} · ${fmt(ep.plays)} 播放 · ${fmt(ep.comments)} 评论${ep.popularity==null?'':` · 同龄热度 ${ep.popularity}%`}`;const topics=node.querySelector('.topics');ep.topics.forEach(t=>{const x=document.createElement('span');x.className='topic';x.textContent=t;topics.append(x)});const details=node.querySelector('details');details.addEventListener('toggle',()=>details.open&&loadDetail(ep.id,details));root.append(node)}$('#more').hidden=recent||(rows.length<=state.shown&&state.nextChunk>=state.manifest.chunks.length)}
async function loadDetail(id,details){const body=details.querySelector('.detail-body');if(body.dataset.loaded)return;try{let d=state.details.get(id);if(!d){const response=await fetch(`data/details/${encodeURIComponent(id)}.json`);if(!response.ok)throw Error();d=await response.json();state.details.set(id,d)}body.replaceChildren();[['简介',d.description],['相关性',d.classificationReason],['内容质量',d.qualityReason]].forEach(([label,value])=>{if(!value)return;const p=document.createElement('p'),b=document.createElement('span');b.className='detail-label';b.textContent=`${label} · `;p.append(b,document.createTextNode(value));body.append(p)});body.dataset.loaded='1'}catch{body.innerHTML='<p class="error">详情载入失败，请稍后重试。</p>'}}
async function loadNext(){if(state.nextChunk>=state.manifest.chunks.length)return;const response=await fetch(state.manifest.chunks[state.nextChunk]);if(!response.ok)throw Error();state.all.push(...await response.json());state.nextChunk++}
async function loadAll(){if(state.loading)return state.loading;state.loading=(async()=>{while(state.nextChunk<state.manifest.chunks.length)await loadNext()})().finally(()=>state.loading=null);return state.loading}
async function useOverall(all=false){setView('overall');if(!state.all.length)await loadNext();if(all)await loadAll();state.shown=Math.max(50,state.shown);render()}
async function start(){try{const response=await fetch('data/index.json');if(!response.ok)throw Error();const data=await response.json();state.manifest=data;state.recent=data.recentEpisodes;if(data.qualityScale===10){const q=$('#quality');q.replaceChildren(new Option('5 分以上','5'),new Option('8 分以上','8'))}data.topics.forEach(t=>$('#topic').add(new Option(t,t)));const updated=data.dataUpdatedAt||data.generatedAt;$('#summary').textContent=`${data.count} 期精选 · 近期为 ${data.recentWindowDays} 天 · 数据更新于 ${date(updated)}`;render()}catch{$('#summary').textContent='数据载入失败';$('#results').innerHTML='<p class="error">无法读取节目索引，请刷新页面重试。</p>'}}
document.querySelectorAll('.views button').forEach(button=>button.addEventListener('click',async()=>{button.dataset.view==='overall'?await useOverall():(setView('recent'),render())}));$('#search').addEventListener('input',async()=>{state.shown=50;if($('#search').value.trim())await useOverall(true);else render()});['topic','quality','date'].forEach(id=>$('#'+id).addEventListener('change',async()=>{state.shown=50;if(state.view==='overall')await loadAll();render()}));$('#sort').addEventListener('change',render);$('#clear').addEventListener('click',()=>{$('#search').value='';setView('recent');$('#topic').value='';$('#quality').selectedIndex=0;$('#date').value='all';$('#sort').value='quality';state.shown=50;render()});$('#more').addEventListener('click',async()=>{if(state.shown>=filtered().length)await loadNext();state.shown+=50;render()});start();"""
