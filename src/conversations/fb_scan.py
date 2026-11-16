#!/usr/bin/env python3
"""fb_scan.py — learn which Messenger rows are Marketplace threads, and remember.

Why a separate scanner: Facebook renders 1:1 and Marketplace rows WITHOUT an href.
Only group chats expose `a[href*="/messages/t/"]`. So the only way to learn a thread's
id is to open the row and read the URL. Opening is also unreliable — the list
re-renders under the click and the navigation is sometimes dropped — so this retries
per row and records what it learns in `fb_threads.json`.

Run it on a slow schedule (every 30 minutes is plenty): a scan is ~6-10 seconds per
row, and the cache means the poll itself never has to touch the UI.

    python3 fb_scan.py --max-rows 8 --tries 2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/hermes/hermes/home/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent / "providers"))

from fb import CACHE, MESSAGES_URL, THREAD_RE, FacebookProvider, _load_cache, _save_cache  # noqa: E402


def scan(max_rows: int = 8, tries: int = 2, only_unread: bool = False) -> dict:
    p = FacebookProvider()
    cache = _load_cache()
    learned, attempted = [], 0

    s = p._open(MESSAGES_URL, wait=9)
    rows = p._rows(s)
    if only_unread:
        rows = [r for r in rows if re.search(r"new message|ulæst|unread", r["text"], re.I)]

    for row in rows[:max_rows]:
        attempted += 1
        text = row["text"]

        # Already known? Match by the buyer name at the start of the row text.
        known = next((cid for cid, m in cache.items()
                      if m.get("buyer") and text.lower().startswith(m["buyer"].lower())), None)
        if known:
            continue

        res = p._resolve_row(s, row, tries=tries)
        if not res:
            continue
        cache[res["conv_id"]] = {
            "buyer": res["buyer"],
            "item": res["item"],
            "row": text[:120],
            "learned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _save_cache(cache)
        learned.append(res)
        # the page is now on a thread; go back to the list for the next row
        s.eval(f'location.href="{MESSAGES_URL}"')
        time.sleep(5)
        s.reset()

    return {"attempted": attempted, "learned": learned, "known": len(cache)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rows", type=int, default=8)
    ap.add_argument("--tries", type=int, default=2)
    ap.add_argument("--only-unread", action="store_true")
    a = ap.parse_args()
    print(json.dumps(scan(a.max_rows, a.tries, a.only_unread), indent=1, ensure_ascii=False))
