#!/usr/bin/env python3
"""Daily crawler for unegui.mn 2-room apartment sale listings.

Crawls the category list pages, records a price/views snapshot for every
active ad, fetches the full detail page only for NEW ads, detects
price_down / price_up / delisted events and runs free regex extraction
on descriptions.

Usage:
    python scraper.py                # full daily crawl
    python scraper.py --max-pages 2  # smoke test (delist detection disabled)
"""

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# unegui.mn sits behind a Cloudflare JS challenge; plain `requests` gets 403.
# curl_cffi impersonates a real Chrome TLS fingerprint and passes it.
from curl_cffi import requests
from bs4 import BeautifulSoup

BASE = "https://www.unegui.mn"
CATEGORY_PATH = "/l-hdlh/l-hdlh-zarna/oron-suuts-zarna/2-r/"
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "unegui.db"
REQUEST_DELAY = 1.2  # seconds; keep >= 1 s, this is a public classifieds site
UB_TZ = timezone(timedelta(hours=8))  # Asia/Ulaanbaatar

# no User-Agent override: it must match the impersonated Chrome fingerprint
HEADERS = {"Accept-Language": "mn,en;q=0.8"}

# Structured attributes: primary source is the detail-page attribute list
# (<span class="key-chars">Label:</span> <a|span class="value-chars">Value</a>),
# fallback is <meta name="keywords"> "Label Value" pairs, comma-separated.
# Longest label first so "Цонхны тоо" wins over "Цонх".
# NOTE (verified live 2026-07-07): area ("Талбай") appears ONLY in the
# key-chars list, not in meta keywords; elevator label is
# "Цахилгаан шаттай эсэх" (old "Лифт" kept as fallback).
ATTR_MAP = [
    ("Цахилгаан шаттай эсэх", "elevator", "str"),
    ("Ашиглалтанд орсон он", "commissioned_year", "int"),
    ("Барилгын давхар", "building_floors", "int"),
    ("Хэдэн давхарт", "floor", "int"),
    ("Төлбөрийн нөхцөл", "payment_terms", "str"),
    ("Цонхны тоо", "window_count", "int"),
    ("Талбай", "area_m2", "float"),
    ("Хаалга", "door", "str"),
    ("Гараж", "garage", "str"),
    ("Цонх", "window_type", "str"),
    ("Тагт", "balcony", "str"),
    ("Лифт", "elevator", "str"),
    ("Шал", "floor_material", "str"),
]

UB_DISTRICTS = [
    "Баянгол", "Баянзүрх", "Сонгинохайрхан", "Сүхбаатар", "Хан-Уул",
    "Чингэлтэй", "Багануур", "Багахангай", "Налайх",
]

PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(сая|тэрбум)")

