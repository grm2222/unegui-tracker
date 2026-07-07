# unegui-tracker

Tracks 2-room apartment sale listings on unegui.mn over time to reveal market
dynamics: real time-on-market, price cuts, delistings (sold signal), duplicate
listings of the same apartment by multiple agents, and per-complex price levels.

## Architecture

- `scraper.py` — daily crawl (GitHub Actions, cron 06:00 UB time).
  Crawls category list pages (`/l-hdlh/l-hdlh-zarna/oron-suuts-zarna/2-r/?page=N`),
  records a price/views snapshot for EVERY active ad each day, fetches the full
  detail page only for NEW ads, detects `price_down` / `price_up` / `delisted`
  events, runs free regex extraction on descriptions.
- `dashboard.py` — regenerates `docs/index.html` (self-contained, GitHub Pages)
  from the DB: value ranking vs complex median ₮/m², per-listing timeline,
  same-complex listings table, duplicate detection.
- `enrich_io.py` — export/import for LLM enrichment done BY CLAUDE in
  Cowork/Claude Code sessions (no API key, on demand, not daily).
- `.github/workflows/crawl.yml` — cron + manual trigger; commits
  `data/unegui.db` + `docs/index.html` back to the repo. Git history of the DB
  IS the historical record.

## Database (SQLite, data/unegui.db)

- `listings` — one row per ad: attributes parsed from the detail page
  (area_m2, floor, building_floors, commissioned_year, balcony, garage,
  window_type/count, door, floor_material, elevator, payment_terms), location
  (district, sub_location=complex/town name, khoroo, latitude, longitude),
  contact (phone from tel: link, seller_name, seller_since), description,
  first_seen / last_seen / delisted_at, `rx_*` regex fields, `llm_*` fields,
  `enriched` flag (0 = awaiting LLM pass).
- `snapshots` — (ad_id, crawl_date) → price_mnt, old_price_mnt (struck-through
  price from list card), views. One row per ad per day.
- `events` — new / price_down / price_up / delisted with old/new values.

Complex/town grouping key = `sub_location` (e.g. "King Tower",
"Нарны хороолол", "14-р хороолол"), which unegui provides in the location
breadcrumb. Same-apartment matching (dashboard.py, `dupMatch`) requires the
EXACT same area_m2 in all tiers (a genuine relist/duplicate never changes the
area — verified against live data), then:
- CONFIRMED (2): same seller phone or name + same floor, building_floors,
  commissioned_year (nulls tolerated) — 100% the same apartment (relist).
- LIKELY (1): different sellers, floor & year equal when both known — the
  typical one-apartment-many-agents case.
Duplicates get a merged timeline,
a cross-agent price-spread table, and are excluded from the complex median.
Known limitation: identical units on the same floor of different buildings in
one complex can over-group — when enriching, note "different unit than №X" in
llm_notes if the descriptions prove they differ, and refine sameUnit if it
becomes a real problem.

## Site parsing notes (re-verified 2026-07-07 on live pages)

- unegui.mn sits behind a **Cloudflare JS challenge**: plain `requests`/curl
  gets 403 (`cf-mitigated: challenge`). `curl_cffi` with
  `impersonate="chrome"` passes it (verified live). Do NOT override
  User-Agent — it must match the impersonated TLS fingerprint.
