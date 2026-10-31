#!/usr/bin/env python3
"""peek_thread.py — print one DBA conversation's transcript (read-only).

The JSON API gives thread *metadata* but not the messages, and the messages
endpoint has not been captured yet. So this opens the thread in the browser and
reads the rendered text — read-only, no typing, no sending.

    python3 peek_thread.py --conv <conversationId>
    python3 peek_thread.py --buyer "Julia og Feno"      # by counterpart name
    python3 peek_thread.py --list                        # rows currently visible
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/home/hermes/hermes/home/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "providers"))

from cdp import Session          # noqa: E402
from dba import DBA              # noqa: E402


def rows_js():
    return """(() => {
        const out = [];
        for (const a of document.querySelectorAll('a[href^="/messages/id/"]')) {
            out.push({href: a.getAttribute('href'),
                      text: (a.innerText || '').replace(/\\s+/g,' ').slice(0,160)});
        }
        return JSON.stringify(out);
    })()"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv")
    ap.add_argument("--buyer")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    with Session("dba.dk") as s:
        import time
        print(f"on: {s.eval('location.href')}", file=sys.stderr)
        if not a.list:
            s.eval("location.href='https://www.dba.dk/messages/'")
            for _ in range(20):
                time.sleep(1.5)
                try:
                    s.reset()
                    if s.eval("!!document.querySelector('a[href^=\"/messages/id/\"]')") is True:
                        break
                except Exception:
                    continue
            print("inbox list loaded", file=sys.stderr)

        if a.list or (not a.conv and not a.buyer):
            import json
            rows = json.loads(s.json(rows_js()))
            print(f"{len(rows)} conversation rows visible:")
            for r in rows:
                print(f"  {r['text'][:100]}")
            return 0

        target = None
        if a.conv:
            for c in DBA().list_conversations():
                if c.conv_id == a.conv:
                    target = c
                    break
        elif a.buyer:
            for c in DBA().list_conversations():
                if a.buyer.lower() in (c.partner_name or "").lower():
                    target = c
                    break
        if not target:
            print("thread not found in the inbox list", file=sys.stderr)
            return 2

        print(f"buyer={target.partner_name} item={target.item_title} "
              f"| last_outgoing={target.last_outgoing}", file=sys.stderr)

        hit = s.eval("""(() => {
            const rows = [...document.querySelectorAll('a[href^="/messages/id/"]')];
            const n = %s;
            const r = rows.find(x => (x.innerText||'').includes(n)) || rows[0];
            if (!r) return false; r.click(); return true;
        })()""" % _js_str(target.partner_name))
        if not hit:
            print("could not click the row", file=sys.stderr)
            return 2

        import time
        # Hydration: the route changes immediately but the transcript renders after
        # the app's own API call. Wait for the composer to exist, not a fixed sleep.
        ready = False
        for _ in range(20):
            time.sleep(1.5)
            try:
                s.reset()
                if s.eval("!!document.querySelector('textarea[placeholder=\"Skriv en besked...\"]')") is True:
                    ready = True
                    break
            except Exception:
                continue
        print(f"composer present: {ready}", file=sys.stderr)
        time.sleep(1)

        text = s.text() or ""
        print("=" * 60)
        print(text)
        print("=" * 60)
        print(f"[{len(text)} chars]", file=sys.stderr)
    return 0


def _js_str(v: str) -> str:
    import json
    return json.dumps(v)


if __name__ == "__main__":
    sys.exit(main())
