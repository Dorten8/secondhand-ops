#!/usr/bin/env python3
"""
publish_item.py — one command: database row -> live listing(s) -> row updated.

This is the entry point any harness (Hermes, Antigravity, a person on a laptop) runs.
It is deliberately thin: it builds the payload, calls the marketplace CLIs one at a
time, verifies the listing URL really came back from the marketplace, then writes the
URLs and STOCK=LISTED into the row and reads them back.

Marketplaces are independent: a Facebook failure must not roll back a DBA success, so
each is reported separately and the exit code says how many succeeded.

Usage:
    python3 bin/publish_item.py --db-number 0002 --category mobile_phone \
        --marketplaces dba,fb --endpoint http://127.0.0.1:9222 --confirm

    # no browser, no publish: just show the payload that would be used
    python3 bin/publish_item.py --db-number 0001 --category mobile_phone --payload-only
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ssot_status import api, load_env  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"

URL_PATTERNS = {
    "dba": re.compile(r"https://www\.dba\.dk/recommerce/forsale/item/\d+"),
    "fb": re.compile(r"https://www\.facebook\.com/marketplace/item/\d+"),
}
CLIS = {"dba": BIN / "dba_publish.py", "fb": BIN / "fb_publish.py"}
URL_FIELD = {"dba": "DBA URL", "fb": "Facebook URL"}


def build_payload(db_number: str, category: str, workdir: pathlib.Path) -> tuple[dict, pathlib.Path]:
    out = workdir / "item.json"
    photos = workdir / "photos"
    cmd = [sys.executable, str(BIN / "item_payload.py"), "--db-number", db_number,
           "--category", category, "--out", str(out), "--photos-dir", str(photos)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit("payload build failed:\n" + (res.stderr or res.stdout))
    print(res.stdout.strip())
    return json.loads(out.read_text()), out


def run_marketplace(market: str, payload_path: pathlib.Path, endpoint: str, confirm: bool) -> dict:
    cmd = [sys.executable, str(CLIS[market]), "--json", str(payload_path), "--endpoint", endpoint]
    cmd += ["--confirm"] if confirm else ["--dry-run"]
    print(f"\n=== {market} ===")
    res = subprocess.run(cmd, capture_output=True, text=True)
    out = (res.stdout or "") + (res.stderr or "")
    print(out.strip()[-1500:])
    match = URL_PATTERNS[market].search(out)
    return {
        "marketplace": market,
        "ok": res.returncode == 0 and bool(match),
        "url": match.group(0) if match else None,
        "returncode": res.returncode,
    }


def record(page_id: str, results: list[dict]) -> dict:
    env = load_env()
    key = env["NOTION_API_KEY"]
    props: dict = {}
    for r in results:
        if r["ok"] and r["url"]:
            props[URL_FIELD[r["marketplace"]]] = {"url": r["url"]}
    if not props:
        return {}
    # a row only counts as LISTED once at least one marketplace really has it
    props["STOCK"] = {"select": {"name": "LISTED"}}
    api(f"/pages/{page_id}", key, {"properties": props}, method="PATCH")
    row = api(f"/pages/{page_id}", key)
    readback = {}
    for field in props:
        p = row["properties"][field]
        readback[field] = p["url"] if p["type"] == "url" else (p["select"] or {}).get("name")
    return readback


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish a database row to marketplaces end to end.")
    ap.add_argument("--db-number", required=True)
    ap.add_argument("--category", required=True)
    ap.add_argument("--marketplaces", default="dba,fb")
    ap.add_argument("--endpoint", default="http://127.0.0.1:9222")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--payload-only", action="store_true")
    ap.add_argument("--page", help="Notion page id, if known (skips the lookup)")
    args = ap.parse_args()

    workdir = pathlib.Path(tempfile.mkdtemp(prefix=f"item-{args.db_number}-"))
    payload, payload_path = build_payload(args.db_number, args.category, workdir)

    if args.payload_only:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:1500])
        return

    markets = [m.strip() for m in args.marketplaces.split(",") if m.strip()]
    unknown = [m for m in markets if m not in CLIS]
    if unknown:
        raise SystemExit(f"unknown marketplace(s): {unknown}; known: {list(CLIS)}")

    results = [run_marketplace(m, payload_path, args.endpoint, args.confirm) for m in markets]

    print("\n=== result ===")
    for r in results:
        print(f"  {r['marketplace']:4s} {'OK  ' if r['ok'] else 'FAIL'} {r['url'] or ''}")

    if args.confirm:
        ok_urls = [r for r in results if r["ok"] and r["url"]]
        if ok_urls:
            page = args.page
            if not page:
                row = api(
                    f"/data_sources/{load_env()['NOTION_DATABASE_DEFAULT']}/query",
                    load_env()["NOTION_API_KEY"],
                    {"page_size": 100},
                )
                for candidate in row["results"]:
                    p = candidate["properties"].get("DB_number", {})
                    text = "".join(x["plain_text"] for x in p.get("rich_text", []))
                    if text.strip() == args.db_number:
                        page = candidate["id"]
                        break
            if page:
                print("\nrow updated:", json.dumps(record(page, results), ensure_ascii=False))
            else:
                print(f"\nWARNING: could not find the row for DB_number {args.db_number}; URLs not recorded")

    failed = [r for r in results if not r["ok"]]
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