LISTING_FIELDS = [
    "ad_id", "url", "title",
    "area_m2", "floor", "building_floors", "commissioned_year",
    "balcony", "garage", "window_type", "window_count", "door",
    "floor_material", "elevator", "payment_terms",
    "district", "sub_location", "khoroo", "latitude", "longitude",
    "phone", "seller_name", "seller_since",
    "description", "posted_date",
    "first_seen", "last_seen", "delisted_at",
    "rx_orientation", "rx_bathrooms", "rx_ceiling_m", "rx_certificate",
    "rx_furniture", "rx_renovation", "rx_landmarks", "rx_notes",
    "llm_orientation", "llm_bathrooms", "llm_ceiling_m", "llm_certificate",
    "llm_furniture", "llm_renovation", "llm_landmarks", "llm_notes",
    "enriched",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    ad_id INTEGER PRIMARY KEY,
    url TEXT, title TEXT,
    area_m2 REAL, floor INTEGER, building_floors INTEGER,
    commissioned_year INTEGER,
    balcony TEXT, garage TEXT, window_type TEXT, window_count INTEGER,
    door TEXT, floor_material TEXT, elevator TEXT, payment_terms TEXT,
    district TEXT, sub_location TEXT, khoroo TEXT,
    latitude REAL, longitude REAL,
    phone TEXT, seller_name TEXT, seller_since TEXT,
    description TEXT, posted_date TEXT,
    first_seen TEXT, last_seen TEXT, delisted_at TEXT,
    rx_orientation TEXT, rx_bathrooms TEXT, rx_ceiling_m TEXT,
    rx_certificate TEXT, rx_furniture TEXT, rx_renovation TEXT,
    rx_landmarks TEXT, rx_notes TEXT,
    llm_orientation TEXT, llm_bathrooms TEXT, llm_ceiling_m TEXT,
    llm_certificate TEXT, llm_furniture TEXT, llm_renovation TEXT,
    llm_landmarks TEXT, llm_notes TEXT,
    enriched INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS snapshots (
    ad_id INTEGER,
    crawl_date TEXT,
    price_mnt INTEGER,
    old_price_mnt INTEGER,
    views INTEGER,
    PRIMARY KEY (ad_id, crawl_date)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_id INTEGER,
    event_date TEXT,
    event_type TEXT,
    old_value TEXT,
    new_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON snapshots(crawl_date);
CREATE INDEX IF NOT EXISTS idx_events_ad ON events(ad_id);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def fetch(session, url, tries=3):
    """GET with retries + backoff; returns HTML text or None."""
    for attempt in range(tries):
        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.text
            print(f"  HTTP {resp.status_code} for {url}", file=sys.stderr)
            if resp.status_code in (403, 429):
                time.sleep(15 * (attempt + 1))
                continue
        except Exception as exc:
            print(f"  {exc.__class__.__name__} for {url}", file=sys.stderr)
        time.sleep(5 * (attempt + 1))
    return None


def parse_price(match):
    value = float(match.group(1).replace(",", "."))
    mult = 1_000_000_000 if match.group(2) == "тэрбум" else 1_000_000
    return int(value * mult)


def coerce(raw, typ):
    if typ == "int":
        m = re.search(r"\d+", raw)
        return int(m.group()) if m else None
    if typ == "float":
        m = re.search(r"\d+(?:[.,]\d+)?", raw)
        return float(m.group().replace(",", ".")) if m else None
    return raw or None


# ---------------------------------------------------------------- list pages

def parse_list_page(html):
    """Return {ad_id: card dict} for one category list page."""
    soup = BeautifulSoup(html, "html.parser")
    cards = {}
    for a in soup.select('a[href*="/adv/"]'):
        m = re.search(r"/adv/(\d+)", a.get("href", ""))
        if not m:
            continue
        ad_id = int(m.group(1))
        title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if re.match(r"^[\d.,\s]+(?:сая|тэрбум|₮)", title):
            title = ""  # the price link, not the title link

        # climb to the smallest ancestor that contains a price -- that is the card
        node, card_text = a, ""
        for _ in range(6):
            if node.parent is None:
                break
            node = node.parent
            card_text = node.get_text(" ", strip=True)
            if PRICE_RE.search(card_text):
                break

        prices = list(PRICE_RE.finditer(card_text))
        price = parse_price(prices[0]) if prices else None
        old_price = None
        if len(prices) >= 2:
            second = parse_price(prices[1])
            if second != price:
                old_price = second  # struck-through price on the list card

        views = None
        vm = re.search(r"Үзсэн\D{0,4}([\d\s,]+)", card_text)
        if vm:
            views = int(re.sub(r"\D", "", vm.group(1)) or 0) or None

        card = {
            "href": a.get("href"),
            "title": title,
            "price_mnt": price,
            "old_price_mnt": old_price,
            "views": views,
        }
        prev = cards.get(ad_id)
        if prev is None:
            cards[ad_id] = card
        else:  # keep the most complete duplicate occurrence (VIP blocks etc.)
            for k, v in card.items():
                if v and not prev.get(k):
                    prev[k] = v
    return cards


def crawl_list_pages(session, max_pages=None):
    """Crawl category pages; returns {ad_id: card}. Dedupes VIP repeats."""
    ads, page, prev_ids = {}, 1, None
    while True:
        if max_pages and page > max_pages:
            break
        url = BASE + CATEGORY_PATH + (f"?page={page}" if page > 1 else "")
        html = fetch(session, url)
        if html is None:
            break
        page_ads = parse_list_page(html)
        ids = set(page_ads)
        if not ids:
            break
        if prev_ids is not None and ids == prev_ids:
            # pagination param ignored by the site -> same page came back
            print(f"page {page}: identical to previous page, stopping "
                  "(check pagination param, see VERIFY list)", file=sys.stderr)
            break
        fresh = 0
        for ad_id, card in page_ads.items():
            if ad_id not in ads:
                ads[ad_id] = card
                fresh += 1
            else:
                for k, v in card.items():
                    if v and not ads[ad_id].get(k):
                        ads[ad_id][k] = v
        print(f"page {page}: {len(ids)} cards, {fresh} first-seen this run")
        if fresh == 0 and page > 3:
            break  # trailing pages are all VIP repeats
        prev_ids = ids
        page += 1
        if page > 400:
            break
        time.sleep(REQUEST_DELAY)
    return ads


# --------------------------------------------------------------- detail page

def parse_detail(html, url):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    d = {"url": url}

    h1 = soup.find("h1")
    if h1:
        d["title"] = h1.get_text(" ", strip=True)
    else:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            d["title"] = og["content"].strip()

    # attribute list on the page: <span class="key-chars">Label:</span> + value
    for key_el in soup.select(".key-chars"):
        row = key_el.parent
        val_el = row.select_one(".value-chars") if row else None
        if not val_el:
            continue
        label = key_el.get_text(strip=True).rstrip(":")
        for attr_label, field, typ in ATTR_MAP:
            if label == attr_label:
                if d.get(field) is None:
                    d[field] = coerce(val_el.get_text(" ", strip=True), typ)
                break

    # fallback: <meta name="keywords"> "Label Value" pairs (no area there)
    kw = soup.find("meta", attrs={"name": "keywords"})
    if kw and kw.get("content"):
        for chunk in kw["content"].split(","):
            chunk = chunk.strip()
            for label, field, typ in ATTR_MAP:
                if chunk.startswith(label):
                    if d.get(field) is None:
                        d[field] = coerce(chunk[len(label):].strip(), typ)
                    break

    desc = soup.find("meta", attrs={"name": "description"})
    d["description"] = (desc.get("content") or "").strip() if desc else ""

    tel = soup.select_one('a[href^="tel:"]')
    if tel:
        d["phone"] = tel["href"].replace("tel:", "").strip()

    # static map URL: longitude comes FIRST
    m = re.search(r"/api/v2/geo/static/streets/(\d+\.\d+)/(\d+\.\d+)/", html)
    if m:
        d["longitude"], d["latitude"] = float(m.group(1)), float(m.group(2))

    m = re.search(r"Үзсэн\s*:?\s*([\d\s,]+)", text)
    if m:
        d["views"] = int(re.sub(r"\D", "", m.group(1)) or 0)

    m = re.search(r"Нийтэлсэн\s*:?\s*([^\n]{3,40}?)(?:\s{2,}|,|Зарын|Үзсэн|$)", text)
    if m:
        d["posted_date"] = m.group(1).strip()

    # location (verified live 2026-07-07):
    #   <span itemprop="address">Хан-Уул, Нисэх</span>
    #   fallback: "Байршил: Улаанбаатар — Хан-Уул — Нисэх"
    addr = soup.select_one('[itemprop="address"]')
    parts = []
    if addr:
        parts = [p.strip() for p in addr.get_text(" ", strip=True).split(",")]
    else:
        loc_m = re.search(r"Байршил\s*:?\s*(.{3,120}?)(?:\s{2,}|$)", text)
        if loc_m:
            parts = [p.strip() for p in re.split(r"[—,]", loc_m.group(1))]
    parts = [p for p in parts if p and p != "Улаанбаатар"]
    for i, part in enumerate(parts):
        hit = next((dist for dist in UB_DISTRICTS if dist in part), None)
        if hit:
            d["district"] = hit
            # the part after the district is the complex/town name
            rest = [p for p in parts[i + 1:] if p]
            if rest:
                d["sub_location"] = rest[0]
            break
    else:
        if parts:  # outside UB: aimag, soum/town
            d["district"] = parts[0]
            if len(parts) > 1:
                d["sub_location"] = parts[1]
    km = re.search(r"(\d+)\s*-?\s*р?\s*хороо(?!лол)",
                   " ".join(parts) + " " + d["description"])
    if km:
        d["khoroo"] = f"{km.group(1)}-р хороо"

    # seller: <div class="author-info" itemtype="schema.org/Person">
    #           <div class="author-name" itemprop="name">NAME</div>
    seller = soup.select_one('.author-name, .author-info [itemprop="name"]')
    if seller:
        name = seller.get_text(" ", strip=True)
        if 0 < len(name) <= 60:
            d["seller_name"] = name
    m = re.search(r"бүртгүүлсэн\s*:?\s*(\d{4})(?:\s*оноос)?", text, re.IGNORECASE)
    if m:
        d["seller_since"] = m.group(1)

    return d


# ---------------------------------------------------------- regex enrichment

RX_FIELDS = ["rx_orientation", "rx_bathrooms", "rx_ceiling_m", "rx_certificate",
             "rx_furniture", "rx_renovation", "rx_landmarks", "rx_notes"]


def rx_extract(desc):
    """Free (no-LLM) extraction from the description text. '' when absent."""
    d = {f: "" for f in RX_FIELDS}
    if not desc:
        return d
    low = desc.lower()

    dirs = re.findall(
        r"(баруун|зүүн|урд|өмнөд|хойд)(?=[\w\s,-]{0,25}(?:тал|цонх|харсан|харна|зүг))",
        low)
    if dirs:
        d["rx_orientation"] = ",".join(dict.fromkeys(dirs))

    m = (re.search(r"(\d)\s*(?:ш\s*)?(?:ариун\s*цэврийн\s*өрөө|санузел|ац\s*ө)", low)
         or re.search(r"ариун\s*цэврийн\s*өрөө\s*[:\-]?\s*(\d)", low))
    if m:
        d["rx_bathrooms"] = m.group(1)

    m = (re.search(r"тааз(?:ны)?\s*(?:өндөр)?\s*[:\-]?\s*(\d(?:[.,]\d+)?)\s*(?:м|метр)", low)
         or re.search(r"(\d[.,]\d+)\s*(?:м|метр)(?:ийн)?\s*(?:өндөр\s*)?тааз", low))
    if m:
        d["rx_ceiling_m"] = m.group(1).replace(",", ".")

    certs = [kw for kw in ("гэрчилгээ бэлэн", "гэрчилгээтэй", "барьцаанд",
                           "комиссын акт") if kw in low]
    d["rx_certificate"] = "; ".join(certs)

    furn = [kw for kw in ("бүрэн тавилгатай", "тавилгатай", "тавилгын хамт",
                          "цахилгаан бараа") if kw in low]
    d["rx_furniture"] = "; ".join(dict.fromkeys(furn))

    reno = [kw for kw in ("евро засвар", "шинэ засвар", "бүрэн засвар",
                          "засвар шаардлагатай", "засваргүй", "хуучин засвар")
            if kw in low]
    d["rx_renovation"] = "; ".join(reno)

    lands = re.findall(r"([\w№\- ]{0,25}(?:сургууль|цэцэрлэг|эмнэлэг|их дэлгүүр))", low)
    lands = [s.strip() for s in dict.fromkeys(lands) if s.strip()][:5]
    d["rx_landmarks"] = "; ".join(lands)

    notes = [kw for kw in ("үнэ буу", "яаралтай", "доод үнэ", "тохиролцоно")
             if kw in low]
    d["rx_notes"] = "; ".join(notes)
    return d


# ----------------------------------------------------------------- db helpers

def add_event(con, ad_id, date, etype, old=None, new=None):
    exists = con.execute(
        "SELECT 1 FROM events WHERE ad_id=? AND event_date=? AND event_type=?",
        (ad_id, date, etype)).fetchone()
    if not exists:
        con.execute(
            "INSERT INTO events (ad_id, event_date, event_type, old_value, new_value)"
            " VALUES (?,?,?,?,?)", (ad_id, date, etype, old, new))


def insert_listing(con, d):
    cols = ",".join(LISTING_FIELDS)
    ph = ",".join("?" * len(LISTING_FIELDS))
    con.execute(f"INSERT OR REPLACE INTO listings ({cols}) VALUES ({ph})",
                [d.get(c) for c in LISTING_FIELDS])


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-pages", type=int, default=None,
                    help="limit list pages (smoke test; disables delist detection)")
    args = ap.parse_args()

    today = datetime.now(UB_TZ).date().isoformat()
    con = connect()
    session = requests.Session(impersonate="chrome")
    stats = dict.fromkeys(("new", "price_down", "price_up", "delisted", "relisted"), 0)

    print(f"crawl {today} -- {BASE}{CATEGORY_PATH}")
    ads = crawl_list_pages(session, args.max_pages)
    if not ads:
        print("no ads found -- site blocked or markup changed; aborting", file=sys.stderr)
        sys.exit(1)

    known = {r[0] for r in con.execute("SELECT ad_id FROM listings")}
    new_ids = [i for i in ads if i not in known]
    print(f"{len(ads)} active ads on site, {len(new_ids)} new to the DB")

    # snapshots + price events for every active ad
    for ad_id, card in ads.items():
        prev = con.execute(
            "SELECT price_mnt FROM snapshots WHERE ad_id=? AND crawl_date<?"
            " ORDER BY crawl_date DESC LIMIT 1", (ad_id, today)).fetchone()
        con.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)",
                    (ad_id, today, card["price_mnt"], card["old_price_mnt"],
                     card["views"]))
        if prev and prev[0] and card["price_mnt"] and prev[0] != card["price_mnt"]:
            etype = "price_down" if card["price_mnt"] < prev[0] else "price_up"
            add_event(con, ad_id, today, etype, prev[0], card["price_mnt"])
            stats[etype] += 1

    # existing ads: bump last_seen, revive relisted ones
    seen_ids = list(ads)
    for i in range(0, len(seen_ids), 500):
        chunk = seen_ids[i:i + 500]
        qs = ",".join("?" * len(chunk))
        for (ad_id,) in con.execute(
                f"SELECT ad_id FROM listings WHERE delisted_at IS NOT NULL"
                f" AND ad_id IN ({qs})", chunk):
            add_event(con, ad_id, today, "relisted")
            stats["relisted"] += 1
        con.execute(f"UPDATE listings SET last_seen=?, delisted_at=NULL"
                    f" WHERE ad_id IN ({qs})", [today] + chunk)
    con.commit()

    # new ads: fetch detail pages
    for n, ad_id in enumerate(new_ids, 1):
        time.sleep(REQUEST_DELAY)
        url = ads[ad_id]["href"]
        if url.startswith("/"):
            url = BASE + url
        html = fetch(session, url)
        if html is None:
            continue
        d = parse_detail(html, url)
        d["ad_id"] = ad_id
        d.setdefault("title", None)
        if not d.get("title"):
            d["title"] = ads[ad_id]["title"]
        d["first_seen"] = d["last_seen"] = today
        d["enriched"] = 0
        d.update(rx_extract(d.get("description", "")))
        detail_views = d.pop("views", None)
        insert_listing(con, d)
        add_event(con, ad_id, today, "new", None, ads[ad_id]["price_mnt"])
        stats["new"] += 1
        if detail_views is not None and ads[ad_id]["views"] is None:
            con.execute("UPDATE snapshots SET views=? WHERE ad_id=? AND crawl_date=?",
                        (detail_views, ad_id, today))
        if n % 25 == 0 or n == len(new_ids):
            print(f"  detail {n}/{len(new_ids)}")
            con.commit()
    con.commit()

    # delistings -- only on a FULL crawl, and only if the crawl looks complete
    if args.max_pages is None:
        active = [r[0] for r in con.execute(
            "SELECT ad_id FROM listings WHERE delisted_at IS NULL")]
        gone = [i for i in active if i not in ads]
        if active and len(gone) > 0.5 * len(active):
            print(f"SKIPPING delist detection: {len(gone)}/{len(active)} active ads "
                  "missing -- crawl looks incomplete", file=sys.stderr)
        else:
            for ad_id in gone:
                last = con.execute(
                    "SELECT price_mnt FROM snapshots WHERE ad_id=?"
                    " ORDER BY crawl_date DESC LIMIT 1", (ad_id,)).fetchone()
                con.execute("UPDATE listings SET delisted_at=? WHERE ad_id=?",
                            (today, ad_id))
                add_event(con, ad_id, today, "delisted", last[0] if last else None)
                stats["delisted"] += 1
    con.commit()
    con.close()

    print("done:", ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
