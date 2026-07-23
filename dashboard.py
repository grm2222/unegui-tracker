#!/usr/bin/env python3
"""Regenerate docs/index.html (self-contained, GitHub Pages) from data/unegui.db.

Features: value ranking vs complex median ₮/m², per-listing price/views
timeline, same-complex listings table, duplicate detection (same apartment
listed by multiple agents / relists).

Usage:  python dashboard.py
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import residences
from scraper import DB_PATH, UB_TZ, connect

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "index.html"


def load_data():
    con = connect()
    con.row_factory = sqlite3.Row

    listings = []
    for r in con.execute("SELECT * FROM listings"):
        r = dict(r)
        res_key, res_conf = residences.resolve(
            r["title"], r["sub_location"], r["district"], r["description"])
        listings.append({
            "res": res_key,
            "resName": residences.display(res_key),
            "resConf": res_conf,
            "id": r["ad_id"], "url": r["url"], "title": r["title"],
            "area": r["area_m2"], "floor": r["floor"],
            "bfloors": r["building_floors"], "year": r["commissioned_year"],
            "balcony": r["balcony"], "garage": r["garage"],
            "window": r["window_type"], "wcount": r["window_count"],
            "door": r["door"], "flmat": r["floor_material"],
            "elevator": r["elevator"], "payment": r["payment_terms"],
            "district": r["district"], "sub": r["sub_location"],
            "khoroo": r["khoroo"], "lat": r["latitude"], "lon": r["longitude"],
            "phone": r["phone"], "seller": r["seller_name"],
            "since": r["seller_since"], "desc": r["description"],
            "posted": r["posted_date"], "first": r["first_seen"],
            "last": r["last_seen"], "delisted": r["delisted_at"],
            "enriched": r["enriched"],
            # llm_* wins over rx_* when present
            **{k: (r[f"llm_{k}"] or r[f"rx_{k}"] or "")
               for k in ("orientation", "bathrooms", "ceiling_m", "certificate",
                         "furniture", "renovation", "landmarks", "notes")},
        })

    snaps = {}
    for r in con.execute("SELECT ad_id, crawl_date, price_mnt, old_price_mnt,"
                         " views FROM snapshots ORDER BY ad_id, crawl_date"):
        snaps.setdefault(r["ad_id"], []).append(
            [r["crawl_date"], r["price_mnt"], r["old_price_mnt"], r["views"]])

    events = {}
    for r in con.execute("SELECT ad_id, event_date, event_type, old_value,"
                         " new_value FROM events ORDER BY event_date"):
        events.setdefault(r["ad_id"], []).append(
            [r["event_date"], r["event_type"], r["old_value"], r["new_value"]])

    con.close()
    return {
        "generated": datetime.now(UB_TZ).strftime("%Y-%m-%d %H:%M"),
        "listings": listings,
        "snapshots": snaps,
        "events": events,
    }


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>unegui 2-room tracker</title>
<!-- Leaflet + OpenStreetMap: both free, no API key. SRI hashes verified
     against unpkg 2026-07-24. This is the ONE external dependency in an
     otherwise self-contained page -- a basemap has to come from somewhere. -->
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<style>
:root { --bg:#f6f7f9; --card:#fff; --ink:#1c2128; --mut:#6a737d; --line:#e2e6ea;
        --good:#1a7f37; --bad:#c9403a; --acc:#0b62c4; --chip:#eef2f6; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#12151a; --card:#1a1f26; --ink:#dfe5ec; --mut:#8a94a0;
          --line:#2b323b; --good:#4dbb6e; --bad:#e5726d; --acc:#5aa2ee; --chip:#242b34; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:14px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif; }
header { padding:14px 18px 8px; }
h1 { font-size:19px; margin:0 0 2px; }
.mut { color:var(--mut); } .sm { font-size:12px; }
.chips { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 0; }
.chip { background:var(--chip); border-radius:14px; padding:3px 11px; font-size:12px; }
.chip b { font-size:13px; }
#controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center;
            padding:8px 18px; }
input,select { background:var(--card); color:var(--ink); border:1px solid var(--line);
               border-radius:6px; padding:6px 9px; font-size:13px; }
label.cb { font-size:13px; display:flex; align-items:center; gap:4px; }
main { padding:0 18px 40px; }
table { border-collapse:collapse; width:100%; background:var(--card);
        border:1px solid var(--line); border-radius:8px; overflow:hidden; }
th,td { padding:6px 9px; text-align:left; border-bottom:1px solid var(--line);
        white-space:nowrap; }
th { cursor:pointer; user-select:none; font-size:12px; color:var(--mut);
     background:var(--chip); position:sticky; top:0; }
tbody tr { cursor:pointer; }
tbody tr:hover { background:var(--chip); }
td.num, th.num { text-align:right; }
.good { color:var(--good); font-weight:600; } .bad { color:var(--bad); }
.b-dup2 { background:var(--bad); color:#fff; border-radius:4px; padding:1px 6px; font-size:11px; }
.b-dup1 { background:var(--acc); color:#fff; border-radius:4px; padding:1px 6px; font-size:11px; }
.b-off { background:var(--mut); color:#fff; border-radius:4px; padding:1px 6px; font-size:11px; }
.tblwrap { overflow-x:auto; }
.pane { display:none; } .pane.on { display:block; }
.wcard { background:var(--card); border:1px solid var(--line); border-radius:8px;
         padding:12px 14px; margin:0 0 12px; }
.wcard h3 { margin:0 0 2px; font-size:15px; }
.wrow { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.btn { background:var(--acc); color:#fff; border:none; border-radius:6px;
       padding:6px 12px; font-size:13px; cursor:pointer; }
.btn.ghost { background:var(--chip); color:var(--ink); }
.btn.danger { background:var(--bad); }
#wishBadge:not(:empty) { background:var(--bad); color:#fff; border-radius:9px;
       padding:0 6px; font-size:11px; margin-left:2px; }
.empty { color:var(--mut); padding:24px 2px; }
.newbadge { background:var(--good); color:#fff; border-radius:4px;
            padding:1px 6px; font-size:11px; }
#map { height:70vh; min-height:420px; border:1px solid var(--line); border-radius:8px;
       display:none; background:var(--chip); }
#map.on { display:block; }
.tblwrap.off { display:none; }
.viewbtn { background:var(--card); border:1px solid var(--line); color:var(--ink);
           border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer; }
.viewbtn.sel { background:var(--acc); color:#fff; border-color:var(--acc); }
.legend { display:none; gap:12px; align-items:center; flex-wrap:wrap;
          font-size:12px; color:var(--mut); padding:8px 0 6px; }
.legend i { width:11px; height:11px; border-radius:50%; display:inline-block;
            margin-right:4px; vertical-align:-1px; }
/* OSM tiles are light-only; dim them so dark mode isn't a glare bomb */
@media (prefers-color-scheme: dark) {
  .leaflet-tile-pane { filter:brightness(.68) contrast(1.06) saturate(.85); }
}
.leaflet-popup-content { font:13px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif;
                         margin:10px 12px; }
#detail { position:fixed; inset:0 0 0 auto; width:min(720px,100%);
          background:var(--card); border-left:1px solid var(--line);
          box-shadow:-8px 0 30px rgba(0,0,0,.25); overflow-y:auto;
          padding:18px; display:none; z-index:9; }
#detail.open { display:block; }
#detail h2 { margin:0 42px 4px 0; font-size:17px; }
#close { position:absolute; top:12px; right:14px; font-size:20px; cursor:pointer;
         background:none; border:none; color:var(--mut); }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
        gap:6px 14px; margin:12px 0; }
.grid div { font-size:13px; } .grid .k { color:var(--mut); font-size:11px; }
.desc { background:var(--chip); border-radius:8px; padding:10px 12px;
        white-space:pre-wrap; font-size:13px; margin:10px 0; }
h3 { font-size:14px; margin:18px 0 6px; }
svg.spark { width:100%; height:90px; background:var(--chip); border-radius:8px; }
a { color:var(--acc); }
.small-t td, .small-t th { padding:4px 8px; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>unegui.mn — 2-room apartment tracker</h1>
  <div class="mut sm">generated __GENERATED__ (UB time) · value = ₮/m² vs residence median (duplicates excluded)</div>
  <div class="chips" id="chips"></div>
</header>
<div id="controls">
  <input id="q" placeholder="search title / residence / district…" size="30">
  <select id="fComplex"><option value="">all residences</option></select>
  <label class="cb"><input type="checkbox" id="fActive" checked> active only</label>
  <label class="cb"><input type="checkbox" id="fDups"> hide duplicate extras</label>
  <span class="mut sm" id="count"></span>
  <button class="viewbtn sel" id="vTable">Listings</button>
  <button class="viewbtn" id="vRes">Residences</button>
  <button class="viewbtn" id="vMap">Map</button>
  <button class="viewbtn" id="vWish">★ Wishlist <span id="wishBadge"></span></button>
</div>
<main>
  <div class="legend" id="legend">
    <span>vs complex median ₮/m²:</span>
    <span><i style="background:#1a7f37"></i>≤ −15%</span>
    <span><i style="background:#5bb974"></i>−15…−5%</span>
    <span><i style="background:#d0b000"></i>±5%</span>
    <span><i style="background:#e8833a"></i>+5…+15%</span>
    <span><i style="background:#c9403a"></i>≥ +15%</span>
    <span><i style="background:#8a94a0"></i>no median (residence &lt; 3 ads)</span>
    <span id="mapcount"></span>
  </div>
  <div id="map"></div>
  <div id="resView" class="pane">
    <div class="tblwrap"><table id="restbl">
      <thead><tr>
        <th data-rk="name">Residence</th><th data-rk="dist">District</th>
        <th class="num" data-rk="nActive">Listed now</th>
        <th class="num" data-rk="nSold">Sold / gone</th>
        <th class="num" data-rk="nTotal">Ever seen</th>
        <th class="num" data-rk="avgPrice">Avg price</th>
        <th class="num" data-rk="medPrice">Median price</th>
        <th class="num" data-rk="medPpm">Median ₮/m²</th>
        <th class="num" data-rk="medDays">Median days to sell</th>
        <th class="num" data-rk="cuts">Price cuts</th>
        <th></th>
      </tr></thead><tbody></tbody>
    </table></div>
  </div>
  <div id="wishView" class="pane"></div>
  <div class="tblwrap"><table id="main">
    <thead><tr>
      <th data-k="resName">Residence</th><th data-k="title">Title</th>
      <th class="num" data-k="area">m²</th><th class="num" data-k="floor">Floor</th>
      <th class="num" data-k="year">Year</th><th class="num" data-k="price">Price</th>
      <th class="num" data-k="ppm">₮/m²</th><th class="num" data-k="vsMed">vs median</th>
      <th class="num" data-k="days">Days</th><th class="num" data-k="views">Views</th>
      <th data-k="dup">Dup</th><th data-k="status">Status</th>
    </tr></thead>
    <tbody></tbody>
  </table></div>
</main>
<div id="detail"><button id="close">✕</button><div id="dbody"></div></div>
<script>
const DATA = __DATA__;
const L = DATA.listings, SNAP = DATA.snapshots, EV = DATA.events;
const TODAY = DATA.generated.slice(0,10);

const fmtM = v => v==null ? "" : (v/1e6).toLocaleString("en",{maximumFractionDigits:1})+" сая";
const fmtPpm = v => v==null ? "" : (v/1e6).toFixed(2)+" сая";
const esc = s => (s??"").toString().replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const days = (a,b) => Math.round((new Date(b)-new Date(a))/864e5);

// -------- derived per-listing fields
for (const l of L) {
  const sn = SNAP[l.id]||[];
  const lastSn = sn.length ? sn[sn.length-1] : null;
  l.price = lastSn ? lastSn[1] : null;
  l.views = lastSn ? lastSn[3] : null;
  l.ppm = (l.price && l.area) ? l.price/l.area : null;
  // Time on market runs from the ad's OWN posted date (data goes back to
  // 2025-10), not from our first crawl -- measuring from first_seen makes
  // every pre-existing ad look at most as old as the tracker itself and
  // understates days-to-sell roughly 3x.
  // Guard: unegui prints RELATIVE dates ("Өчигдөр 16:24" = yesterday) for
  // recent ads and older rows stored that string verbatim, so accept only a
  // real ISO date in a sane range; anything else falls back to first_seen.
  const p = (l.posted||"").slice(0,10);
  const from = (/^\d{4}-\d{2}-\d{2}$/.test(p) && p >= "2015-01-01" && p <= TODAY)
               ? p : l.first;
  const d = from ? days(from, l.delisted||TODAY) : null;
  l.days = (d==null || !isFinite(d)) ? null : Math.max(0, d);
  l.status = l.delisted ? "delisted "+l.delisted : "active";
  l.cuts = (EV[l.id]||[]).filter(e=>e[1]==="price_down").length;
}

// -------- duplicate detection (dupMatch): exact same area in ALL tiers
function sameUnit(a,b){
  if (!a.area || !b.area || a.area!==b.area) return 0;
  if ((a.res||"") !== (b.res||"")) return 0;
  const eqN = (x,y) => x==null || y==null || x===y;   // nulls tolerated
  const samePhone = a.phone && b.phone && a.phone===b.phone;
  const sameName  = a.seller && b.seller && a.seller===b.seller;
  if ((samePhone||sameName) && eqN(a.floor,b.floor) && eqN(a.bfloors,b.bfloors)
      && eqN(a.year,b.year)) return 2;                // CONFIRMED (relist)
  if (!(samePhone||sameName) && eqN(a.floor,b.floor) && eqN(a.year,b.year))
    return 1;                                         // LIKELY (multi-agent)
  return 0;
}
const parent = {}, find = x => parent[x]===x ? x : (parent[x]=find(parent[x]));
L.forEach(l => parent[l.id]=l.id);
const tier = {};
const buckets = {};
L.forEach(l => { const k=(l.res||"?")+"|"+(l.area||"?");
                 (buckets[k]=buckets[k]||[]).push(l); });
for (const arr of Object.values(buckets))
  for (let i=0;i<arr.length;i++) for (let j=i+1;j<arr.length;j++){
    const t = sameUnit(arr[i],arr[j]);
    if (t){ parent[find(arr[i].id)] = find(arr[j].id);
            tier[arr[i].id]=Math.max(tier[arr[i].id]||0,t);
            tier[arr[j].id]=Math.max(tier[arr[j].id]||0,t); }
  }
const groups = {};
L.forEach(l => { const r=find(l.id); (groups[r]=groups[r]||[]).push(l); });
const dupGroups = Object.values(groups).filter(g=>g.length>1);
for (const l of L){ l.dup = tier[l.id]||0; l.rep = true; l.group = null; }
for (const g of dupGroups){
  g.forEach(l => l.group = g);
  // representative = cheapest active (falls back to cheapest overall);
  // only the rep counts toward the complex median
  const act = g.filter(l=>!l.delisted && l.price);
  const rep = (act.length?act:g.filter(l=>l.price)).sort((a,b)=>a.price-b.price)[0];
  g.forEach(l => l.rep = (l===rep));
}

// -------- complex medians (₮/m², active representatives only)
const median = a => { if(!a.length) return null; a=[...a].sort((x,y)=>x-y);
  const m=a.length>>1; return a.length%2 ? a[m] : (a[m-1]+a[m])/2; };
const cplx = {};
for (const l of L)
  if (l.res && l.ppm && !l.delisted && l.rep)
    (cplx[l.res]=cplx[l.res]||[]).push(l.ppm);
const cmed = {}, cn = {};
for (const [k,v] of Object.entries(cplx)){ cmed[k]=median(v); cn[k]=v.length; }
for (const l of L)
  l.vsMed = (l.ppm && l.res && cmed[l.res] && cn[l.res]>=3)
            ? (l.ppm/cmed[l.res]-1)*100 : null;

// -------- residences: the real grouping unit. Coordinates are seller-placed
// and often wrong, so residences are keyed on the NAME parsed from the ad
// (plus district), not on position. See residences.py.
const RES = {};
for (const l of L){
  const r = RES[l.res] || (RES[l.res] = {key:l.res, name:l.resName,
                                         conf:l.resConf, all:[]});
  r.all.push(l);
}
const avg = a => a.length ? a.reduce((x,y)=>x+y,0)/a.length : null;
for (const r of Object.values(RES)){
  r.active = r.all.filter(l=>!l.delisted);
  r.sold   = r.all.filter(l=>l.delisted);
  r.nActive = r.active.length; r.nSold = r.sold.length; r.nTotal = r.all.length;
  const prices = r.active.map(l=>l.price).filter(Boolean);
  r.avgPrice = avg(prices);
  r.medPrice = median(prices);
  r.medPpm   = median(r.active.map(l=>l.ppm).filter(Boolean));
  // "days to sell" only means anything for ads that actually went away
  r.medDays  = median(r.sold.map(l=>l.days).filter(d=>d!=null));
  r.cuts     = r.all.reduce((a,l)=>a+l.cuts,0);
  const parts = (r.name||"").split(" · ");
  r.nameOnly = parts[0]; r.dist = parts[1] || parts[0];
}

// -------- header chips
const act = L.filter(l=>!l.delisted);
const cuts30 = Object.values(EV).flat().filter(e =>
  e[1]==="price_down" && days(e[0],TODAY)<=30).length;
const sold30 = Object.values(EV).flat().filter(e =>
  e[1]==="delisted" && days(e[0],TODAY)<=30).length;
document.getElementById("chips").innerHTML =
  `<span class="chip"><b>${act.length}</b> active</span>`+
  `<span class="chip"><b>${L.length-act.length}</b> delisted total</span>`+
  `<span class="chip"><b>${cuts30}</b> price cuts /30d</span>`+
  `<span class="chip"><b>${sold30}</b> delisted /30d</span>`+
  `<span class="chip"><b>${dupGroups.length}</b> duplicate groups</span>`;

// -------- filters + table
const sel = document.getElementById("fComplex");
Object.keys(cmed).map(k=>[k, RES[k]?RES[k].name:k])
  .sort((a,b)=>a[1].localeCompare(b[1])).forEach(([k,nm])=>{
    const o=document.createElement("option"); o.value=k;
    o.textContent=`${nm} (${cn[k]}, med ${fmtPpm(cmed[k])})`; sel.appendChild(o);
  });
let sortK="vsMed", sortDir=1;
function rows(){
  const q=document.getElementById("q").value.toLowerCase();
  const c=sel.value, actOnly=document.getElementById("fActive").checked,
        hideDup=document.getElementById("fDups").checked;
  let r=L.filter(l=>
    (!actOnly||!l.delisted) && (!c||l.res===c) && (!hideDup||l.rep) &&
    (!q || (l.title+" "+(l.resName||"")+" "+(l.sub||"")+" "+(l.district||""))
             .toLowerCase().includes(q)));
  r.sort((a,b)=>{ const x=a[sortK], y=b[sortK];
    if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
    return (x<y?-1:x>y?1:0)*sortDir; });
  return r;
}
function render(){
  const r=rows();
  document.getElementById("count").textContent=r.length+" listings";
  document.querySelector("#main tbody").innerHTML=r.map(l=>`<tr data-id="${l.id}">
    <td>${esc(l.resName||"")}</td>
    <td title="${esc(l.title)}">${esc((l.title||"").slice(0,46))}</td>
    <td class="num">${l.area??""}</td>
    <td class="num">${l.floor??""}${l.bfloors?"/"+l.bfloors:""}</td>
    <td class="num">${l.year??""}</td>
    <td class="num">${fmtM(l.price)}${l.cuts?` <span class="bad sm">↓${l.cuts}</span>`:""}</td>
    <td class="num">${fmtPpm(l.ppm)}</td>
    <td class="num ${l.vsMed==null?"":l.vsMed<0?"good":"bad"}">${
      l.vsMed==null?"":(l.vsMed>0?"+":"")+l.vsMed.toFixed(1)+"%"}</td>
    <td class="num">${l.days??""}</td>
    <td class="num">${l.views??""}</td>
    <td>${l.dup===2?'<span class="b-dup2">RELIST</span>'
         :l.dup===1?'<span class="b-dup1">DUP?</span>':""}</td>
    <td>${l.delisted?'<span class="b-off">gone</span>':"active"}</td>
  </tr>`).join("");
  if(mapOn) drawMap();   // keep the map in sync with the filters
}
// -------- map view (Leaflet + OpenStreetMap tiles; free, no API key)
// Coordinates are per-listing seller-placed pins, so we plot listings
// individually -- sub_location is often a whole district (Баянзүрх spans
// ~100 km), far too coarse to be a single map marker.
// NB: this file already uses `L` for the listings array, which shadows
// Leaflet's global `L` -- reach it through window (top-level `const` does
// not create a window property, so window.L is still Leaflet).
const LF = window.L;
let map=null, layer=null, mapOn=false;
function pinColor(l){
  if (l.vsMed==null) return "#8a94a0";
  if (l.vsMed <= -15) return "#1a7f37";
  if (l.vsMed <=  -5) return "#5bb974";
  if (l.vsMed <    5) return "#d0b000";
  if (l.vsMed <   15) return "#e8833a";
  return "#c9403a";
}
function initMap(){
  map = LF.map("map", {preferCanvas:true}).setView([47.9187,106.9176], 12);
  LF.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom:19,
    attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
}
// A few dozen ads sit in other aimags (Дархан, Ховд, Дорнод...). Fitting
// bounds to them zooms the map out to the whole country, so trim the outer
// 2% of points when there are enough to make that meaningful.
let fitTimer = null;
function fitTo(pts, tries){
  if(!map || !pts.length) return;
  // cancel any pending retry, or a stale one from an earlier filter would
  // fire later and re-fit the map to the previous point set
  if(fitTimer){ clearTimeout(fitTimer); fitTimer = null; }
  // the container starts display:none, so the first fit can land before
  // layout -- a zero width would make fitBounds pick zoom 0 (whole world)
  if(map.getSize().x === 0 && (tries||0) < 12){
    fitTimer = setTimeout(()=>{
      fitTimer = null; map.invalidateSize(); fitTo(pts, (tries||0)+1);
    }, 60);
    return;
  }
  const q=(arr,f)=>arr[Math.min(arr.length-1, Math.max(0, Math.floor(arr.length*f)))];
  let b;
  if(pts.length >= 25){
    const lats=pts.map(p=>p.lat).sort((a,b)=>a-b),
          lons=pts.map(p=>p.lon).sort((a,b)=>a-b);
    b = LF.latLngBounds([q(lats,.02),q(lons,.02)], [q(lats,.98),q(lons,.98)]);
  } else {
    b = LF.latLngBounds(pts.map(p=>[p.lat,p.lon]));
  }
  // animate:false -- the re-fit fires on every filter change, so snap there
  // instead of flying across the city each time (and zoom animations stall
  // in backgrounded tabs, which would leave the map on the old view)
  map.fitBounds(b.pad(0.06), {animate:false});
}
function drawMap(){
  if(!map) return;
  if(layer) map.removeLayer(layer);
  const pts = rows().filter(l=>l.lat && l.lon);
  layer = LF.layerGroup(pts.map(l=>{
    const m = LF.circleMarker([l.lat,l.lon], {radius:5, weight:1,
      color:"rgba(0,0,0,.45)", fillColor:pinColor(l), fillOpacity:.85});
    m.bindPopup(
      `<b>${esc(l.resName||"")}</b>`+
      (l.khoroo?` <span style="opacity:.6">${esc(l.khoroo)}</span>`:"")+
      `<br>${esc((l.title||"").slice(0,70))}<br>`+
      `<b>${fmtM(l.price)}</b>`+
      (l.area?` · ${l.area} m²`:"")+
      (l.floor?` · ${l.floor}${l.bfloors?"/"+l.bfloors:""} давхар`:"")+
      `<br>${fmtPpm(l.ppm)}/m²`+
      (l.vsMed!=null?` (${l.vsMed>0?"+":""}${l.vsMed.toFixed(0)}% vs median)`:"")+
      (l.dup?`<br><b style="color:#c9403a">${l.dup===2?"RELIST":"possible duplicate"}</b>`:"")+
      `<br><a href="#" onclick="openDetail(${l.id});return false">open details →</a>`);
    return m;
  }));
  layer.addTo(map);
  document.getElementById("mapcount").textContent =
    `${pts.length} of ${rows().length} shown (rest have no coordinates)`;
  fitTo(pts);
}
// -------- views: listings | residences | map | wishlist
let view = "table";
function setView(v){
  view = v; mapOn = (v === "map");
  document.getElementById("map").classList.toggle("on", v==="map");
  document.querySelector("main > .tblwrap").classList.toggle("off", v!=="table");
  document.getElementById("resView").classList.toggle("on", v==="res");
  document.getElementById("wishView").classList.toggle("on", v==="wish");
  document.getElementById("legend").style.display = v==="map" ? "flex" : "none";
  const btn = {table:"vTable", res:"vRes", map:"vMap", wish:"vWish"};
  for (const [k,id] of Object.entries(btn))
    document.getElementById(id).classList.toggle("sel", k===v);
  if (v==="res")  renderRes();
  if (v==="wish") renderWish();
  if (v==="map"){
    if(!map) initMap();
    // container was display:none until now -- Leaflet needs a re-measure
    setTimeout(()=>{ map.invalidateSize(); drawMap(); }, 0);
  }
}
document.getElementById("vTable").onclick = ()=>setView("table");
document.getElementById("vRes").onclick   = ()=>setView("res");
document.getElementById("vMap").onclick   = ()=>setView("map");
document.getElementById("vWish").onclick  = ()=>setView("wish");

// -------- residences view
let resSortK = "nActive", resSortDir = -1;
function resRows(){
  const q = document.getElementById("q").value.toLowerCase();
  const min = document.getElementById("fActive").checked ? 1 : 0;
  let r = Object.values(RES).filter(x =>
    x.nActive >= min && (!q || x.name.toLowerCase().includes(q)));
  r.sort((a,b)=>{ const x=a[resSortK], y=b[resSortK];
    if(x==null&&y==null) return 0; if(x==null) return 1; if(y==null) return -1;
    return (typeof x==="string" ? x.localeCompare(y) : (x<y?-1:x>y?1:0))*resSortDir; });
  return r;
}
function renderRes(){
  const r = resRows();
  document.getElementById("count").textContent = r.length+" residences";
  document.querySelector("#restbl tbody").innerHTML = r.map(x=>`
    <tr data-res="${esc(x.key)}">
      <td>${esc(x.nameOnly)}${x.conf===0?' <span class="mut sm">(district only)</span>':""}</td>
      <td class="mut">${esc(x.dist)}</td>
      <td class="num">${x.nActive}</td>
      <td class="num">${x.nSold}</td>
      <td class="num">${x.nTotal}</td>
      <td class="num">${fmtM(x.avgPrice)}</td>
      <td class="num">${fmtM(x.medPrice)}</td>
      <td class="num">${fmtPpm(x.medPpm)}</td>
      <td class="num">${x.medDays==null?"":x.medDays}</td>
      <td class="num">${x.cuts||""}</td>
      <td><button class="btn ghost" onclick="watchAdd('${esc(x.key)}');event.stopPropagation()">★ watch</button></td>
    </tr>`).join("") ||
    '<tr><td colspan="11" class="empty">no residences match</td></tr>';
}
document.querySelectorAll("#restbl th").forEach(th=>th.onclick=()=>{
  const k=th.dataset.rk; if(!k) return;
  resSortDir = (resSortK===k) ? -resSortDir : -1; resSortK=k; renderRes();
});
document.querySelector("#restbl tbody").addEventListener("click",e=>{
  const tr=e.target.closest("tr[data-res]");
  if(tr && e.target.tagName!=="BUTTON") openResidence(tr.dataset.res);
});

function openResidence(key){
  const r = RES[key]; if(!r) return;
  const act = [...r.active].sort((a,b)=>(a.ppm||9e9)-(b.ppm||9e9));
  const sold = [...r.sold].sort((a,b)=>(a.delisted<b.delisted?1:-1));
  const row = l => `<tr><td>${esc((l.title||"").slice(0,44))}</td>
    <td class="num">${l.area??""}</td><td class="num">${l.floor??""}${l.bfloors?"/"+l.bfloors:""}</td>
    <td class="num">${l.year??""}</td><td class="num">${fmtM(l.price)}</td>
    <td class="num">${fmtPpm(l.ppm)}</td><td class="num">${l.days??""}</td>
    <td>${l.delisted?esc(l.delisted):"active"}</td>
    <td><a href="#" onclick="openDetail(${l.id});return false">open</a></td></tr>`;
  const head = `<thead><tr><th>title</th><th class="num">m²</th><th class="num">floor</th>
    <th class="num">year</th><th class="num">price</th><th class="num">₮/m²</th>
    <th class="num">days</th><th>status</th><th></th></tr></thead>`;
  document.getElementById("dbody").innerHTML = `
    <h2>${esc(r.nameOnly)}</h2>
    <div class="mut sm">${esc(r.dist)}${r.conf===0?" · grouped by district only — no residence name in these ads":""}</div>
    <div class="chips">
      <span class="chip"><b>${r.nActive}</b> listed now</span>
      <span class="chip"><b>${r.nSold}</b> sold / gone</span>
      <span class="chip"><b>${r.nTotal}</b> ever seen</span>
      <span class="chip">avg <b>${fmtM(r.avgPrice)}</b></span>
      <span class="chip">median <b>${fmtM(r.medPrice)}</b></span>
      <span class="chip"><b>${fmtPpm(r.medPpm)}</b>/m²</span>
      ${r.medDays!=null?`<span class="chip">median <b>${r.medDays}</b> days to sell</span>`:""}
      ${r.cuts?`<span class="chip bad"><b>${r.cuts}</b> price cuts</span>`:""}
    </div>
    <div class="wrow" style="margin:10px 0">
      <button class="btn" onclick="watchAdd('${esc(r.key)}')">★ add to wishlist</button>
    </div>
    <h3>Listed now (${act.length})</h3>
    ${act.length?`<div class="tblwrap"><table class="small-t">${head}
      <tbody>${act.map(row).join("")}</tbody></table></div>`:'<div class="empty">none</div>'}
    ${sold.length?`<h3>Sold / delisted (${sold.length})</h3>
      <div class="tblwrap"><table class="small-t">${head}
      <tbody>${sold.slice(0,60).map(row).join("")}</tbody></table></div>`:""}`;
  document.getElementById("detail").classList.add("open");
}

// -------- wishlist (localStorage; the page is static, so this lives in the
// browser only -- clearing site data clears it)
const WKEY = "unegui.wishlist.v1";
function wlLoad(){ try { return JSON.parse(localStorage.getItem(WKEY)) || []; }
                   catch(e){ return []; } }
function wlSave(w){ try { localStorage.setItem(WKEY, JSON.stringify(w)); }
                    catch(e){ alert("Could not save the wishlist (storage blocked)."); } }
function watchAdd(key){
  const r = RES[key]; if(!r) return;
  const suggested = r.medPrice ? Math.round(r.medPrice/1e6) : 200;
  const ans = prompt(
    `Wishlist — ${r.nameOnly}\n\nAlert me when a listing here is at or below (сая ₮):`,
    suggested);
  if(ans===null) return;
  const maxP = Math.round(parseFloat(String(ans).replace(",","."))*1e6);
  if(!maxP || maxP<=0){ alert("Please enter a number, e.g. 220"); return; }
  const w = wlLoad().filter(x=>x.res!==key);
  w.push({res:key, maxPrice:maxP, added:TODAY});
  wlSave(w); updateWishBadge();
  if(confirm(`Saved: ${r.nameOnly} at or below ${fmtM(maxP)}.\n\nOpen the wishlist now?`))
    setView("wish");
}
function watchRemove(key){
  wlSave(wlLoad().filter(x=>x.res!==key)); updateWishBadge(); renderWish();
}
function wlMatches(w){
  const r = RES[w.res]; if(!r) return [];
  return r.active.filter(l=>l.price && l.price<=w.maxPrice)
                 .sort((a,b)=>a.price-b.price);
}
function updateWishBadge(){
  const n = wlLoad().reduce((a,w)=>a+wlMatches(w).length, 0);
  document.getElementById("wishBadge").textContent = n || "";
}
function renderWish(){
  const w = wlLoad();
  document.getElementById("count").textContent = w.length+" watched";
  if(!w.length){
    document.getElementById("wishView").innerHTML =
      `<div class="empty">Nothing on the wishlist yet.<br><br>
       Open <b>Residences</b>, pick one, and hit <b>★ watch</b> to be told when
       something there comes up at or below your price.</div>`;
    return;
  }
  document.getElementById("wishView").innerHTML = w.map(x=>{
    const r = RES[x.res], ms = wlMatches(x);
    if(!r) return "";
    const fresh = ms.filter(l=>l.first > x.added);
    return `<div class="wcard">
      <div class="wrow" style="justify-content:space-between">
        <div>
          <h3>${esc(r.nameOnly)} <span class="mut sm">${esc(r.dist)}</span></h3>
          <div class="mut sm">watching at or below <b>${fmtM(x.maxPrice)}</b>
            · residence median ${fmtM(r.medPrice)} · added ${x.added}</div>
        </div>
        <div class="wrow">
          <span class="chip"><b>${ms.length}</b> match${ms.length===1?"":"es"}</span>
          ${fresh.length?`<span class="newbadge">${fresh.length} new since added</span>`:""}
          <button class="btn ghost" onclick="watchAdd('${esc(x.res)}')">edit price</button>
          <button class="btn danger" onclick="watchRemove('${esc(x.res)}')">remove</button>
        </div>
      </div>
      ${ms.length ? `<div class="tblwrap" style="margin-top:10px">
        <table class="small-t"><thead><tr><th>title</th><th class="num">m²</th>
        <th class="num">floor</th><th class="num">price</th><th class="num">₮/m²</th>
        <th class="num">vs median</th><th></th></tr></thead><tbody>${
        ms.map(l=>`<tr><td>${l.first>x.added?'<span class="newbadge">new</span> ':""}${
          esc((l.title||"").slice(0,42))}</td>
          <td class="num">${l.area??""}</td>
          <td class="num">${l.floor??""}${l.bfloors?"/"+l.bfloors:""}</td>
          <td class="num"><b>${fmtM(l.price)}</b></td>
          <td class="num">${fmtPpm(l.ppm)}</td>
          <td class="num ${l.vsMed==null?"":l.vsMed<0?"good":"bad"}">${
            l.vsMed==null?"":(l.vsMed>0?"+":"")+l.vsMed.toFixed(0)+"%"}</td>
          <td><a href="#" onclick="openDetail(${l.id});return false">open</a></td></tr>`).join("")
        }</tbody></table></div>`
      : `<div class="mut sm" style="margin-top:8px">Nothing at that price yet —
         cheapest here right now is ${fmtM(r.active.reduce((m,l)=>
           l.price&&(!m||l.price<m)?l.price:m, null))}.</div>`}
    </div>`;
  }).join("");
}

["q","fComplex","fActive","fDups"].forEach(id=>
  document.getElementById(id).addEventListener("input",()=>{
    if(view==="res") renderRes(); else render();
  }));
document.querySelectorAll("#main th").forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; if(!k)return;
  sortDir = (sortK===k) ? -sortDir : 1; sortK=k; render();
});

// -------- detail panel
function spark(sn){
  const pts=sn.filter(s=>s[1]);
  if(pts.length<2) return "";
  const w=680,h=90,p=8;
  const t0=+new Date(pts[0][0]), t1=+new Date(pts[pts.length-1][0])||t0+1;
  const vs=pts.map(s=>s[1]), lo=Math.min(...vs), hi=Math.max(...vs)||1;
  const X=t=>p+(w-2*p)*((+new Date(t)-t0)/Math.max(t1-t0,1));
  const Y=v=>h-p-(h-2*p)*((v-lo)/Math.max(hi-lo,1));
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline fill="none" stroke="var(--acc)" stroke-width="2"
      points="${pts.map(s=>X(s[0]).toFixed(1)+","+Y(s[1]).toFixed(1)).join(" ")}"/>
    <text x="${p}" y="12" fill="var(--mut)" font-size="10">${fmtM(hi)}</text>
    <text x="${p}" y="${h-2}" fill="var(--mut)" font-size="10">${fmtM(lo)}</text>
  </svg>`;
}
function timelineTable(sn, label){
  const prevBy={};                       // delta per ad, not across interleaved ads
  const tr=sn.map(s=>{
    const key=label ? (s[4]||"") : "";
    const prev=prevBy[key];
    const d = prev!=null && s[1]!=null && s[1]!==prev
      ? ` <span class="${s[1]<prev?"good":"bad"}">(${s[1]<prev?"":"+"}${fmtM(s[1]-prev)})</span>` : "";
    if(s[1]!=null) prevBy[key]=s[1];
    return `<tr><td>${s[0]}</td>${label?`<td>${esc(s[4]||"")}</td>`:""}
      <td class="num">${fmtM(s[1])}${d}</td>
      <td class="num">${s[2]?fmtM(s[2]):""}</td><td class="num">${s[3]??""}</td></tr>`;
  }).join("");
  return `<div class="tblwrap"><table class="small-t"><thead><tr><th>date</th>${
    label?"<th>ad</th>":""}<th class="num">price</th><th class="num">was</th>
    <th class="num">views</th></tr></thead><tbody>${tr}</tbody></table></div>`;
}
function kv(k,v){ return v?`<div><div class="k">${k}</div>${esc(v)}</div>`:""; }
function openDetail(id){
  const l=L.find(x=>x.id===id); if(!l) return;
  const sn=SNAP[id]||[], ev=EV[id]||[];
  let html=`<h2><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a></h2>
    <div class="mut sm">#${l.id} · ${esc([l.district,l.sub,l.khoroo].filter(Boolean).join(", "))}
      · first seen ${l.first} · ${l.delisted?"delisted "+l.delisted:"last seen "+l.last}
      · ${l.days} days on market</div>
    <div class="chips">
      <span class="chip"><b>${fmtM(l.price)}</b></span>
      <span class="chip">${fmtPpm(l.ppm)}/m²</span>
      ${l.vsMed!=null?`<span class="chip ${l.vsMed<0?"good":"bad"}">${
        (l.vsMed>0?"+":"")+l.vsMed.toFixed(1)}% vs ${esc(l.resName)} median</span>`:""}
      ${l.enriched?'<span class="chip">LLM ✓</span>':'<span class="chip mut">regex only</span>'}
    </div>
    <div class="grid">
      ${kv("area",l.area&&l.area+" m²")}${kv("floor",l.floor&&l.floor+(l.bfloors?"/"+l.bfloors:""))}
      ${kv("commissioned",l.year)}${kv("balcony",l.balcony)}${kv("garage",l.garage)}
      ${kv("windows",[l.window,l.wcount].filter(Boolean).join(" × "))}
      ${kv("door",l.door)}${kv("floor mat.",l.flmat)}${kv("elevator",l.elevator)}
      ${kv("payment",l.payment)}${kv("orientation",l.orientation)}
      ${kv("bathrooms",l.bathrooms)}${kv("ceiling",l.ceiling_m&&l.ceiling_m+" m")}
      ${kv("certificate",l.certificate)}${kv("furniture",l.furniture)}
      ${kv("renovation",l.renovation)}${kv("landmarks",l.landmarks)}
      ${kv("notes",l.notes)}${kv("phone",l.phone)}${kv("seller",l.seller)}
      ${kv("seller since",l.since)}${kv("posted",l.posted)}
    </div>
    ${l.desc?`<div class="desc">${esc(l.desc)}</div>`:""}
    <h3>Price timeline</h3>${spark(sn)}${timelineTable(sn)}
    ${ev.length?`<h3>Events</h3><div class="tblwrap"><table class="small-t"><tbody>${
      ev.map(e=>`<tr><td>${e[0]}</td><td>${e[1]}</td><td class="num">${
        e[2]?fmtM(+e[2]):""}</td><td class="num">${e[3]?fmtM(+e[3]):""}</td></tr>`).join("")
      }</tbody></table></div>`:""}`;

  if (l.group){
    const g=l.group;
    html+=`<h3>Same apartment — ${g.length} listings (${
      l.dup===2?"CONFIRMED relist":"LIKELY multi-agent"}) · price spread across agents</h3>
      <div class="tblwrap"><table class="small-t"><thead><tr><th>ad</th><th>seller</th>
      <th>phone</th><th class="num">price</th><th class="num">₮/m²</th>
      <th>status</th><th></th></tr></thead><tbody>${
      g.map(x=>`<tr><td>#${x.id}${x.rep?" ★":""}</td><td>${esc(x.seller||"")}</td>
        <td>${esc(x.phone||"")}</td><td class="num">${fmtM(x.price)}</td>
        <td class="num">${fmtPpm(x.ppm)}</td><td>${x.delisted?"gone":"active"}</td>
        <td><a href="#" onclick="openDetail(${x.id});return false">open</a></td></tr>`).join("")
      }</tbody></table></div>
      <h3>Merged timeline</h3>${timelineTable(
        g.flatMap(x=>(SNAP[x.id]||[]).map(s=>[...s.slice(0,4),"#"+x.id]))
         .sort((a,b)=>a[0]<b[0]?-1:1), true)}`;
  }

  const same=L.filter(x=>x.res && x.res===l.res && x.id!==l.id && !x.delisted)
              .sort((a,b)=>(a.ppm||9e9)-(b.ppm||9e9)).slice(0,25);
  if (same.length){
    html+=`<h3>Other active listings in ${esc(l.resName)} (median ${fmtPpm(cmed[l.res])}/m²)</h3>
      <div class="tblwrap"><table class="small-t"><thead><tr><th>title</th>
      <th class="num">m²</th><th class="num">floor</th><th class="num">price</th>
      <th class="num">₮/m²</th><th></th></tr></thead><tbody>${
      same.map(x=>`<tr><td>${esc((x.title||"").slice(0,40))}</td>
        <td class="num">${x.area??""}</td><td class="num">${x.floor??""}</td>
        <td class="num">${fmtM(x.price)}</td><td class="num">${fmtPpm(x.ppm)}</td>
        <td><a href="#" onclick="openDetail(${x.id});return false">open</a></td></tr>`).join("")
      }</tbody></table></div>`;
  }

  document.getElementById("dbody").innerHTML=html;
  document.getElementById("detail").classList.add("open");
}
document.querySelector("#main tbody").addEventListener("click",e=>{
  const tr=e.target.closest("tr[data-id]");
  if(tr) openDetail(+tr.dataset.id);
});
document.getElementById("close").onclick=()=>
  document.getElementById("detail").classList.remove("open");
document.addEventListener("keydown",e=>{ if(e.key==="Escape")
  document.getElementById("detail").classList.remove("open"); });

render();
updateWishBadge();
</script>
</body>
</html>
"""


def main():
    data = load_data()
    payload = json.dumps(data, ensure_ascii=False,
                         separators=(",", ":")).replace("</", "<\\/")
    html = (HTML.replace("__GENERATED__", data["generated"])
                .replace("__DATA__", payload))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({len(data['listings'])} listings, "
          f"{sum(len(v) for v in data['snapshots'].values())} snapshots, "
          f"{len(html)//1024} KiB)")


if __name__ == "__main__":
    main()
