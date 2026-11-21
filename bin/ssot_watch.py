#!/usr/bin/env python3
"""
ssot_watch.py — tell me when the sales database CHANGES, without polling the LLM.

The ownership rule only works if a new row reaches the owner in minutes, not at the
next daily sweep. Notion has no push into a home network without a public endpoint,
so this is the cheap half of the problem: a zero-token state diff.

Run it on a schedule. It compares the database against a local snapshot and prints
ONLY what changed:

    NEW         a row appeared                -> this is the handover, act on it
    STOCK       status moved                  -> UNLISTED means publishable
    FLAGS       a row became unpublishable     -> named field missing
    GONE        a row was deleted             -> stop managing it

Silence means nothing changed. Exit 0 either way, so it is safe as a watchdog.

    python3 bin/ssot_watch.py            # diff vs last run
    python3 bin/ssot_watch.py --baseline # record current state, print nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ssot_status import api, load_env, summarise  # reuse, do not duplicate the schema

STATE_CANDIDATES = (
    os.environ.get("SSOT_WATCH_STATE", ""),
    str(Path.home() / "hermes" / "home" / "state" / "ssot_watch.json"),
    str(Path.home() / ".cache" / "ssot_watch.json"),
)


def state_path() -> Path:
    for c in STATE_CANDIDATES:
        if c:
            p = Path(c)
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
            except OSError:
                continue
    return Path("/tmp/ssot_watch.json")


def fetch(token: str, ds: str) -> list[dict]:
    rows: list[dict] = []
    cursor = None
    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        page = api(f"/data_sources/{ds}/query", token, payload)
        rows.extend(page.get("results", []))
        if not page.get("has_more"):
            break
        cursor = page.get("next_cursor")
    return rows


def snapshot(rows: list[dict], stale_days: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        s = summarise(r, stale_days)
        out[s["page_id"]] = {
            "name": s["name"],
            "stock": s["stock"],
            "price": s["price_dkk"],
            "flags": s["flags"],
            "edited": r.get("last_edited_time", ""),
        }
    return out


def diff(old: dict[str, dict], new: dict[str, dict]) -> list[str]:
    lines: list[str] = []

    for pid, cur in new.items():
        prev = old.get(pid)
        if prev is None:
            lines.append(
                f"NEW    {cur['stock']:<9} {cur['name'][:46]}"
                + (f"  {cur['price']} DKK" if cur["price"] else "")
                + ("" if not cur["flags"] else "   ⚠ " + "; ".join(cur["flags"]))
            )
            continue
        if prev["stock"] != cur["stock"]:
            lines.append(f"STOCK  {cur['name'][:46]}  {prev['stock']} -> {cur['stock']}")
        if prev["price"] != cur["price"]:
            lines.append(f"PRICE  {cur['name'][:46]}  {prev['price']} -> {cur['price']} DKK")
        if prev["flags"] != cur["flags"]:
            if cur["flags"]:
                lines.append(f"FLAGS  {cur['name'][:46]}  ⚠ " + "; ".join(cur["flags"]))
            else:
                lines.append(f"CLEAR  {cur['name'][:46]}  no longer flagged")

    for pid, prev in old.items():
        if pid not in new:
            lines.append(f"GONE   {prev['name'][:46]}  (row deleted — stop managing it)")

    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report only what changed in the sales DB.")
    ap.add_argument("--baseline", action="store_true", help="record state, print nothing")
    ap.add_argument("--stale-days", type=int, default=14)
    ap.add_argument("--database", help="data_source id; default NOTION_DATABASE_DEFAULT")
    args = ap.parse_args(argv)

    env = load_env()
    token = env.get("NOTION_API_KEY") or env.get("NOTION_TOKEN")
    ds = args.database or env.get("NOTION_DATABASE_DEFAULT")
    if not token or not ds:
        print("ssot_watch: NOTION_API_KEY / NOTION_DATABASE_DEFAULT missing", file=sys.stderr)
        return 2

    new = snapshot(fetch(token, ds), args.stale_days)
    path = state_path()

    old: dict[str, dict] = {}
    if path.is_file() and not args.baseline:
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}

    if args.baseline or not path.is_file():
        path.write_text(json.dumps(new, indent=2), encoding="utf-8")
        if not args.baseline:
            # first real run: baseline silently rather than dumping every row
            return 0
        print(f"baseline recorded: {len(new)} row(s) -> {path}")
        return 0

    for line in diff(old, new):
        print(line)

    path.write_text(json.dumps(new, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
