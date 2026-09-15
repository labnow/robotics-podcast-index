from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from urllib.parse import urlparse

from .export import TAG

EPISODE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
BLOCKED_NAMES = {"robotics_podcasts.db", ".robotcast", "refresh_token", "classify.lock"}


def _clean_html(value: str | None) -> str:
    return " ".join(unescape(TAG.sub(" ", value or "")).split())


def _inside(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Output path escapes site directory: {candidate}")
    return candidate


def _rows(conn):
    return conn.execute("""SELECT e.episode_id,e.title,e.podcast_name,e.description,
      e.published_at,e.duration_seconds,e.play_count,e.comment_count,e.url,
      e.relevance_score,e.age_popularity_percentile,c.quality_score,c.topics_json,
      c.reason,c.quality_reason,group_concat(m.keyword,'; ') AS matched_keywords
      FROM episodes e JOIN episode_classifications c USING(episode_id)
      LEFT JOIN episode_matches m USING(episode_id)
      WHERE e.relevance_score>=2 AND c.quality_score>=2
        AND (e.play_count IS NULL OR e.play_count>0)
      GROUP BY e.episode_id
      ORDER BY c.quality_score DESC,e.published_at DESC,e.title""").fetchall()


def build_site(conn, output: Path) -> dict:
    output = output.resolve()
    if output.name in {"", ".", ".."}:
        raise ValueError("Choose a dedicated site output directory")
    if output.exists():
        shutil.rmtree(output)
    (output / "assets").mkdir(parents=True)
    (output / "data" / "details").mkdir(parents=True)

    rows = list(_rows(conn))
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
            "relevance": row["relevance_score"], "quality": row["quality_score"],
            "topics": topics, "url": row["url"],
        })
        detail = {
            "description": _clean_html(row["description"]),
            "classificationReason": row["reason"] or "",
            "qualityReason": row["quality_reason"] or "",
            "matchedKeywords": [x.strip() for x in (row["matched_keywords"] or "").split(";") if x.strip()],
        }
        detail_path = _inside(output, output / "data" / "details" / f"{episode_id}.json")
        detail_path.write_text(json.dumps(detail, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    payload = {"generatedAt": generated, "dataUpdatedAt": updated, "count": len(items), "episodes": items}
    (output / "data" / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (output / "assets" / "site.css").write_text(SITE_CSS, encoding="utf-8")
    (output / "assets" / "site.js").write_text(SITE_JS, encoding="utf-8")
    (output / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    return validate_site(output)


def validate_site(directory: Path) -> dict:
    root = directory.resolve()
    required = [root / "index.html", root / "assets/site.css", root / "assets/site.js", root / "data/index.json"]
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"Missing site files: {', '.join(missing)}")
    payload = json.loads((root / "data/index.json").read_text(encoding="utf-8"))
    episodes = payload.get("episodes", [])
    seen = set()
    previous = None
    for item in episodes:
        episode_id = item.get("id", "")
        if not EPISODE_ID.fullmatch(episode_id) or episode_id in seen:
            raise ValueError(f"Invalid or duplicate episode ID: {episode_id!r}")
        seen.add(episode_id)
        if (item.get("relevance", 0) < 2 or item.get("quality", 0) < 2
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
<header><div><p class="eyebrow">ROBOTICS AUDIO INDEX</p><h1>机器人与具身智能播客精选</h1><p id="summary" class="summary">正在读取精选节目…</p></div><div class="signal" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></div></header>
<main><section class="controls" aria-label="筛选节目"><label class="search"><span>搜索</span><input id="search" type="search" placeholder="标题、播客或主题" autocomplete="off"></label><label><span>主题</span><select id="topic"><option value="">全部主题</option></select></label><label><span>质量</span><select id="quality"><option value="2">2 分以上</option><option value="3">仅 3 分</option></select></label><label><span>发布时间</span><select id="date"><option value="all">全部时间</option><option value="30">近 30 天</option><option value="180">近半年</option><option value="365">近一年</option></select></label><label><span>排序</span><select id="sort"><option value="quality">质量优先</option><option value="date">最新发布</option></select></label></section>
<div class="result-bar"><strong id="result-count">—</strong><button id="clear" type="button">清除筛选</button></div><section id="results" class="results" aria-live="polite"></section><p id="empty" class="empty" hidden>没有符合当前条件的节目，试试减少筛选条件。</p><button id="more" class="more" type="button" hidden>显示更多</button></main>
<template id="episode-template"><article class="episode"><div class="score" aria-label="质量评分"><b></b><span>质量</span></div><div class="episode-main"><div class="episode-top"><div><h2></h2><p class="podcast"></p></div><a class="listen" target="_blank" rel="noopener">去小宇宙收听 <span>↗</span></a></div><div class="meta"></div><div class="topics"></div><details><summary>内容简介与入选理由</summary><div class="detail-body"><p class="loading">正在载入…</p></div></details></div></article></template>
<script src="assets/site.js" defer></script></body></html>"""

SITE_CSS = """@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&family=Noto+Sans+SC:wght@400;500;600;700&display=swap');
:root{--ink:#eaf3ff;--muted:#8ca0b8;--bg:#07101d;--panel:#0c1929;--line:#20344a;--accent:#4de1c1;--accent2:#f6c95c;--danger:#ff8b78;color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 82% -5%,#153756 0,transparent 34rem),var(--bg);color:var(--ink);font-family:'Noto Sans SC',system-ui,sans-serif;font-size:16px;line-height:1.6}body:before{content:'';position:fixed;inset:0;pointer-events:none;opacity:.16;background-image:linear-gradient(#fff1 1px,transparent 1px),linear-gradient(90deg,#fff1 1px,transparent 1px);background-size:32px 32px;mask-image:linear-gradient(to bottom,#000,transparent 60%)}header,main{position:relative;width:min(1180px,calc(100% - 40px));margin:auto}header{min-height:230px;display:flex;align-items:flex-end;justify-content:space-between;padding:56px 0 34px;border-bottom:1px solid var(--line)}.eyebrow{margin:0 0 10px;color:var(--accent);font:500 .78rem 'IBM Plex Mono',monospace;letter-spacing:.18em}h1{font-size:clamp(2rem,5vw,4.1rem);line-height:1.08;letter-spacing:-.045em;margin:0;max-width:820px}.summary{color:var(--muted);margin:14px 0 0}.signal{height:94px;display:flex;align-items:flex-end;gap:6px}.signal span{width:7px;background:var(--accent);border-radius:5px;animation:pulse 1.8s ease-in-out infinite}.signal span:nth-child(1){height:28%}.signal span:nth-child(2){height:72%;animation-delay:.2s}.signal span:nth-child(3){height:46%;animation-delay:.4s}.signal span:nth-child(4){height:92%;animation-delay:.6s}.signal span:nth-child(5){height:58%;animation-delay:.8s}@keyframes pulse{50%{opacity:.35;transform:scaleY(.65)}}main{padding:28px 0 80px}.controls{display:grid;grid-template-columns:2fr repeat(4,1fr);gap:12px}.controls label{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:.8rem}.controls input,.controls select{width:100%;border:1px solid var(--line);background:#091524;color:var(--ink);border-radius:8px;padding:11px 12px;font:inherit;font-size:.9rem;outline:none}.controls input:focus,.controls select:focus{border-color:var(--accent);box-shadow:0 0 0 3px #4de1c122}.result-bar{display:flex;align-items:center;justify-content:space-between;margin:26px 0 10px;color:var(--muted)}#result-count{color:var(--ink)}#clear{border:0;background:none;color:var(--accent);font:inherit;cursor:pointer}.results{border-top:1px solid var(--line)}.episode{display:grid;grid-template-columns:72px 1fr;gap:22px;padding:25px 8px;border-bottom:1px solid var(--line);transition:background .2s}.episode:hover{background:#ffffff05}.score{text-align:center;border-right:1px solid var(--line);align-self:stretch;display:flex;flex-direction:column;justify-content:center}.score b{font:500 1.65rem 'IBM Plex Mono',monospace;color:var(--accent2)}.score span{color:var(--muted);font-size:.72rem}.episode-top{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}h2{font-size:1.15rem;line-height:1.45;margin:0 0 4px}.podcast{margin:0;color:var(--muted)}.listen{flex:0 0 auto;color:var(--accent);text-decoration:none;font-size:.9rem;padding:5px 0}.meta{display:flex;flex-wrap:wrap;gap:8px 16px;margin:13px 0 11px;color:var(--muted);font:500 .78rem 'IBM Plex Mono','Noto Sans SC',monospace}.topics{display:flex;gap:7px;flex-wrap:wrap}.topic{border:1px solid #2b5261;color:#a9eade;background:#10252d;border-radius:999px;padding:2px 9px;font-size:.75rem}details{margin-top:14px}summary{cursor:pointer;color:var(--muted);font-size:.86rem;list-style:none}summary:before{content:'＋';color:var(--accent);margin-right:7px}details[open] summary:before{content:'−'}.detail-body{margin-top:12px;padding:14px 16px;border-left:2px solid var(--accent);background:#0a1624;color:#c7d4e2}.detail-body p{margin:0 0 10px}.detail-body p:last-child{margin-bottom:0}.detail-label{color:var(--accent);font-size:.78rem}.empty{text-align:center;color:var(--muted);padding:70px 10px}.more{display:block;margin:30px auto 0;border:1px solid var(--line);border-radius:8px;padding:10px 24px;background:var(--panel);color:var(--ink);font:inherit;cursor:pointer}.error{color:var(--danger)}@media(max-width:800px){header{min-height:190px}.signal{display:none}.controls{grid-template-columns:1fr 1fr}.search{grid-column:1/-1}.episode{grid-template-columns:48px 1fr;gap:12px}.episode-top{display:block}.listen{display:inline-block;margin-top:10px}}@media(max-width:520px){header,main{width:min(100% - 24px,1180px)}header{padding-top:38px}.controls{grid-template-columns:1fr 1fr}.controls label:last-child{grid-column:1/-1}.episode{padding-left:0;padding-right:0}.episode-top h2{font-size:1.03rem}.meta{gap:6px 12px}.listen{padding:8px 0}.score b{font-size:1.35rem}}@media(prefers-reduced-motion:reduce){.signal span{animation:none}}"""

SITE_JS = """const $=s=>document.querySelector(s),state={all:[],shown:30,details:new Map()};
const fmt=n=>new Intl.NumberFormat('zh-CN',{notation:n>=10000?'compact':'standard',maximumFractionDigits:1}).format(n||0);
const date=s=>s?new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'short',day:'numeric'}).format(new Date(s)):'日期未知';
const duration=s=>s?`${Math.round(s/60)} 分钟`:'时长未知';
function filtered(){const q=$('#search').value.trim().toLocaleLowerCase(),topic=$('#topic').value,quality=Number($('#quality').value),days=$('#date').value;let rows=state.all.filter(e=>(!q||[e.title,e.podcast,...e.topics].join(' ').toLocaleLowerCase().includes(q))&&(!topic||e.topics.includes(topic))&&e.quality>=quality);if(days!=='all'){const floor=Date.now()-Number(days)*864e5;rows=rows.filter(e=>new Date(e.published).getTime()>=floor)}rows.sort($('#sort').value==='date'?(a,b)=>(b.published||'').localeCompare(a.published||''):(a,b)=>b.quality-a.quality||(b.published||'').localeCompare(a.published||''));return rows}
function render(){const rows=filtered(),root=$('#results');root.replaceChildren();$('#result-count').textContent=`找到 ${rows.length} 期`;$('#empty').hidden=rows.length>0;for(const ep of rows.slice(0,state.shown)){const node=$('#episode-template').content.cloneNode(true);node.querySelector('.score b').textContent=ep.quality;node.querySelector('h2').textContent=ep.title;node.querySelector('.podcast').textContent=ep.podcast||'未知播客';const link=node.querySelector('.listen');link.href=ep.url;node.querySelector('.meta').textContent=`${date(ep.published)} · ${duration(ep.duration)} · ${fmt(ep.plays)} 播放 · ${fmt(ep.comments)} 评论${ep.popularity==null?'':` · 同龄热度 ${ep.popularity}%`}`;const topics=node.querySelector('.topics');ep.topics.forEach(t=>{const x=document.createElement('span');x.className='topic';x.textContent=t;topics.append(x)});const details=node.querySelector('details');details.addEventListener('toggle',()=>details.open&&loadDetail(ep.id,details));root.append(node)}$('#more').hidden=rows.length<=state.shown}
async function loadDetail(id,details){const body=details.querySelector('.detail-body');if(body.dataset.loaded)return;try{let d=state.details.get(id);if(!d){const response=await fetch(`data/details/${encodeURIComponent(id)}.json`);if(!response.ok)throw Error();d=await response.json();state.details.set(id,d)}body.replaceChildren();[['简介',d.description],['相关性',d.classificationReason],['内容质量',d.qualityReason]].forEach(([label,value])=>{if(!value)return;const p=document.createElement('p'),b=document.createElement('span');b.className='detail-label';b.textContent=`${label} · `;p.append(b,document.createTextNode(value));body.append(p)});body.dataset.loaded='1'}catch{body.innerHTML='<p class="error">详情载入失败，请稍后重试。</p>'}}
async function start(){try{const response=await fetch('data/index.json');if(!response.ok)throw Error();const data=await response.json();state.all=data.episodes;const topics=[...new Set(state.all.flatMap(e=>e.topics))].sort((a,b)=>a.localeCompare(b,'zh-CN'));topics.forEach(t=>$('#topic').add(new Option(t,t)));const updated=data.dataUpdatedAt||data.generatedAt;$('#summary').textContent=`${data.count} 期精选 · 数据更新于 ${date(updated)}`;render()}catch{$('#summary').textContent='数据载入失败';$('#results').innerHTML='<p class="error">无法读取节目索引，请刷新页面重试。</p>'}}
['search','topic','quality','date','sort'].forEach(id=>$('#'+id).addEventListener(id==='search'?'input':'change',()=>{state.shown=30;render()}));$('#clear').addEventListener('click',()=>{$('#search').value='';$('#topic').value='';$('#quality').value='2';$('#date').value='all';$('#sort').value='quality';state.shown=30;render()});$('#more').addEventListener('click',()=>{state.shown+=30;render()});start();"""
