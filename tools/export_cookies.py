#!/usr/bin/env python3
"""export_cookies.py — hand a signed-in session to another machine, without the browser.

Why this exists: the marketplace providers drive a real browser that a human logged
into. Testing that code anywhere else means either exposing CDP over the network
(a whole browser's reach, including every other tab and profile) or moving just the
cookies for one domain. This moves the cookies.

The output is Playwright's `storage_state` shape, so the receiving side can load it
directly:

    python3 tools/export_cookies.py facebook.com --out /tmp/fb_cookies.json
    # then, in another checkout on another machine:
    browser = p.chromium.launch()
    ctx = browser.new_context(storage_state="/tmp/fb_cookies.json")

Cookies are credentials. This writes 0600, refuses to write inside the repo, and the
file must never be committed, pasted into chat, or put in an issue.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/hermes/hermes/home/scripts")
from cdp import Session  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def export(domain: str, out: Path) -> dict:
    """Read every cookie for `domain` from the live browser and write storage_state."""
    with Session(domain) as s:
        s.eval('location.href="https://%s/"' % domain)
        time.sleep(4)
        s.reset()
        res = s._call("Network.getAllCookies", {}, session_id=s.session_id, timeout=30)

    cookies, names = [], []
    for c in res.get("cookies", []):
        dom = (c.get("domain") or "").lstrip(".")
        if domain.lstrip(".") not in dom:
            continue
        names.append(c["name"])
        cookies.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain"),
            "path": c.get("path", "/"),
            "expires": c.get("expires", -1),
            "httpOnly": bool(c.get("httpOnly")),
            "secure": bool(c.get("secure")),
            "sameSite": {"None": "None", "Lax": "Lax", "Strict": "Strict"}.get(c.get("sameSite"), "Lax"),
        })

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"cookies": cookies, "origins": []}, indent=1))
    os.chmod(out, 0o600)

    # What matters: does the session cookie actually exist? Names only, never values.
    critical = [n for n in ("c_user", "xs", "datr") if n in names]
    return {"cookies": len(cookies), "session_cookies": critical,
            "domains": sorted({c["domain"] for c in cookies}),
            "expires_soonest": min((c["expires"] for c in cookies if c["expires"] > 0), default=-1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", help="e.g. facebook.com")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    out = Path(a.out).resolve()
    if REPO in out.parents or out.parent == REPO:
        print(f"refusing: {out} is inside the repo — cookies must never be committed",
              file=sys.stderr)
        return 2

    info = export(a.domain, out)
    if not info["cookies"]:
        print(f"no cookies for {a.domain} — is that site open and signed in?", file=sys.stderr)
        return 3
    if "c_user" not in info["session_cookies"]:
        print("warning: no c_user cookie — this may be a logged-out session", file=sys.stderr)

    print(json.dumps({"path": str(out), "mode": oct(os.stat(out).st_mode & 0o777),
                      **info}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
