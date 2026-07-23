#!/usr/bin/env python3
"""Resolve a RESIDENCE name for each listing.

Why: `sub_location` from the site is unusable as a residence key -- 47% of
listings sit under a bare district name ("Баянзүрх" spans ~100 km), and
seller-placed coordinates are only roughly accurate. Residence names, on the
other hand, are written into the ad titles ("Нарны хороолол", "King Tower",
"Arga bilig хотхон"), so we parse them out and use the name as the grouping
key, with coordinates only as a sanity check.

Resolution order per listing:
  1. name parsed out of the title (marker words: хотхон / хороолол /
     residence / tower / apartment / town / city ...)
  2. else `sub_location`, when it is not just a district name
  3. else the district -- grouped, but flagged low confidence

Run directly for a quality report:
    python residences.py
"""

import re
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "unegui.db"

# district names and the abbreviations sellers prefix titles with
DISTRICTS = ["Баянзүрх", "Хан-Уул", "Сонгинохайрхан", "Сүхбаатар", "Баянгол",
             "Чингэлтэй", "Багануур", "Багахангай", "Налайх"]
DISTRICT_LOW = {d.lower() for d in DISTRICTS}
ABBR = {"бзд", "худ", "схд", "сбд", "бгд", "чд", "бнд", "нд", "бхд",
        "баянзүрх", "хан-уул", "сонгинохайрхан", "сүхбаатар", "баянгол",
        "чингэлтэй", "дүүрэг", "дүүргийн", "хот", "хотын", "улаанбаатар"}

# words that mark the END of a residence name
MARKERS = ["хотхон", "хороолол", "хороолл", "резиденс", "residence", "tower",
           "тауэр", "plaza", "плаза", "апартмент", "apartment", "аппартмент",
           "town", "таун", "city", "сити", "village", "palace", "палас",
           "хаус", "house", "цамхаг"]
MARKER_RE = re.compile(
    r"([0-9A-Za-zА-Яа-яЁёӨөҮүІ\-Ѐ-ӿ]+"
    r"(?:\s+[0-9A-Za-zА-Яа-яЁёӨөҮүІ\-Ѐ-ӿ]+){0,2})\s+(" +
    "|".join(MARKERS) + r")\b", re.IGNORECASE)

# tokens that are never part of a residence name
NOISE = ABBR | {
    "хороо", "хороонд", "байр", "байранд", "орон", "сууц", "өрөө", "өрөөний",
    "зарна", "зарах", "шинэ", "хуучин", "давхар", "давхарт", "тоот", "мкв",
    "м2", "мк", "m2", "их", "бага", "төв", "төвд", "баруун", "зүүн", "урд",
    "хойд", "хажууд", "ойролцоо", "дэргэд", "ард", "хамт", "дотор", "н",
    "нь", "ба", "болон", "тэй", "той", "зэрэг", "гэр", "айл",
}
NUM_KHOROO = re.compile(r"^\d+\s*-?\s*р?$")


def _clean_tokens(text):
    toks = [t for t in re.split(r"[\s,.:;()]+", text.strip().lower()) if t]
    # Drop leading noise / district / "26-р хороо" prefixes, but NEVER the
    # last token: "10-р хороолол" is itself the residence name, so the
    # number must survive when it is all that precedes the marker.
    while len(toks) > 1 and (toks[0] in NOISE or NUM_KHOROO.match(toks[0])):
        toks.pop(0)
    if toks and toks[0] in NOISE:
        toks.pop(0)
    return toks


def _parse(text):
    """Residence name parsed out of free text, or None."""
    if not text:
        return None
    m = MARKER_RE.search(text)
    if not m:
        return None
    toks = _clean_tokens(m.group(1))
    marker = m.group(2).lower()
    if not toks:
        return None
    name = " ".join(toks[-2:]) if len(toks) >= 2 else toks[0]
    if len(name) < 2 or name in NOISE:
        return None
    return f"{name} {marker}".strip()


def from_title(title):
    return _parse(title)


def _name(title, sub_location, description):
    """-> (name, confidence) before district scoping."""
    name = _parse(title)
    if name:
        return name, 3
    if sub_location and sub_location.lower() not in DISTRICT_LOW:
        return sub_location.strip().lower(), 2
    # only worth reading the description once the better sources are exhausted
    name = _parse(description)
    if name:
        return name, 1
    if sub_location:
        return sub_location.strip().lower(), 0
    return None, 0


def resolve(title, sub_location, district, description=None):
    """-> (residence_key, confidence), confidence 3/2/1/0.

    The key is scoped by district: the same residence name recurs in
    different cities ("Алтай хотхон" exists in both УБ and Дархан, 334 km
    apart), and district comes from the site's own address field, so it is
    far more trustworthy than the seller-placed pin.
    """
    name, conf = _name(title, sub_location, description)
    dist = (district or "?").strip().lower()
    if not name:
        return dist, 0
    return (name if name == dist else f"{name}|{dist}"), conf


def split_key(key):
    """residence key -> (name, district)."""
    return tuple(key.split("|", 1)) if "|" in key else (key, key)


def display(key):
    """Human-readable form of a residence key: 'Name · District'."""
    name, dist = split_key(key)
    cap = lambda s: " ".join(w.capitalize() for w in s.split())
    return cap(name) if name == dist else f"{cap(name)} · {cap(dist)}"


def resolve_all(rows):
    """rows: (ad_id, title, sub_location, district[, description])
    -> {ad_id: (key, conf)}"""
    return {r[0]: resolve(r[1], r[2], r[3], r[4] if len(r) > 4 else None)
            for r in rows}


def _report():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT ad_id, title, sub_location, district, latitude, longitude,"
        " delisted_at, description FROM listings").fetchall()
    res = resolve_all([(r[0], r[1], r[2], r[3], r[7]) for r in rows])

    by_conf = defaultdict(int)
    groups = defaultdict(list)
    for ad_id, title, sub, dist, lat, lon, gone, _desc in rows:
        key, conf = res[ad_id]
        by_conf[conf] += 1
        groups[key].append((lat, lon))

    total = len(rows)
    print(f"{total} listings -> {len(groups)} residences")
    print(f"  from title        (3): {by_conf[3]:5d}  {by_conf[3]/total:5.1%}")
    print(f"  from sub_location (2): {by_conf[2]:5d}  {by_conf[2]/total:5.1%}")
    print(f"  from description  (1): {by_conf[1]:5d}  {by_conf[1]/total:5.1%}")
    print(f"  district only     (0): {by_conf[0]:5d}  {by_conf[0]/total:5.1%}")

    # QUALITY CHECK: a good residence key clusters tightly in space
    spreads = []
    for key, pts in groups.items():
        pts = [(a, b) for a, b in pts if a and b]
        if len(pts) >= 3:
            lat_km = (max(p[0] for p in pts) - min(p[0] for p in pts)) * 111
            spreads.append((lat_km, key, len(pts)))
    spreads.sort()
    if spreads:
        vals = [s[0] for s in spreads]
        print(f"\ncoordinate spread over {len(spreads)} residences with >=3 ads:")
        print(f"  median {statistics.median(vals):.2f} km   "
              f"90th pct {sorted(vals)[int(len(vals)*.9)]:.2f} km")
        print("\n  loosest (likely still district-ish or a common name):")
        for km, key, n in spreads[-8:][::-1]:
            print(f"    {km:7.1f} km  n={n:4d}  {key}")

    print("\ntop 25 residences by listing count:")
    counts = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:25]
    for key, pts in counts:
        print(f"  {len(pts):5d}  {display(key)}")
    con.close()


if __name__ == "__main__":
    _report()
