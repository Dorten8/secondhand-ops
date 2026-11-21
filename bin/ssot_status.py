#!/usr/bin/env python3
"""
ssot_status.py — read the general sales database and report what needs attention.

Ownership rule this exists to serve: whoever publishes an item may hand it over,
but once a row is in the database, *this* agent owns it until it is SOLD. That
means somebody has to actually look at the rows, not just write them. This is
that look.

Read-only. Never writes, never sends to a buyer. Prints one line per item and a
short summary; empty database prints nothing but the header, so it is safe to run
on a schedule.

Usage:
    python3 bin/ssot_status.py                 # human summary
    python3 bin/ssot_status.py --json          # machine readable
    python3 bin/ssot_status.py --stale-days 14
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

NOTION_VERSION = "2025-09-03"


def load_env() -> dict[str, str]:
    for candidate in (
        os.environ.get("ENV_FILE", ""),
        str(Path.home() / "hermes" / "home" / ".env"),
        str(Path.home() / ".hermes" / ".env"),
        ".env",
    ):
        if not candidate:
            continue
        p = Path(candidate)
        if not p.is_file():
            continue
        out: dict[str, str] = {}
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
        return out
    return {}


def api(path: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "https://api.notion.com/v1" + path,
        data=data,
        headers={
            "Authorization": "Bearer " + token,
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def rich(v: dict) -> str:
    return "".join(t.get("plain_text", "") for t in v.get("rich_text", []))

def title(v: dict) -> str:
    return "".join(t.get("plain_text", "") for t in v.get("title", []))

def select(v: dict) -> str:
    return (v.get("select") or {}).get("name", "")

def number(v: dict) -> str:
    n = v.get("number")
    return f"{n:g}" if isinstance(n, (int, float)) else ""


def pick(p: dict, *names: str) -> dict:
    """Return the first present property, so a column rename cannot break the sweep."""
    for n in names:
        if n in p:
            return p[n]
    return {}

def summarise(row: dict, stale_days: int) -> dict:
    p = row["properties"]
    name = title(p.get("Name", {})) or "(untitled)"
    stock = select(p.get("STOCK", {}))
    price = number(p.get("Price (DKK)", {}))
    db_id = rich(p.get("DB_number", {}))
    dba = (p.get("DBA URL", {}) or {}).get("url") or ""
    fb = (p.get("Facebook URL", {}) or {}).get("url") or ""
    photos = len((p.get("Photo", {}) or {}).get("files", []) or [])
    # Schema is operator-owned and was reorganised on 2026-11-21:
    # "Listing" split into "Listing title" + "Description"; "Notes" -> "Notes_seller".
    listing_title = rich(pick(p, "Listing title", "Listing"))
    description = rich(pick(p, "Description"))
    notes = rich(pick(p, "Notes_seller", "Notes"))

    edited = row.get("last_edited_time") or ""
    age = None
    if edited:
        try:
            then = dt.datetime.fromisoformat(edited.replace("Z", "+00:00"))
            age = (dt.datetime.now(dt.timezone.utc) - then).days
        except ValueError:
            age = None

    flags = []
    if stock == "LISTED" and not (dba or fb):
        flags.append("LISTED with no ad URL")
    if stock == "UNLISTED" and not listing_title:
        flags.append("no listing title")
    if stock == "UNLISTED" and not description:
        flags.append("no description to publish")
    if stock == "UNLISTED" and photos == 0:
        flags.append("no photos")
    if stock == "LISTED" and age is not None and age >= stale_days:
        flags.append(f"listed {age}d with no update")
    if not price:
        flags.append("no price")

    return {
        "name": name,
        "stock": stock or "?",
        "price_dkk": price,
        "marketplace_id": db_id,
        "url": dba or fb,
        "photos": photos,
        "age_days": age,
        "flags": flags,
        "page_id": row["id"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report the state of the sales database.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true",
                    help="watchdog mode: print ONLY items with flags; silent otherwise")
    ap.add_argument("--stale-days", type=int, default=14)
    ap.add_argument("--database", help="data_source id; default from NOTION_DATABASE_DEFAULT")
    args = ap.parse_args(argv)

    env = load_env()
    token = env.get("NOTION_API_KEY") or env.get("NOTION_TOKEN")
    ds = args.database or env.get("NOTION_DATABASE_DEFAULT")
    if not token:
        print("NOTION_API_KEY missing", file=sys.stderr)
        return 2
    if not ds:
        print("NOTION_DATABASE_DEFAULT missing — set it or pass --database", file=sys.stderr)
        return 2

    rows: list[dict] = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        page = api(f"/data_sources/{ds}/query", token, payload)
        rows.extend(page.get("results", []))
        if not page.get("has_more"):
            break
        cursor = page.get("next_cursor")

    items = [summarise(r, args.stale_days) for r in rows]

    if args.json:
        print(json.dumps(items, indent=2))
        return 0

    if args.quiet:
        # Watchdog mode: silence is the success case.
        flagged = [i for i in items if i["flags"]]
        if not flagged:
            return 0
        for it in flagged:
            print(f"{it['stock']:<9} {it['name'][:44]:<44} "
                  f"{it['price_dkk'] or '-':>6} DKK   ⚠ " + "; ".join(it["flags"]))
        return 0

    if not items:
        print("database is empty — nothing to manage")
        return 0

    counts: dict[str, int] = {}
    for it in items:
        counts[it["stock"]] = counts.get(it["stock"], 0) + 1

    print(" | ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print()
    for it in items:
        line = f"{it['stock']:<9} {it['name'][:44]:<44} {it['price_dkk'] or '-':>6} DKK"
        if it["flags"]:
            line += "   ⚠ " + "; ".join(it["flags"])
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
