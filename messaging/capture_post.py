#!/usr/bin/env python3
"""capture_post.py — watch DBA's network until a message actually gets sent.

WHY: DBA's read API is known and used, but the *send* endpoint has never been
observed. One real send with the network watched gives us the method, URL and
body — after which sending stops needing a browser and the whole lane becomes
HTTP-only (which is what makes it portable to Antigravity and to friends).

USAGE
    python3 capture_post.py --selftest      # prove the watcher sees traffic
    python3 capture_post.py --minutes 45    # watch for a POST, dump it

Jacob sends one message himself (typing in the DBA web UI is enough — he does not
have to copy-paste, any send works). We only OBSERVE; we never send anything.

OUTPUT
    $HERMES_HOME/messaging/captured_posts.jsonl   one line per captured POST

SAFETY
    Read-only observation. Cookies and auth headers are recorded for replay BUT
    the file is chmod 600 and header values are stored, so treat it as a secret.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

CDP = "http://127.0.0.1:9222"
OUT = Path("/home/hermes/hermes/home/messaging/captured_posts.jsonl")

# A send is a POST whose URL mentions conversations; keep it narrow so we do not
# drown in analytics beacons.
INTERESTING = ("/messages/api/", "conversation", "/messages/")


def find_page():
    lst = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=8))
    pages = [t for t in lst if t.get("type") == "page"
             and "dba.dk" in (t.get("url") or "")]
    if not pages:
        raise SystemExit("no dba.dk tab open — open https://www.dba.dk/messages/ first")
    # prefer the messages page
    for t in pages:
        if "/messages" in (t.get("url") or ""):
            return t
    return pages[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=45)
    ap.add_argument("--selftest", action="store_true",
                    help="reload the page and just prove we can see requests, then exit")
    a = ap.parse_args()

    ver = json.load(urllib.request.urlopen(f"{CDP}/json/version", timeout=8))
    page = find_page()
    print(f"attached to: {page.get('url')[:80]}", flush=True)

    ws = connect(ver["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024, open_timeout=15)
    nid = [0]

    def call(method, params=None, sid=None, wait=True):
        nid[0] += 1
        msg = {"id": nid[0], "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        ws.send(json.dumps(msg))
        if not wait:
            return None
        while True:
            m = json.loads(ws.recv(timeout=25))
            if m.get("id") == nid[0]:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    sid = call("Target.attachToTarget", {"targetId": page["id"], "flatten": True})["sessionId"]
    call("Network.enable", {}, sid)
    print("network watcher armed", flush=True)

    if a.selftest:
        call("Page.reload", {"ignoreCache": False}, sid)
        seen = []
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                m = json.loads(ws.recv(timeout=2))
            except TimeoutError:
                continue
            if m.get("method") == "Network.requestWillBeSent":
                seen.append(m["params"]["request"]["url"])
        ws.close()
        posts = [u for u in seen if u]
        print(f"SELFTEST: saw {len(posts)} requests, e.g. {posts[:3]}")
        print("SELFTEST PASSTHROUGH" if posts else "SELFTEST FAILED — watcher saw nothing")
        return 0 if posts else 1

    captured = 0
    deadline = time.time() + a.minutes * 60
    pending = {}
    print(f"watching for a DBA send for {a.minutes:.0f} min — send a message now", flush=True)

    while time.time() < deadline:
        try:
            m = json.loads(ws.recv(timeout=3))
        except TimeoutError:
            continue
        except Exception:
            break
        meth = m.get("method")
        p = m.get("params") or {}

        if meth == "Network.requestWillBeSent":
            req = p.get("request") or {}
            url = req.get("url") or ""
            if req.get("method") == "POST" and any(k in url for k in INTERESTING):
                pending[p.get("requestId")] = {
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "method": "POST",
                    "url": url,
                    "headers": req.get("headers"),
                    "post_data_inline": req.get("postData"),
                    "request_id": p.get("requestId"),
                }

        elif meth == "Network.loadingFinished":
            rid = p.get("requestId")
            if rid in pending:
                rec = pending.pop(rid)
                if not rec.get("post_data_inline"):
                    try:
                        d = call("Network.getRequestPostData", {"requestId": rid}, sid)
                        rec["post_data_inline"] = d.get("postData")
                    except Exception as exc:
                        rec["post_data_error"] = str(exc)[:120]
                OUT.parent.mkdir(parents=True, exist_ok=True)
                with OUT.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                try:
                    OUT.chmod(0o600)
                except Exception:
                    pass
                captured += 1
                print(f"\n*** CAPTURED POST #{captured} ***", flush=True)
                print(f"  {rec['url'][:120]}", flush=True)
                print(f"  body: {str(rec.get('post_data_inline'))[:400]}", flush=True)
                print(f"  -> {OUT}", flush=True)

    ws.close()
    print(f"done. captured {captured} send(s).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
