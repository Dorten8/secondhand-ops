#!/usr/bin/env python3
"""
Imports exported cookies (from Cookie-Editor extension in Firefox/Chrome) into ./browser_profile/
Supports cookies_dba.json, cookies_facebook.json, or cookies.json
"""

import json
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).parent / "browser_profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

def import_cookies():
    cookie_files = list(Path(__file__).parent.glob("cookies*.json"))
    if not cookie_files:
        print("❌ No cookies*.json files found in the project folder!")
        print("Export your cookies as JSON using 'Cookie-Editor' and save as:")
        print("  - cookies_dba.json")
        print("  - cookies_facebook.json")
        return

    all_cookies = []
    for cf in cookie_files:
        try:
            with open(cf, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    all_cookies.extend(data)
                    print(f"Loaded {len(data)} cookies from {cf.name}")
        except Exception as e:
            print(f"Error reading {cf.name}: {e}")

    # Standardize cookie keys for Playwright
    clean_cookies = []
    for c in all_cookies:
        cookie = {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False)
        }
        if c.get("sameSite") in ["Strict", "Lax", "None"]:
            cookie["sameSite"] = c.get("sameSite")
        clean_cookies.append(cookie)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True
        )
        context.add_cookies(clean_cookies)
        context.close()

    print(f"\n✅ Successfully imported {len(clean_cookies)} cookies into ./browser_profile/!")

if __name__ == "__main__":
    import_cookies()
