#!/usr/bin/env python3
"""
item_payload.py — turn a row in the sales database into the JSON the publishers eat.

Why this exists: dba_publish.py and fb_publish.py both take a payload file, and until
now that file was written by hand for each item. That made publishing something only
the machine that built it could do. This closes the gap: one row in, one payload out,
no editor in between.

Marketplace-specific naming (DBA's numeric category path, Facebook's category label
and condition wording) is not something Notion stores — it is a property of the
marketplace, so it lives in config/marketplace-categories.json and is selected with
--category.

Usage:
    python3 bin/item_payload.py --db-number 0001 --category mobile_phone \
        --out /tmp/item.json --photos-dir /tmp/item-photos
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ssot_status import api, load_env, title, rich  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATEGORIES = ROOT / "config" / "marketplace-categories.json"


def plain(prop: dict):
    t = prop["type"]
    if t == "title":
        return "".join(x["plain_text"] for x in prop["title"]).strip()
    if t == "rich_text":
        return "".join(x["plain_text"] for x in prop["rich_text"]).strip()
    if t == "select":
        return ((prop["select"] or {}).get("name") or "").strip()
    if t == "url":
        return (prop["url"] or "").strip()
    if t == "number":
        return prop["number"]
    if t == "files":
        return prop["files"]
    return None


def files_of(prop: dict) -> list:
    value = plain(prop) if prop else None
    return value if isinstance(value, list) else []


def find_row(key: str, ds: str, db_number: str | None, page_id: str | None) -> dict:
    if page_id:
        return api(f"/pages/{page_id}", key)
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = api(f"/data_sources/{ds}/query", key, body)
        rows.extend(res["results"])
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    for r in rows:
        props = r["properties"]
        if "DB_number" in props and plain(props["DB_number"]) == db_number:
            return r
    raise SystemExit(f"no row with DB_number {db_number}")


def download(files: list, dest: pathlib.Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for i, f in enumerate(files, 1):
        url = f.get("file", {}).get("url") or f.get("external", {}).get("url")
        if not url:
            continue
        name = (f.get("name") or f"photo{i}").replace("/", "_")
        target = dest / f"{i:02d}_{name}"
        if not target.suffix:
            target = target.with_suffix(".jpg")
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-marketplace/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(target, "wb") as fh:
            fh.write(r.read())
        out.append(str(target))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a publisher payload from a database row.")
    ap.add_argument("--db-number", help="DB_number, e.g. 0001")
    ap.add_argument("--page", help="Notion page id (alternative to --db-number)")
    ap.add_argument("--category", required=True, help="key in config/marketplace-categories.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--photos-dir", required=True)
    args = ap.parse_args()

    env = load_env()
    key, ds = env["NOTION_API_KEY"], env["NOTION_DATABASE_DEFAULT"]
    rows_conf = json.loads(CATEGORIES.read_text())
    if args.category not in rows_conf:
        raise SystemExit(f"unknown category {args.category!r}; known: {', '.join(rows_conf)}")
    conf = rows_conf[args.category]

    row = find_row(key, ds, args.db_number, args.page)
    p = row["properties"]

    # every field the listing asserts must come from the row - never invented
    missing = [f for f in ("Name", "Description", "Price (DKK)") if not plain(p.get(f, {"type": "rich_text", "rich_text": []}))]
    if missing:
        raise SystemExit("row is missing required fields: " + ", ".join(missing))

    photos = download(files_of(p.get("Photo", {})), pathlib.Path(args.photos_dir))
    if not photos:
        raise SystemExit("row has no photos - refusing to publish an untitled photo-less listing")

    payload = {
        "db_number": plain(p.get("DB_number", {"type": "rich_text", "rich_text": []})),
        "title": plain(p["Name"]),
        "description": plain(p["Description"]),
        "price": plain(p["Price (DKK)"]),
        "listing_title": plain(p.get("Listing title", {"type": "rich_text", "rich_text": []})),
        "photos": photos,
        "postal": conf["postal"],
        "category": conf["dba"]["category"],
        "condition": conf["dba"]["condition"],
        "attributes": conf["dba"].get("attributes", {}),
        "dba_condition_label": conf["dba"].get("condition_label"),
        "fb": {
            "category": conf["fb"]["category"],
            "condition": conf["fb"]["condition"],
        },
    }
    pathlib.Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    print(f"wrote {args.out}: {len(payload['description'])} chars, {len(photos)} photos, price {payload['price']}")


if __name__ == "__main__":
    main()
