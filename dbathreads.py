#!/usr/bin/env python3
"""
dbathreads.py
Compact DBA Message Threads Extractor for Hermes / Antigravity.

Outputs EXACTLY ONE LINE of clean JSON:
[{"threadid": ..., "adid": ..., "itemtitle": ..., "buyername": ..., "lastmessage": ..., "lastat": ..., "unread": ...}]

Usage:
  python3 dbathreads.py [--cdp http://127.0.0.1:9222] [--playwright]
"""

import sys
import json
import urllib.request
import asyncio
from pathlib import Path

# JavaScript snippet that executes within the logged-in dba.dk session context:
DBA_THREADS_JS = """
(async () => {
    let loginId = window.configs?.app?.loginId;
    if (!loginId) {
        const meta = document.querySelector('meta[name="nmp:tracking:login-id"]');
        if (meta) loginId = meta.content;
    }
    if (!loginId) {
        return { error: "Not logged into DBA or not on dba.dk page" };
    }
    const resp = await fetch('/messages/api/conversations/users/' + loginId + '/conversationgroups?offset=0&limit=30&numberOfConversationsInGroup=10');
    if (!resp.ok) {
        return { error: "DBA API error: " + resp.status };
    }
    const data = await resp.json();
    const threads = [];
    for (const g of data) {
        const item = g.groupBasis?.itemInfo || {};
        for (const c of (g.conversations || [])) {
            threads.push({
                threadid: c.conversationId,
                adid: item.itemId || c.itemId,
                itemtitle: item.title || "",
                buyername: c.partnerName || "Køber",
                lastmessage: c.lastMessagePreview || g.lastMessagePreview || "",
                lastat: c.lastMessageDate || g.lastMessageDate || "",
                unread: (c.unseenCounter || 0) > 0
            });
        }
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

    dba_tab = next((t for t in tabs if "dba.dk" in t.get("url", "")), None)
    if not dba_tab:
        dba_tab = next((t for t in tabs if t.get("type") == "page"), None)
    if not dba_tab:
        return None, "No active browser tabs found via CDP"

    ws_url = dba_tab.get("webSocketDebuggerUrl")
    if not ws_url:
        return None, "No webSocketDebuggerUrl on tab"

    async with websockets.connect(ws_url) as ws:
        # Check if current URL is dba.dk, if not navigate
        current_url = dba_tab.get("url", "")
        if "dba.dk" not in current_url:
            nav_cmd = {"id": 1, "method": "Page.navigate", "params": {"url": "https://www.dba.dk/messages/"}}
            await ws.send(json.dumps(nav_cmd))
            await ws.recv()
            await asyncio.sleep(2)

        eval_cmd = {
            "id": 2,
            "method": "Runtime.evaluate",
            "params": {
                "expression": DBA_THREADS_JS,
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
        await page.goto("https://www.dba.dk/messages/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        val = await page.evaluate(DBA_THREADS_JS)
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
