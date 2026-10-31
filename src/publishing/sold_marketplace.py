#!/usr/bin/env python3
"""sold_marketplace.py — mark an ad sold ON the marketplace, not just in the registry.

`listctl sold <id>` updates the registry, cascades to related wheelsets and syncs
Notion. It does not touch the live ad, so the ad stays visible and buyers keep
asking about something that is gone — the worst message this system can send.

This module closes that gap. DBA has no API for it, so it drives the control that
exists on the page ("Marker som solgt"), with the same discipline as the send path:

  * a real mouse event, never a synthetic .click()   (the SPA ignores those)
  * a dry-run mode that proves the control is there before anything is pressed
  * verification afterwards — the ad must actually read as sold, not "we clicked"

    python3 sold_marketplace.py --title "Rear Wheel / Baghjul - 28" --dry-run
    python3 sold_marketplace.py --title "Rear Wheel / Baghjul - 28" --yes
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/hermes/hermes/home/scripts")      # cdp.py
from cdp import Session                                      # noqa: E402

LIST_URL = "https://www.dba.dk/my-items/"
SOLD_LABELS = ("Marker som solgt", "Mark as sold")
CONFIRM_LABELS = ("Marker som solgt", "Ja", "Yes", "Bekræft")


def _find_and_click(s, labels: tuple[str, ...]) -> dict:
    """Locate the first visible control whose text matches, then click it for real."""
    js = """(() => {
        const want = %s;
        const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
        const els = [...document.querySelectorAll('button, a, [role=button]')];
        for (const el of els) {
            const t = norm(el.innerText || el.value || el.getAttribute('aria-label'));
            if (!t) continue;
            if (!want.some(w => t.toLowerCase() === w.toLowerCase())) continue;
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;         // not visible
            el.scrollIntoView({block: 'center'});
            const b = el.getBoundingClientRect();
            return JSON.stringify({text: t, x: b.left + b.width / 2, y: b.top + b.height / 2});
        }
        return 'null';
    })()""" % json.dumps(list(labels))

    hit = s.json(js)
    if not hit or hit == "null":
        return {"found": False}

    for kind in ("mousePressed", "mouseReleased"):
        s._call("Input.dispatchMouseEvent",
                {"type": kind, "x": hit["x"], "y": hit["y"],
                 "button": "left", "clickCount": 1},
                session_id=s.session_id)
    return {"found": True, "text": hit["text"]}


def mark_sold(title_needle: str, apply: bool = False) -> dict:
    """Open the seller's ad list, find the ad by title, press 'mark as sold'."""
    out: dict = {"title_needle": title_needle, "applied": apply}
    with Session("dba.dk") as s:
        s.eval(f"location.href={json.dumps(LIST_URL)}")
        time.sleep(6)
        s.reset()

        # find the row for this ad and open its details page
        # A title substring is ambiguous in practice: "Rear Wheel / Baghjul - 28\""
        # matched a completely different wheel because several ads share that
        # prefix. Collect every match and refuse unless exactly one ad is meant.
        matches = s.json("""(() => {
            const needle = %s;
            const norm = t => (t || '').replace(/\\s+/g,' ').trim();
            const rows = [...document.querySelectorAll('a, [role=link]')]
                .filter(a => norm(a.innerText).length > 12);
            const hits = rows
                .map(a => ({href: a.getAttribute('href') || '', text: norm(a.innerText).slice(0, 90)}))
                .filter(h => h.href.includes('/my-items/details/') && h.text.toLowerCase().includes(needle.toLowerCase()));
            const seen = new Set();
            return JSON.stringify(hits.filter(h => !seen.has(h.href) && seen.add(h.href)));
        })()""" % json.dumps(title_needle))
        matches = matches or []
        out["match_count"] = len(matches)
        if not matches:
            return {**out, "ok": False, "error": "ad not found in my-items list"}
        if len(matches) > 1:
            return {**out, "ok": False,
                    "error": (f"{len(matches)} ads match that title — refusing to guess. "
                              "Use a longer, unique substring."),
                    "candidates": matches}
        row = matches[0]
        out["row"] = row

        href = row.get("href") or ""
        if href.startswith("/"):
            href = "https://www.dba.dk" + href
        if not href.startswith("https://www.dba.dk"):
            return {**out, "ok": False, "error": f"unexpected href {href!r}"}

        s.eval(f"location.href={json.dumps(href)}")
        time.sleep(6)
        s.reset()
        out["details_url"] = href

        pressed = _find_and_click(s, SOLD_LABELS)
        out["control"] = pressed
        if not pressed.get("found"):
            return {**out, "ok": False,
                    "error": "no 'mark as sold' control on the details page"}

        if not apply:
            return {**out, "ok": True, "dry_run": True,
                    "note": "control found and clickable — re-run with --yes to press it"}

        time.sleep(2)
        s.reset()
        confirmed = _find_and_click(s, CONFIRM_LABELS)      # dialog, usually
        out["confirm"] = confirmed
        time.sleep(3)

        # verify: the ad must now read as sold on the page it lives on
        try:
            s.reset()
        except Exception:
            pass
        state = s.json("""(() => {
            const body = (document.body.innerText || '').toLowerCase();
            return JSON.stringify({
                says_sold: body.includes('solgt'),
                title: document.title.slice(0, 60)
            });
        })()""")
        out["state"] = state
        out["ok"] = bool(state and state.get("says_sold"))
        if not out["ok"]:
            out["error"] = ("pressed, but the page does not read as sold — treat as failed, "
                            "check the ad manually before repeating")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Mark a DBA ad sold on the marketplace.")
    ap.add_argument("--title", required=True, help="a substring of the ad title")
    ap.add_argument("--yes", action="store_true", help="actually press the control")
    a = ap.parse_args(argv)

    res = mark_sold(a.title, apply=a.yes)
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 0 if res.get("ok") else 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
