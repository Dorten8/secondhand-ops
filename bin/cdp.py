#!/usr/bin/env python3
"""
cdp.py — minimal Chrome DevTools Protocol driver over the live browser.

No Playwright: the browser on this box is already running and already logged in,
so all that is needed is a websocket and a few commands. Deliberately small —
eval JS, click by text, fill inputs, upload files, screenshot.

Usage as a library:
    from cdp import Browser
    b = Browser()
    page = b.new_page("https://www.dba.dk/create-item/start")
    print(page.eval("document.title"))

CLI:
    python3 bin/cdp.py [--url URL] --eval "JS"
    python3 bin/cdp.py --url URL --dump        # selects/options/inputs on the page
    python3 bin/cdp.py --url URL --shot /tmp/x.png
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import urllib.request

import websockets

DEFAULT_ENDPOINT = "http://127.0.0.1:9222"


def _http(path: str, method: str = "GET", endpoint: str = DEFAULT_ENDPOINT) -> dict:
    req = urllib.request.Request(endpoint + path, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read().decode() or "null"
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw}


class Page:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._id = 0

    async def _send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = json.dumps({"id": self._id, "method": method, "params": params or {}})
        await self.ws.send(msg)
        while True:
            data = json.loads(await self.ws.recv())
            if data.get("id") == self._id:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})

    async def _open(self):
        self.ws = await websockets.connect(self.ws_url, max_size=64 * 1024 * 1024)

    async def close(self):
        await self.ws.close()

    async def goto(self, url: str, wait: float = 3.0):
        await self._send("Page.enable")
        await self._send("Page.navigate", {"url": url})
        await asyncio.sleep(wait)

    async def eval(self, expression: str):
        r = await self._send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("text", "js error"))
        return r.get("result", {}).get("value")

    async def shot(self, path: str):
        r = await self._send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        return path

    async def upload(self, selector: str, files: list[str]):
        """DOM.setFileInputFiles — the only reliable way to fill a file input."""
        doc = await self._send("DOM.getDocument", {"depth": 0})
        node = await self._send(
            "DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": selector}
        )
        node_id = node.get("nodeId")
        if not node_id:
            raise RuntimeError(f"no element matches {selector}")
        await self._send("DOM.setFileInputFiles", {"files": files, "nodeId": node_id})


class Browser:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT):
        self.endpoint = endpoint

    def new_page(self, url: str = "about:blank") -> Page:
        t = _http("/json/new?" + urllib.parse.quote(url, safe=":/?=&"), "PUT", self.endpoint)
        return Page(t["webSocketDebuggerUrl"])

    def attach(self, url_contains: str) -> Page:
        for t in _http("/json/list", endpoint=self.endpoint):
            if t.get("type") == "page" and url_contains in (t.get("url") or ""):
                return Page(t["webSocketDebuggerUrl"])
        raise RuntimeError(f"no page matching {url_contains!r}")


import urllib.parse  # noqa: E402  (used by new_page)


DUMP_JS = r"""(() => {
  const out = {url: location.href, title: document.title, selects: [], inputs: [], textareas: [], buttons: []};
  document.querySelectorAll('select').forEach((s,i) => {
    out.selects.push({i, name: s.name || s.id || null, value: s.value,
      options: Array.from(s.options).slice(0,25).map(o => ({v:o.value, t:o.text.trim()}))});
  });
  document.querySelectorAll('input').forEach((inp,i) => {
    out.inputs.push({i, type: inp.type, name: inp.name || null, id: inp.id || null,
                     placeholder: inp.placeholder || null, value: (inp.value||'').slice(0,40)});
  });
  document.querySelectorAll('textarea').forEach((t,i) => out.textareas.push({i, name:t.name||null, id:t.id||null, len:(t.value||'').length}));
  document.querySelectorAll('button, a, [role="button"], w-button').forEach((b,i) => {
    const txt = (b.innerText || '').trim();
    if (txt && txt.length < 40) out.buttons.push(txt);
  });
  out.buttons = [...new Set(out.buttons)].slice(0, 40);
  return out;
})()"""


async def _amain(args):
    b = Browser()
    page = b.new_page(args.url) if args.url else b.attach(args.attach)
    await page._open()
    try:
        if args.url:
            await page.goto(args.url, wait=args.wait)
        if args.dump:
            print(json.dumps(await page.eval(DUMP_JS), indent=2, ensure_ascii=False))
        if args.eval:
            print(json.dumps(await page.eval(args.eval), indent=2, ensure_ascii=False))
        if args.shot:
            print("shot:", await page.shot(args.shot))
    finally:
        await page.close()


def main():
    ap = argparse.ArgumentParser(description="Drive the live CDP browser.")
    ap.add_argument("--url")
    ap.add_argument("--attach", help="attach to an existing page whose URL contains this")
    ap.add_argument("--eval")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--shot")
    ap.add_argument("--wait", type=float, default=3.0)
    args = ap.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
