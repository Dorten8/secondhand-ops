#!/usr/bin/env python3
"""
fix_past_dba_listings.py
Updates all existing DBA listings to ensure:
1. Exact asking price is set and displayed.
2. Postal code is strictly 2300 (København S).
"""

import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("dba_fixer")

PROJECT_DIR = Path(__file__).resolve().parent
CATALOG_FILE = PROJECT_DIR / "catalog_prebuilt.json"
ITEMS_DIR = PROJECT_DIR / "items"

async def fix_ad(page, item_id, ad_id, price):
    edit_url = f"https://www.dba.dk/recommerce/create/{ad_id}"
    logger.info(f"[{item_id}] Navigating to {edit_url} (Setting {price} kr., 2300)...")
    await page.goto(edit_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2500)

    # 1. Fill Price and Postnr
    num_inps = await page.locator("input[type=\"number\"]").all()
    if len(num_inps) < 2:
        logger.error(f"[{item_id}] Could not find 2 number inputs on {edit_url}")
        return False

    # Price
    await num_inps[0].click()
    await num_inps[0].fill(str(price))
    await num_inps[0].press("Tab")

    # Postnr
    await num_inps[1].click()
    await num_inps[1].fill("2300")
    await num_inps[1].press("Tab")
    await page.wait_for_timeout(800)

    # Click Gem on Step 1
    gem1 = page.locator("button:has-text(\"Gem\"), w-button:has-text(\"Gem\")").first
    await gem1.click()
    await page.wait_for_timeout(3500)

    # Step 2: Delivery
    if "delivery" in page.url:
        # Ensure 'Jeg kan ikke sende varen' is checked
        lbl = await page.query_selector("text=\"Jeg kan ikke sende varen\"")
        if lbl:
            await lbl.click()
            await page.wait_for_timeout(500)

        gem2 = page.locator("button:has-text(\"Gem\"), w-button:has-text(\"Gem\")").first
        await gem2.click()
        await page.wait_for_timeout(3500)

    logger.info(f"[{item_id}] Successfully updated DBA ad {ad_id} to {price} kr. / 2300!")
    return True

async def main():
    with open(CATALOG_FILE) as f:
        catalog = json.load(f)

    # Find all items that have dba_url
    to_fix = []
    for item_id, item in sorted(catalog.items()):
        meta_path = ITEMS_DIR / item_id / "metadata.json"
        if meta_path.exists():
            with open(meta_path) as mf:
                mdata = json.load(mf)
                dba_url = mdata.get("dba_url")
                if dba_url and "/recommerce/forsale/item/" in dba_url:
                    ad_id = dba_url.split("/recommerce/forsale/item/")[-1].split("?")[0]
                    to_fix.append((item_id, ad_id, item["price"]))

    logger.info(f"Found {len(to_fix)} existing DBA ads to fix.")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROJECT_DIR / "browser_profile"),
            headless=True,
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        for item_id, ad_id, price in to_fix:
            try:
                await fix_ad(page, item_id, ad_id, price)
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"[{item_id}] Error fixing ad {ad_id}: {e}")

        await context.close()

    logger.info("All past DBA ads have been updated!")

if __name__ == "__main__":
    asyncio.run(main())
