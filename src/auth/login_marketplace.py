#!/usr/bin/env python3
"""
Interactive Marketplace Authentication Helper
Launches Chromium on your display (:1) so you can log in to DBA.dk and Facebook once.
The session is saved permanently in ./browser_profile/ for automated publishing.
"""

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).parent / "browser_profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

def launch_login():
    print("\n" + "="*65)
    print("🚀 LAUNCHING CHROMIUM TO SAVE YOUR MARKETPLACE SESSIONS")
    print("="*65)
    print("A browser window will open on your screen.")
    print("• Tab 1: DBA.dk (Log in to your DBA account)")
    print("• Tab 2: Facebook Marketplace (Log in to your Facebook account)")
    print("• Tip: Check 'Husk mig' / 'Remember me'")
    print("="*65 + "\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            args=[
                "--start-maximized",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled"
            ],
            viewport=None
        )

        page1 = context.pages[0] if context.pages else context.new_page()
        page1.goto("https://www.dba.dk/min-side/log-ind/")

        page2 = context.new_page()
        page2.goto("https://www.facebook.com/marketplace")

        print("👉 The browser window is now open on your desktop.")
        print("👉 Log in on both tabs. When you are finished, press ENTER here:")
        input("\n[PRESS ENTER ONCE LOGGED IN TO SAVE & EXIT] > ")

        context.close()
        print("\n✅ Session successfully saved to ./browser_profile/!")
        print("Playwright automation can now publish directly using your authenticated session.")

if __name__ == "__main__":
    launch_login()
