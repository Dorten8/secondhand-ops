#!/usr/bin/env python3
"""
update_crosslinks_dba.py
Updates descriptions on DBA.dk for all wheelsets and constituent wheels
to ensure cross-sell links are live and discoverable.
"""

import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("dba_crosslinks")

PROJECT_DIR = Path(__file__).resolve().parent
CATALOG_PATH = PROJECT_DIR / "catalog_prebuilt.json"
PROFILE_DIR = PROJECT_DIR / "browser_profile"

ITEMS_TO_UPDATE = [
    # Wheelsets
    ("WS02", "25983833"),
    ("WS03", "25983847"),
    ("WS04", "25983880"),
    # Constituent Wheels
    ("WF01", "25982219"),
    ("WF03", "25982330"),
    ("WF04", "18961393"),
    ("WF07", "25982480"),
    ("WRC01", "25983556"),
    ("WRC04", "25983618"),
    ("WRC06", "25983667"),
    ("WRS04", "25983752"),
]

async def update_item_desc(page, item_id, ad_id, new_desc):
    edit_url = f"https://www.dba.dk/recommerce/create/{ad_id}"
    logger.info(f"[{item_id}] Navigating to {edit_url} to update crosslinks...")
    await page.goto(edit_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)

    desc_area = page.locator("textarea:not([placeholder*=\"Billedtekst\"])").first
    if not await desc_area.is_visible():
        logger.error(f"[{item_id}] Description textarea not visible!")
        return False

    await desc_area.click()
    await desc_area.fill(new_desc)
    await desc_area.press("Tab")
    await page.wait_for_timeout(800)

    # Click Gem
    gem1 = page.locator("button:has-text(\"Gem\"), w-button:has-text(\"Gem\")").first
    await gem1.click()
    await page.wait_for_timeout(4000)

    if "delivery" in page.url:
        gem2 = page.locator("button:has-text(\"Gem\"), w-button:has-text(\"Gem\")").first
        await gem2.click()
        await page.wait_for_timeout(4000)

    logger.info(f"[{item_id}] Successfully committed cross-linked description to DBA ad {ad_id}!")
    return True

async def main():
    with open(CATALOG_PATH) as f:
        catalog = json.load(f)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
            viewport={"width": 1920, "height": 1080}
        )
        page = await ctx.new_page()

        for item_id, ad_id in ITEMS_TO_UPDATE:
            if item_id in catalog:
                desc = catalog[item_id]["description"]
                try:
                    await update_item_desc(page, item_id, ad_id, desc)
                    await asyncio.sleep(3)
                except Exception as e:
                    logger.error(f"[{item_id}] Failed to update: {e}")

        await ctx.close()
    logger.info("All wheelset cross-links updated on DBA!")

if __name__ == "__main__":
    asyncio.run(main())