- Detail pages are server-rendered; no JS needed:
  - Structured attributes: PRIMARY source is the on-page attribute list —
    `<li><span class="key-chars">Label:</span> <a|span class="value-chars">
    Value</a></li>`. `<meta name="keywords">` has the same "Label Value"
    pairs comma-separated but is only a fallback: it does NOT contain area
    ("Талбай"), which exists only in key-chars. Elevator label is
    "Цахилгаан шаттай эсэх" (not "Лифт"). See ATTR_MAP in scraper.py.
  - Title: `<h1>` (a list-card anchor's text can be the PRICE link, not the title).
  - `<meta name="description">` = full listing description.
  - Location: `<span itemprop="address">Хан-Уул, Нисэх</span>` =
    district, sub_location. Fallback line "Байршил: Улаанбаатар — Хан-Уул —
    Нисэх" uses em-dash separators.
  - Seller: `<div class="author-info" itemtype=".../Person"><div
    class="author-name" itemprop="name">NAME</div>`. Do not match
    `[class*=author]` broadly — `phone-author` is the "Дугаар харах" button.
  - Phone: `tel:+976XXXXXXXX` link (full number, even though page shows
    "99XX-XXXX" masked).
  - Coordinates: static map URL `/api/v2/geo/static/streets/{LON}/{LAT}/14/...`
    — longitude comes FIRST. Also in `data-default-lat`/`data-default-lng`.
  - "Зарын дугаар: NNNNN", "Нийтэлсэн: YYYY-MM-DD HH:MM", "Үзсэн : NN".
  - Old (struck-through) prices appear only on LIST cards, as a second
    "X сая" value near the first (~3 of 60 cards on a live page).
- List pages: 60 cards/page; `?page=2` confirmed to return a different 60
  (0 overlap). ~2,000 ads ≈ 34 pages.
- Ads are Mongolian Cyrillic. Prices in "сая" (millions MNT), rarely "тэрбум".
- VIP ads repeat across pages — scraper dedupes by ad_id within a run.
- All of the above verified live 2026-07-07 via `python scraper.py
  --max-pages 1`: 60/60 ads parsed with area, district, sub_location, phone,
  coords, seller_name, views.

## Things to VERIFY on first real GitHub Actions run (then delete this section)

1. GitHub Actions runner IP not blocked: curl_cffi passes Cloudflare from a
   home IP (verified 2026-07-07); datacenter IPs get challenged harder. If
   the workflow logs 403s: add longer backoff, reduce rate, or move the crawl
   to a VPS/home machine (repo layout stays the same).
2. Spot-check a few `old_price_mnt` values against strikethrough prices on
   the live site (regex found 3/60 on page 1; plausible but eyeball it).
3. Full crawl size/time: ~34 list pages + ~2,000 detail pages ≈ 45 min at
   1.2 s delay — fits the 90 min workflow timeout. Subsequent runs fetch
   only new ads (~50–150/day).

## LLM enrichment workflow (Claude does this, occasionally, no API)

When the user asks to "enrich" (in Cowork or Claude Code):

1. `python enrich_io.py export > pending.json` — pending descriptions
   (`enriched = 0`).
2. Read pending.json and for each item extract these fields FROM THE
   DESCRIPTION TEXT (leave "" when absent, never guess):
   - `llm_orientation` — window facing (баруун/зүүн/урд/хойд combinations)
   - `llm_bathrooms` — count of ариун цэврийн өрөө if stated
   - `llm_ceiling_m` — ceiling height
   - `llm_certificate` — гэрчилгээ бэлэн / барьцаанд / комиссын акт гарсан
   - `llm_furniture` — furniture that stays
   - `llm_renovation` — renovation state (шинэ / бүрэн засвартай / засвар
     шаардлагатай ...)
   - `llm_landmarks` — nearby schools/kindergartens/landmarks, ";"-separated
   - `llm_notes` — urgency signals (ҮНЭ БУУЛАА, яаралтай, доод үнэ), garage
     price, contradictions between description and structured attributes
     (e.g. different commissioned year), price-per-m² claims
3. Write results to `pending_filled.json` in the format documented in
   `enrich_io.py`, then `python enrich_io.py import pending_filled.json`.
4. `python dashboard.py` to refresh, then commit:
   `git add -A && git commit -m "enrich N listings" && git push`.

Batch size guidance: process in chunks of ~50 descriptions to keep each pass
accurate. Total pending after initial crawl will be ~2,000 — fine to spread
over several sessions; dashboard falls back to `rx_*` regex fields meanwhile.

## Common commands

```bash
python scraper.py --max-pages 2   # quick smoke test
python scraper.py                 # full daily crawl
python dashboard.py               # rebuild docs/index.html
python enrich_io.py export        # dump pending descriptions
sqlite3 data/unegui.db "SELECT event_type, COUNT(*) FROM events GROUP BY 1"
```

## Setup (one-time)

1. Create GitHub repo, push these files.
2. Settings → Pages → deploy from branch, folder `/docs` → dashboard gets a
   public URL.
3. Actions tab → run "Daily crawl" manually once (workflow_dispatch) and check
   the log against the VERIFY list above.

## Conventions

- Don't reformat the DB schema casually — git history of unegui.db is the
  dataset; schema migrations need an ALTER TABLE script.
- Prices stored as integer MNT (226 сая → 226000000).
- All dates ISO (YYYY-MM-DD); crawl runs at 06:00 Asia/Ulaanbaatar.
- Keep request delay ≥ 1 s; this is a public classifieds site — be polite.
