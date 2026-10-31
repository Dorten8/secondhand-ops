#!/usr/bin/env python3
"""
dbathread.py <id>
Compact DBA Single Thread Extractor for Hermes / Antigravity.

Outputs EXACTLY ONE LINE of clean JSON:
{"threadid": ..., "item": ..., "buyer": ..., "messages": [{"from": ..., "at": ..., "text": ...}]}

Usage:
  python3 dbathread.py <thread_id> [--cdp http://127.0.0.1:9222] [--playwright]
"""

import sys
import json
import urllib.request
import asyncio
from pathlib import Path

def make_thread_js(thread_id):
    return f"""
(async () => {{
    let loginId = window.configs?.app?.loginId;
    if (!loginId) {{
        const meta = document.querySelector('meta[name="nmp:tracking:login-id"]');
        if (meta) loginId = meta.content;
    }}
    if (!loginId) {{
        return {{ error: "Not logged into DBA or not on dba.dk page" }};
    }}
    const threadId = "{thread_id}";
    const resp = await fetch('/messages/api/conversations/users/' + loginId + '/conversations/' + threadId + '/messages?order=asc&size=50');
    if (!resp.ok) {{
        return {{ error: "DBA API error: " + resp.status }};
    }}
    const data = await resp.json();
    const msgs = (data.messageResponseList || []).map(m => ({{
        from: m.outgoing ? "Jacob" : "Buyer",
        at: m.sent || "",
        text: m.body || ""
    }}));

    return {{
        threadid: threadId,
        messages: msgs
    }};
}})()
"""

async def run_via_cdp(thread_id, cdp_url="http://127.0.0.1:9222"):
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
                "expression": make_thread_js(thread_id),
                "awaitPromise": True,
                "returnByValue": True
            }
        }
        await ws.send(json.dumps(eval_cmd))
        raw = await ws.recv()
        res = json.loads(raw)
        val = res.get("result", {}).get("result", {}).get("value")
        return val, None

async def run_via_playwright(thread_id, user_data_dir="./browser_profile"):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=True
        )
        page = await ctx.new_page()
        await page.goto("https://www.dba.dk/messages/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        val = await page.evaluate(make_thread_js(thread_id))
        await ctx.close()
        return val

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: dbathread.py <thread_id> [--cdp URL] [--playwright]"}))
        sys.exit(1)

    thread_id = sys.argv[1]
    cdp_host = "http://127.0.0.1:9222"
    use_playwright = False

    for i, arg in enumerate(sys.argv[2:], start=2):
        if arg == "--cdp" and i + 1 < len(sys.argv):
            cdp_host = sys.argv[i + 1]
        elif arg == "--playwright":
            use_playwright = True

    data = None
    if not use_playwright:
        try:
            data, err = asyncio.run(run_via_cdp(thread_id, cdp_host))
        except Exception:
            data = None

    if data is None:
        try:
            profile_dir = Path(__file__).resolve().parent / "browser_profile"
            data = asyncio.run(run_via_playwright(thread_id, str(profile_dir)))
        except Exception as e:
            data = {"error": str(e)}

    # Print EXACTLY ONE LINE of compact JSON
    print(json.dumps(data, separators=(',', ':'), ensure_ascii=False))

if __name__ == "__main__":
    main()
