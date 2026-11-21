#!/usr/bin/env python3
"""
fb_publish.py — publish one item to Facebook Marketplace through the live browser.

Same philosophy as dba_publish.py: the browser that already holds the Facebook
session is the credential, so we attach over CDP instead of storing cookies or
using Playwright.

Facebook specifics (all learned the hard way, all silent when wrong):
  * "Category" and "Condition" are combobox roles, and the words "Electronics" /
    "Mobile phones" appear BOTH as section headers (aria-label "Header …") and as
    selectable rows. Clicking the header scrolls the list and selects nothing.
    Pick the leaf whose text matches and which is NOT a header.
  * Title/Price need focus + Input.insertText. Assigning .value leaves the field
    visually filled but unsaved.
  * Clicking anything with a stale rect lands on the wrong element — and on
    Facebook that can navigate the tab away and lose the whole draft. Always
    scroll → re-measure → click.
  * The description lives on the second step, behind "Next".

Usage:
    python3 bin/fb_publish.py --json item.json --dry-run
    python3 bin/fb_publish.py --json item.json --confirm
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Browser  # noqa: E402

CREATE_URL = "https://www.facebook.com/marketplace/create/item"


class FB:
    def __init__(self, page, log=print):
        self.page = page
        self.log = log

    async def click_at(self, x, y):
        for ev in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.page._send(
                "Input.dispatchMouseEvent",
                {"type": ev, "x": x, "y": y, "button": "left", "clickCount": 1},
            )
            await asyncio.sleep(0.12)

    async def rect_of(self, js_finder):
        """Run a finder that scrolls + returns a rect, twice, to survive re-layout."""
        first = await self.page.eval(js_finder)
        if not first or "x" not in first:
            return None
        await asyncio.sleep(0.4)
        second = await self.page.eval(js_finder)
        return second if second and "x" in second else first

    async def click_text(self, text, scope="document", header_ok=False):
        """Click the leaf whose trimmed text equals `text`, skipping section headers."""
        finder = """(() => {
          const want = %s;
          const scope = %s === 'dialog'
            ? ([...document.querySelectorAll('[role="dialog"]')].pop() || document)
            : document;
          const leaves = [...scope.querySelectorAll('*')].filter(e =>
              e.children.length === 0 && (e.innerText || '').trim() === want);
          const usable = leaves.filter(e => {
            const aria = (e.getAttribute('aria-label') || '') + ((e.parentElement && e.parentElement.getAttribute('aria-label')) || '');
            return %s ? true : !/^Header /.test(aria);
          });
          if (!usable.length) return {err: 'not found'};
          const el = usable[usable.length - 1];
          el.scrollIntoView({block: 'center'});
          const r = el.getBoundingClientRect();
          return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
        })()""" % (json.dumps(text), json.dumps(scope), "true" if header_ok else "false")
        rect = await self.rect_of(finder)
        if not rect:
            return {"clicked": False, "reason": f"no leaf {text!r}"}
        await self.click_at(rect["x"], rect["y"])
        return {"clicked": True, **rect}

    async def open_combobox(self, label):
        finder = """(() => {
          const cb = [...document.querySelectorAll('[role="combobox"]')]
              .find(c => (c.innerText || '').includes(%s));
          if (!cb) return {err: 'no combobox'};
          cb.scrollIntoView({block: 'center'});
          const r = cb.getBoundingClientRect();
          return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
        })()""" % json.dumps(label)
        rect = await self.rect_of(finder)
        if not rect:
            return {"clicked": False, "reason": f"no combobox {label!r}"}
        await self.click_at(rect["x"], rect["y"])
        await asyncio.sleep(2)
        return {"clicked": True}

    async def upload(self, files):
        doc = await self.page._send("DOM.getDocument", {"depth": 0})
        q = await self.page._send(
            "DOM.querySelector",
            {"nodeId": doc["root"]["nodeId"], "selector": "input[type=file]"},
        )
        if not q.get("nodeId"):
            return {"ok": False, "reason": "no file input"}
        await self.page._send("DOM.setFileInputFiles", {"files": files, "nodeId": q["nodeId"]})
        return {"ok": True, "count": len(files)}

    async def type_into(self, index, text, kind="text"):
        finder = """(() => {
          const els = [...document.querySelectorAll('input[type=%s]')];
          const el = els[%d];
          if (!el) return {err: 'missing input'};
          el.scrollIntoView({block: 'center'});
          const r = el.getBoundingClientRect();
          return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
        })()""" % (kind, index)
        rect = await self.rect_of(finder)
        if not rect:
            return {"ok": False, "reason": f"no {kind} input {index}"}
        await self.click_at(rect["x"], rect["y"])
        await self.page._send("Input.insertText", {"text": text})
        return {"ok": True}

    async def combos(self):
        return await self.page.eval(
            """(() => [...document.querySelectorAll('[role="combobox"]')]
                .map(c => (c.innerText || '').replace(/\\n/g, ' | ').trim().slice(0, 45)))()"""
        )

    async def texts(self):
        return await self.page.eval(
            """(() => [...document.querySelectorAll('input[type=text]')].map(i => i.value))()"""
        )

    async def state(self):
        return {
            "url": await self.page.eval("location.href"),
            "combos": await self.combos(),
            "inputs": await self.texts(),
            "textareas": await self.page.eval("document.querySelectorAll('textarea').length"),
            "body": (await self.page.eval("(() => (document.body ? document.body.innerText.slice(0,300) : \"\"))()")) or "",
        }


async def run(args):
    item = json.loads(Path(args.json).read_text())
    browser = Browser(args.endpoint)
    page = browser.new_page("https://www.facebook.com/marketplace/")
    await page._open()
    fb = FB(page)
    try:
        # Do NOT navigate straight to /marketplace/create/item: Facebook redirects a
        # directly-loaded create page to Messenger within ~5 seconds, which loses the
        # whole draft. Arriving by client-side routing from /marketplace/ is stable.
        await page.goto("https://www.facebook.com/marketplace/", wait=8)
        st = await fb.state()
        print("landing:", st["url"])
        print("create:", await fb.click_text("Create new listing", header_ok=True))
        await asyncio.sleep(4)
        print("item for sale:", await fb.click_text("Item for sale", header_ok=True))
        await asyncio.sleep(6)
        st = await fb.state()
        print("form:", st["url"])
        if "create/item" not in st["url"]:
            print("STOP: did not reach the listing form", st["url"])
            print("shot:", await page.shot("/tmp/fb_stop.png"))
            return 1

        print("photos:", await fb.upload(item["photos"]))
        await asyncio.sleep(12)

        print("title:", await fb.type_into(0, item["title"]))
        print("price:", await fb.type_into(1, str(item["price"])))
        await asyncio.sleep(1.5)

        print("category:", await fb.open_combobox("Category"))
        print(" ->", await fb.click_text(item["category"], scope="dialog"))
        await asyncio.sleep(2.5)

        print("condition:", await fb.open_combobox("Condition"))
        print(" ->", await fb.click_text(item["condition"], scope="dialog"))
        await asyncio.sleep(2.5)

        st = await fb.state()
        print("state:", json.dumps({k: st[k] for k in ("combos", "inputs")}, ensure_ascii=False))
        print("shot:", await page.shot("/tmp/fb_step1.png"))

        if args.dry_run:
            print("DRY RUN — stopping before Next. Inspect /tmp/fb_step1.png")
            return 0

        print("next:", await fb.click_text("Next", header_ok=True))
        await asyncio.sleep(6)
        st = await fb.state()
        print("after Next:", st["url"], "| textareas:", st["textareas"])
        print("shot:", await page.shot("/tmp/fb_step2.png"))

        # step 2: description
        if st["textareas"]:
            finder = """(() => {
              const ta = document.querySelector('textarea');
              if (!ta) return {err: 'none'};
              ta.scrollIntoView({block: 'center'});
              const r = ta.getBoundingClientRect();
              return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
            })()"""
            rect = await fb.rect_of(finder)
            if rect:
                await fb.click_at(rect["x"], rect["y"])
                await page._send("Input.insertText", {"text": item["description"]})
                print("description inserted")
                await asyncio.sleep(2)

        st = await fb.state()
        print("pre-publish state:", json.dumps(st, ensure_ascii=False)[:400])
        print("shot:", await page.shot("/tmp/fb_ready.png"))

        if not args.confirm:
            print("Not clicked Publish — rerun with --confirm")
            return 0

        print("publish:", await fb.click_text("Publish", header_ok=True))
        await asyncio.sleep(12)
        print("final url:", await page.eval("location.href"))
        print("shot:", await page.shot("/tmp/fb_published.png"))
        return 0
    finally:
        await page.close()


def main():
    ap = argparse.ArgumentParser(description="Publish one item to Facebook Marketplace.")
    ap.add_argument("--json", required=True)
    ap.add_argument("--endpoint", default="http://127.0.0.1:9222")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# KNOWN LIMITATION (measured 2026-11-21, do not treat as a bug to "fix" by retrying)
#
# Facebook Marketplace publishing from this box is only intermittently reachable:
#
#   * a directly-loaded /marketplace/create/item survives ~5 s, then the tab is
#     redirected to /messages/t/<id> and the draft is lost;
#   * after a handful of fresh tabs, even /marketplace/ redirects to Messenger;
#   * the redirect target id changes on every interaction, i.e. it is a soft block,
#     not a route.
#
# Conclusion: arriving by client-side routing (click "Create new listing" from an
# already-open Marketplace tab) is the only path that worked, and once the soft
# block engages it stops working too. Do NOT retry in a loop — that is how a
# selling account earns a restriction. When this happens: stop, report, and let a
# human restart the browser session or list it by hand.
#
# DBA has no equivalent behaviour: its wizard is stable over dozens of navigations.
# ---------------------------------------------------------------------------
