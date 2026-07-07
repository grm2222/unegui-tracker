#!/usr/bin/env python3
"""Export/import for LLM enrichment (done BY CLAUDE in Cowork/Claude Code
sessions -- no API key, on demand, not daily).

Export pending descriptions (listings with enriched = 0):

    python enrich_io.py export > pending.json
    python enrich_io.py export --limit 50 > pending.json   # one batch

Output: JSON array of
    {"ad_id": 123, "title": "...", "description": "...",
     "attrs": {"area_m2": ..., "floor": ..., "building_floors": ...,
               "commissioned_year": ..., "sub_location": ...}}
`attrs` are the structured page attributes -- use them to flag
contradictions with the description in llm_notes.

Import filled results:

    python enrich_io.py import pending_filled.json

pending_filled.json: JSON array, one object per ad. Every llm_* field is a
string; leave "" when the description does not state it (never guess).
Missing keys are treated as "". Imported ads get enriched = 1.

    [{"ad_id": 123,
      "llm_orientation": "баруун,урд",
      "llm_bathrooms": "2",
      "llm_ceiling_m": "3",
      "llm_certificate": "гэрчилгээ бэлэн",
      "llm_furniture": "бүрэн тавилгатай",
      "llm_renovation": "шинэ засвартай",
      "llm_landmarks": "23-р сургууль; Монгени цэцэрлэг",
      "llm_notes": "яаралтай, доод үнэ; он зөрүүтэй: тайлбарт 2019, хүснэгтэд 2021"},
     ...]
"""

import json
import sys

from scraper import connect

LLM_FIELDS = ["llm_orientation", "llm_bathrooms", "llm_ceiling_m",
              "llm_certificate", "llm_furniture", "llm_renovation",
              "llm_landmarks", "llm_notes"]


def export(limit=None):
    con = connect()
    q = ("SELECT ad_id, title, description, area_m2, floor, building_floors,"
         " commissioned_year, sub_location FROM listings"
         " WHERE enriched = 0 AND description != '' ORDER BY first_seen, ad_id")
    if limit:
        q += f" LIMIT {int(limit)}"
    items = [{
        "ad_id": r[0], "title": r[1], "description": r[2],
        "attrs": {"area_m2": r[3], "floor": r[4], "building_floors": r[5],
                  "commissioned_year": r[6], "sub_location": r[7]},
    } for r in con.execute(q)]
    con.close()
    json.dump(items, sys.stdout, ensure_ascii=False, indent=1)
    print(f"\n-- {len(items)} pending", file=sys.stderr)


def import_(path):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    con = connect()
    n = 0
    for it in items:
        ad_id = it.get("ad_id")
        if ad_id is None:
            print(f"skipping item without ad_id: {it}", file=sys.stderr)
            continue
        sets = ", ".join(f"{f}=?" for f in LLM_FIELDS)
        vals = [str(it.get(f, "") or "") for f in LLM_FIELDS]
        cur = con.execute(
            f"UPDATE listings SET {sets}, enriched=1 WHERE ad_id=?",
            vals + [ad_id])
        if cur.rowcount:
            n += 1
        else:
            print(f"ad_id {ad_id} not in DB", file=sys.stderr)
    con.commit()
    left = con.execute("SELECT COUNT(*) FROM listings WHERE enriched=0").fetchone()[0]
    con.close()
    print(f"imported {n} listings, {left} still pending")


def main():
    args = sys.argv[1:]
    if args[:1] == ["export"]:
        limit = None
        if "--limit" in args:
            limit = args[args.index("--limit") + 1]
        export(limit)
    elif args[:1] == ["import"] and len(args) == 2:
        import_(args[1])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
