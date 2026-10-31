#!/usr/bin/env python3
"""
fbthreads.py
Compact Facebook Messenger / Marketplace Threads Extractor for Hermes / Antigravity.

Outputs EXACTLY ONE LINE of clean JSON:
[{"threadid": ..., "adid": ..., "itemtitle": ..., "buyername": ..., "lastmessage": ..., "lastat": ..., "unread": ...}]

Usage:
  python3 fbthreads.py [--cdp http://127.0.0.1:9222] [--playwright]
"""

import sys
import json
import urllib.request
import asyncio
from pathlib import Path

FB_THREADS_JS = """
(async () => {
    let rows = Array.from(document.querySelectorAll('div[role="gridcell"], div[role="row"], a[href*="/messages/t/"], a[href*="/messages/e2ee/t/"]'));
    let seen = new Set();
    let threads = [];

    for (let r of rows) {
        let anchor = r.closest('a') || r.querySelector('a') || r;
        let href = (anchor.getAttribute && anchor.getAttribute('href')) || '';
        let fullText = (r.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
        if (fullText.length === 0) continue;

        let threadId = href;
        if (!threadId) {
            threadId = fullText[0];
        }
        if (seen.has(threadId)) continue;
        seen.add(threadId);

        let buyerName = fullText[0] || 'Unknown';
        if (buyerName === 'Active now' && fullText.length > 1) {
            buyerName = fullText[1];
        }

        let isUnread = fullText.some(t => t.toLowerCase().includes('unread') || t.toLowerCase().includes('new message'));
        let lastMsg = fullText.filter(t => t !== 'Active now' && t !== buyerName && !t.includes('·') && !t.toLowerCase().includes('unread')).pop() || '';
        let lastAt = fullText.find(t => t.match(/^[0-9]+[smhdwy]$/) || t.match(/^[0-9]{1,2}:[0-9]{2}/)) || '';

        threads.push({
            threadid: threadId,
            adid: "",
            itemtitle: "Marketplace / Messenger",
            buyername: buyerName,
            lastmessage: lastMsg,
            lastat: lastAt,
            unread: isUnread
        });
    }

    return threads;
})()
"""

async def run_via_cdp(cdp_url="http://127.0.0.1:9222"):
    import websockets
    try:
        req = urllib.request.urlopen(f"{cdp_url}/json", timeout=5)
        tabs = json.loads(req.read().decode())
    except Exception as e:
        return None, f"CDP connection failed: {e}"

    fb_tab = next((t for t in tabs if "facebook.com/messages" in t.get("url", "")), None)
    if not fb_tab:
        fb_tab = next((t for t in tabs if "facebook.com" in t.get("url", "")), None)
    if not fb_tab:
        return None, "No Facebook tabs found via CDP"

    ws_url = fb_tab.get("webSocketDebuggerUrl")
    if not ws_url:
        return None, "No webSocketDebuggerUrl on tab"

    async with websockets.connect(ws_url) as ws:
        current_url = fb_tab.get("url", "")
        if "facebook.com/messages" not in current_url:
            nav_cmd = {"id": 1, "method": "Page.navigate", "params": {"url": "https://www.facebook.com/messages/"}}
            await ws.send(json.dumps(nav_cmd))
            await ws.recv()
            await asyncio.sleep(3)

        eval_cmd = {
            "id": 2,
            "method": "Runtime.evaluate",
            "params": {
                "expression": FB_THREADS_JS,
                "awaitPromise": True,
                "returnByValue": True
            }
        }
        await ws.send(json.dumps(eval_cmd))
        raw = await ws.recv()
        res = json.loads(raw)
        val = res.get("result", {}).get("result", {}).get("value")
        return val, None

async def run_via_playwright(user_data_dir="./browser_profile"):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=True
        )
        page = await ctx.new_page()
        await page.goto("https://www.facebook.com/messages/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3500)
        val = await page.evaluate(FB_THREADS_JS)
        await ctx.close()
        return val

def main():
    cdp_host = "http://127.0.0.1:9222"
    use_playwright = False
    for i, arg in enumerate(sys.argv):
        if arg == "--cdp" and i + 1 < len(sys.argv):
            cdp_host = sys.argv[i + 1]
        elif arg == "--playwright":
            use_playwright = True

    data = None
    if not use_playwright:
        try:
            data, err = asyncio.run(run_via_cdp(cdp_host))
        except Exception:
            data = None

    if data is None:
        try:
            profile_dir = Path(__file__).resolve().parent / "browser_profile"
            data = asyncio.run(run_via_playwright(str(profile_dir)))
        except Exception as e:
            data = {"error": str(e)}

    # Print EXACTLY ONE LINE of compact JSON
    print(json.dumps(data, separators=(',', ':'), ensure_ascii=False))

if __name__ == "__main__":
    main()
