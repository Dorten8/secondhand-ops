#!/usr/bin/env python3
"""
batch_publisher.py
Autonomous batch listing publisher for Facebook Marketplace and DBA.dk.
Executes completely locally in the background without conversational token roundtrips.
Logs live progress to BATCH_PUBLISHING_PROGRESS.md and stdout.

CRITICAL AGENTIC OPERATING RULE:
Web pages (DBA.dk & Facebook Marketplace) frequently change their DOM and UI flow.
Any LLM agent executing or amending this automation MUST perform a supervised,
single-item first-run inspection (e.g. `python3 batch_publisher.py --item <ID>`)
with screenshot verification before launching bulk automation. If UI elements or
validation rules change, the agent must amend this script and confirm the fix first.
"""

import os
import sys
import json
import time
import argparse
import asyncio
import logging
import requests
from pathlib import Path
from playwright.async_api import async_playwright

# Setup logging
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("batch_publishing.log", mode="a")
    ]
)
logger = logging.getLogger("batch_publisher")

PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / ".env"
CATALOG_FILE = PROJECT_DIR / "catalog_prebuilt.json"
ITEMS_DIR = PROJECT_DIR / "items"
PROGRESS_MD = PROJECT_DIR / "BATCH_PUBLISHING_PROGRESS.md"

def load_config():
    cfg = {}
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()
    return cfg

CONFIG = load_config()
NOTION_TOKEN = CONFIG.get("NOTION_TOKEN")
NOTION_DB_ID = CONFIG.get("NOTION_DATABASE_ID")

def update_notion_item(item_id, dba_url=None, fb_url=None):
    if not NOTION_TOKEN or not NOTION_DB_ID:
        logger.warning("Notion config missing, skipping Notion update")
        return False
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    query_url = f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query"
    try:
        r = requests.post(query_url, headers=headers, json={"filter": {"property": "DB_number", "rich_text": {"equals": item_id}}}, timeout=15)
        res = r.json()
        if res.get("results"):
            pid = res["results"][0]["id"]
            props = res["results"][0]["properties"]
            patch_payload = {"properties": {}}
            if "DBA URL" in props and dba_url:
                patch_payload["properties"]["DBA URL"] = {"url": dba_url}
            if "Facebook URL" in props and fb_url:
                patch_payload["properties"]["Facebook URL"] = {"url": fb_url}
            if "STOCK" in props:
                patch_payload["properties"]["STOCK"] = {"select": {"name": "LISTED"}}
            
            patch_url = f"https://api.notion.com/v1/pages/{pid}"
            r_patch = requests.patch(patch_url, headers=headers, json=patch_payload, timeout=15)
            logger.info(f"Notion updated for #{item_id}: HTTP {r_patch.status_code}")
            return True
        else:
            logger.warning(f"Item #{item_id} not found in Notion database")
            return False
    except Exception as e:
        logger.error(f"Error updating Notion for #{item_id}: {e}")
        return False

def update_local_metadata(item_id, dba_url=None, fb_url=None):
    meta_path = ITEMS_DIR / item_id / "metadata.json"
    if meta_path.exists():
        try:
            with open(meta_path) as f:
                data = json.load(f)
            if dba_url:
                data["dba_url"] = dba_url
            if fb_url:
                data["fb_url"] = fb_url
            if data.get("dba_url") and data.get("fb_url"):
                data["status"] = "LISTED"
            with open(meta_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Updated local metadata.json for #{item_id}")
        except Exception as e:
            logger.error(f"Error updating metadata.json for #{item_id}: {e}")

def update_progress_report(catalog):
    listed_count = 0
    total_count = len(catalog)
    rows = []
    
    for item_id, item in sorted(catalog.items()):
        meta_path = ITEMS_DIR / item_id / "metadata.json"
        dba_url = item.get("dba_url")
        fb_url = item.get("fb_url")
        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    m = json.load(f)
                    dba_url = dba_url or m.get("dba_url")
                    fb_url = fb_url or m.get("fb_url")
            except:
                pass

        is_listed = bool(dba_url and fb_url)
        if is_listed:
            listed_count += 1
            status = "✅ LIVE"
        elif dba_url or fb_url:
            status = "🟡 PARTIAL"
        else:
            status = "⏳ QUEUED"

        dba_link = f"[DBA Link]({dba_url})" if dba_url else "—"
        fb_link = f"[FB Link]({fb_url})" if fb_url else "—"
        rows.append(f"| `#{item_id}` | {item['title'][:45]} | {item['price']} kr | {status} | {dba_link} | {fb_link} |")

    pct = int(listed_count / total_count * 100) if total_count else 0
    report = f"""# 🚀 Automated Marketplace Batch Publishing Progress

**Overall Progress:** {listed_count} / {total_count} items published ({pct}%)
**Last Updated:** {time.strftime('%Y-%m-%d %H:%M:%S')}

| Item ID | Title (`WHAT - WHATTYPE - MAKE`) | Price | Status | DBA.dk Listing | Facebook Marketplace |
| :--- | :--- | :--- | :---: | :---: | :---: |
""" + "\n".join(rows) + "\n"

    try:
        PROGRESS_MD.write_text(report)
    except Exception as e:
        logger.error(f"Error writing progress report: {e}")

async def publish_item_dba(context, item):
    item_id = item["item_id"]
    title = item["title"]
    price = str(item["price"])
    desc = item["description"]
    photos = item["photos"]

    page = await context.new_page()
    ad_id = None
    try:
        logger.info(f"[{item_id}] DBA: Navigating to create page...")
        await page.goto("https://www.dba.dk/create-item/start", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2500)

        # Click 'Markedspladsen'
        mk_btn = await page.query_selector("text=Markedspladsen")
        if not mk_btn:
            raise Exception("Markedspladsen button not found on /create-item/start")
        await mk_btn.click()
        await page.wait_for_timeout(3000)

        cur_url = page.url
        logger.info(f"[{item_id}] DBA: Current URL after Markedspladsen: {cur_url}")
        if "/recommerce/create/" in cur_url:
            ad_id = cur_url.split("/recommerce/create/")[-1].split("?")[0]
        else:
            raise Exception(f"Unexpected DBA create URL: {cur_url}")

        logger.info(f"[{item_id}] DBA Ad ID: {ad_id}")

        # 1. Categories
        selects = await page.locator("select").all()
        if len(selects) >= 1:
            await selects[0].select_option("69") # Sport og friluftsliv
            await page.wait_for_timeout(1000)

        selects = await page.locator("select").all()
        if len(selects) >= 2:
            await selects[1].select_option("3963") # Cykling
            await page.wait_for_timeout(1000)

        selects = await page.locator("select").all()
        if len(selects) >= 3:
            await selects[2].select_option("258") # Cykeludstyr
            await page.wait_for_timeout(1000)

        # Stand -> Brugt - men i god stand
        selects = await page.locator("select").all()
        if len(selects) >= 4:
            opts = await selects[3].locator("option").all()
            for o in opts:
                txt = (await o.inner_text()).lower()
                if "god stand" in txt or "brugt" in txt:
                    val = await o.get_attribute("value")
                    await selects[3].select_option(val)
                    break

        # 2. Photos upload
        if photos:
            file_inp = await page.query_selector("input[type=\"file\"]")
            if file_inp:
                logger.info(f"[{item_id}] DBA: Uploading {len(photos)} photos...")
                await file_inp.set_input_files(photos)
                await page.wait_for_timeout(4500)

        # 3. Title
        t_inp = page.locator("input[name=\"title\"]")
        if await t_inp.count() == 0:
            t_inp = page.locator("input#textfield").first
        await t_inp.fill(title)

        # 4. Description
        tas = await page.locator("textarea").all()
        if tas:
            for ta in tas[:-1]:
                await ta.fill("")
            await tas[-1].fill(desc)
            await tas[-1].evaluate("el => el.scrollTop = 0")

        # 5. Price & Postal Code (STRICTLY 2300 København S)
        num_inps = await page.locator("input[type=\"number\"]").all()
        if len(num_inps) >= 2:
            # Price
            await num_inps[0].click()
            await num_inps[0].fill(price)
            await num_inps[0].press("Tab")
            # Postal Code
            await num_inps[1].click()
            await num_inps[1].fill("2300")
            await num_inps[1].press("Tab")
            logger.info(f"[{item_id}] DBA: Set price={price} kr. and postnr=2300")
        elif len(num_inps) == 1:
            await num_inps[0].click()
            await num_inps[0].fill(price)
            await num_inps[0].press("Tab")

        await page.wait_for_timeout(1500)

        # Click 'Fortsæt' on Step 1
        logger.info(f"[{item_id}] DBA: Submitting Step 1...")
        step1_res = await page.evaluate('''() => {
            const all = Array.from(document.querySelectorAll('*'));
            const el = all.find(e => e.children.length === 0 && e.innerText && e.innerText.trim() === 'Fortsæt');
            if (el) {
                (el.closest('button, a, [role="button"], w-button') || el).click();
                return true;
            }
            return false;
        }''')
        if not step1_res:
            raise Exception("Failed to click 'Fortsæt' on Step 1")

        await page.wait_for_timeout(3500)

        # Step 2: Delivery
        logger.info(f"[{item_id}] DBA: Handling Step 2 (Delivery)...")
        lbl = await page.query_selector("text=\"Jeg kan ikke sende varen\"")
        if lbl:
            await lbl.click()
            await page.wait_for_timeout(800)

        step2_res = await page.evaluate('''() => {
            const all = Array.from(document.querySelectorAll('*'));
            const el = all.find(e => e.children.length === 0 && e.innerText && e.innerText.trim() === 'Fortsæt');
            if (el) {
                (el.closest('button, a, [role="button"], w-button') || el).click();
                return true;
            }
            return false;
        }''')
        await page.wait_for_timeout(3500)

        # Step 3: Package selection (Basis - 0 kr.)
        logger.info(f"[{item_id}] DBA: Handling Step 3 (Package Basis)...")
        basis_el = await page.query_selector("text=\"Basis\"")
        if basis_el:
            await basis_el.click()
            await page.wait_for_timeout(1200)

        # Click 'Opret annoncen'
        opret_res = await page.evaluate('''() => {
            const all = Array.from(document.querySelectorAll('*'));
            const el = all.find(e => e.innerText && e.innerText.trim() === 'Opret annoncen');
            if (el) {
                (el.closest('button, a, [role="button"], w-button') || el).click();
                return true;
            }
            return false;
        }''')
        if not opret_res:
            raise Exception("Failed to click 'Opret annoncen' on Step 3")

        await page.wait_for_timeout(5000)

        live_dba_url = f"https://www.dba.dk/recommerce/forsale/item/{ad_id}"
        logger.info(f"[{item_id}] DBA LIVE: {live_dba_url}")
        return live_dba_url

    except Exception as e:
        logger.error(f"[{item_id}] DBA publish error: {e}")
        try:
            err_shot = f"/tmp/dba_err_{item_id}.png"
            await page.screenshot(path=err_shot)
            logger.info(f"Saved error screenshot to {err_shot}")
        except:
            pass
        return None
    finally:
        await page.close()

async def publish_item_fb(context, item):
    item_id = item["item_id"]
    title = item["title"]
    price = str(item["price"])
    desc = item["description"]
    photos = item["photos"]
    w_size = item.get("fb_wheel_size")
    mat = item.get("fb_material")
    b_type = item.get("fb_type")
    brand = item.get("fb_brand")

    page = await context.new_page()
    try:
        logger.info(f"[{item_id}] FB: Navigating to create item page...")
        await page.goto("https://www.facebook.com/marketplace/create/item", wait_until="domcontentloaded", timeout=35000)
        await page.wait_for_timeout(3500)

        # 1. Photos
        if photos:
            file_inp = await page.query_selector("input[type=\"file\"]")
            if file_inp:
                logger.info(f"[{item_id}] FB: Uploading {len(photos)} photos...")
                await file_inp.set_input_files(photos)
                await page.wait_for_timeout(3500)

        # 2. Title
        title_inp = await page.query_selector("label:has-text(\"Title\") input")
        if title_inp:
            await title_inp.fill(title)

        # 3. Price
        price_inp = await page.query_selector("label:has-text(\"Price\") input")
        if price_inp:
            await price_inp.fill(price)

        # 4. Category -> Bicycles
        cat_lbl = await page.query_selector("label:has-text(\"Category\")")
        if cat_lbl:
            await cat_lbl.click()
            await page.wait_for_timeout(1000)
            await page.click("span:text-is(\"Bicycles\"), div[role=\"button\"]:has-text(\"Bicycles\")")
            await page.wait_for_timeout(1500)

        # 5. Condition -> Used - Good
        cond_lbl = await page.query_selector("label:has-text(\"Condition\")")
        if cond_lbl:
            await cond_lbl.click()
            await page.wait_for_timeout(1000)
            await page.click("span:text-is(\"Used - Good\"), div[role=\"button\"]:has-text(\"Used - Good\")")
            await page.wait_for_timeout(1500)

        # 6. Bicycle type (optional)
        if b_type:
            type_lbl = await page.query_selector("label:has-text(\"Bicycle type\")")
            if type_lbl:
                try:
                    await type_lbl.click()
                    await page.wait_for_timeout(1000)
                    opt = await page.query_selector(f"span:text-is(\"{b_type}\"), div[role=\"button\"]:has-text(\"{b_type}\")")
                    if opt:
                        await opt.click()
                    else:
                        await page.keyboard.press("Escape")
                    await page.wait_for_timeout(1000)
                except:
                    pass

        # 7. Brand
        if brand:
            brand_inp = await page.query_selector("label:has-text(\"Brand\") input")
            if brand_inp:
                await brand_inp.fill(brand)

        # 8. Wheel size (optional)
        if w_size:
            wheel_lbl = await page.query_selector("label:has-text(\"Bicycle wheel size\")")
            if wheel_lbl:
                try:
                    await wheel_lbl.click()
                    await page.wait_for_timeout(1000)
                    opt_w = await page.query_selector(f"span:text-is(\"{w_size}\"), div[role=\"button\"]:has-text(\"{w_size}\")")
                    if opt_w:
                        await opt_w.click()
                    else:
                        await page.keyboard.type(w_size)
                        await page.keyboard.press("Enter")
                    await page.wait_for_timeout(1000)
                except:
                    pass

        # 9. Material (optional)
        if mat:
            mat_lbl = await page.query_selector("label:has-text(\"Material\")")
            if mat_lbl:
                try:
                    await mat_lbl.click()
                    await page.wait_for_timeout(1000)
                    opt_m = await page.query_selector(f"span:text-is(\"{mat}\"), div[role=\"button\"]:has-text(\"{mat}\")")
                    if opt_m:
                        await opt_m.click()
                    else:
                        await page.keyboard.type(mat)
                        await page.keyboard.press("Enter")
                    await page.wait_for_timeout(1000)
                except:
                    pass

        # 10. More details (expand description)
        desc_container = await page.query_selector("div[role=\"button\"][aria-expanded=\"false\"]:has-text(\"More details\")")
        if desc_container:
            await desc_container.click()
            await page.wait_for_timeout(1000)

        desc_lbl = await page.query_selector("label:has-text(\"Description\")")
        if desc_lbl:
            ta = await desc_lbl.query_selector("textarea")
            if ta:
                await ta.fill(desc)

        # Tags
        tags_lbl = await page.query_selector("label:has-text(\"Product tags\")")
        if tags_lbl:
            ta_tags = await tags_lbl.query_selector("textarea")
            if ta_tags:
                await ta_tags.fill(item.get("tags", "cykel, bike parts, dba"))
                await page.keyboard.press("Enter")

        # Door pickup
        door_el = await page.query_selector("div:has-text(\"Door pickup\"):not(:has-text(\"Meetup preferences\"))")
        if door_el:
            chk = await door_el.query_selector("input[type=\"checkbox\"], [role=\"checkbox\"]")
            is_checked = False
            if chk:
                aria_val = await chk.get_attribute("aria-checked")
                is_checked = (aria_val == "true")
            if not is_checked:
                await door_el.click()

        await page.wait_for_timeout(1500)

        # Click Next (Using evaluate/force to avoid disabled state timeout)
        logger.info(f"[{item_id}] FB: Clicking Next button...")
        clicked_next = await page.evaluate('''() => {
            const nextBtn = document.querySelector('div[aria-label="Next"]');
            if (nextBtn) {
                nextBtn.click();
                return true;
            }
            return false;
        }''')
        if not clicked_next:
            # Fallback
            next_btn = page.locator('div[aria-label="Next"]').first
            await next_btn.click(force=True)

        await page.wait_for_timeout(4000)

        # Step 2: Click Publish (Ensure no groups checked)
        logger.info(f"[{item_id}] FB: Clicking Publish on Step 2...")
        clicked_publish = await page.evaluate('''() => {
            const pubBtn = document.querySelector('div[aria-label="Publish"]') || Array.from(document.querySelectorAll('*')).find(e => e.innerText && e.innerText.trim() === 'Publish');
            if (pubBtn) {
                (pubBtn.closest('div[role="button"]') || pubBtn).click();
                return true;
            }
            return false;
        }''')
        if not clicked_publish:
            pub_btn = page.locator('div[aria-label="Publish"]').first
            await pub_btn.click(force=True)

        # Wait for publication to complete & redirect
        logger.info(f"[{item_id}] FB: Awaiting upload completion...")
        await page.wait_for_timeout(8000)

        # Navigate to /marketplace/you/selling to retrieve live item link
        logger.info(f"[{item_id}] FB: Fetching live link from inventory...")
        await page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(4500)

        # Look for the card div button matching the item title
        card = await page.query_selector(f"div[role=\"button\"][aria-label*=\"{title.split(' - ')[0]}\"]")
        if not card:
            first_word = title.split()[0]
            card = await page.query_selector(f"div[role=\"button\"][aria-label*=\"{first_word}\"]")

        fb_live_url = None
        if card:
            logger.info(f"[{item_id}] FB: Found listing card, clicking to open details...")
            await card.click()
            await page.wait_for_timeout(3000)
            
            links = await page.query_selector_all("a[href*=\"/marketplace/item/\"]")
            for l in links:
                href = await l.get_attribute("href")
                if href:
                    full = f"https://www.facebook.com{href}" if href.startswith("/") else href
                    fb_live_url = full.split("?")[0]
                    if "/marketplace/item/" in fb_live_url:
                        break

        if not fb_live_url:
            links = await page.query_selector_all("a[href*=\"/marketplace/item/\"]")
            for l in links:
                href = await l.get_attribute("href")
                if href:
                    full = f"https://www.facebook.com{href}" if href.startswith("/") else href
                    fb_live_url = full.split("?")[0]
                    break

        logger.info(f"[{item_id}] FB LIVE: {fb_live_url}")
        return fb_live_url

    except Exception as e:
        logger.error(f"[{item_id}] FB publish error: {e}")
        try:
            err_shot = f"/tmp/fb_err_{item_id}.png"
            await page.screenshot(path=err_shot)
            logger.info(f"Saved error screenshot to {err_shot}")
        except:
            pass
        return None
    finally:
        await page.close()

async def main():
    parser = argparse.ArgumentParser(description="Batch listing publisher")
    parser.add_argument("--item", help="Publish a single specific item ID")
    parser.add_argument("--limit", type=int, help="Limit number of items to publish")
    parser.add_argument("--skip-dba", action="store_true", help="Skip DBA publishing")
    parser.add_argument("--skip-fb", action="store_true", help="Skip Facebook publishing")
    args = parser.parse_args()

    if not CATALOG_FILE.exists():
        logger.error(f"Catalog file not found: {CATALOG_FILE}")
        return

    with open(CATALOG_FILE) as f:
        catalog = json.load(f)

    logger.info(f"Loaded catalog with {len(catalog)} items.")
    update_progress_report(catalog)

    items_to_process = []
    if args.item:
        if args.item in catalog:
            items_to_process.append((args.item, catalog[args.item]))
        else:
            logger.error(f"Item #{args.item} not found in catalog!")
            return
    else:
        for item_id, item in sorted(catalog.items()):
            meta_path = ITEMS_DIR / item_id / "metadata.json"
            dba_url = None
            fb_url = None
            if meta_path.exists():
                try:
                    with open(meta_path) as mf:
                        mdata = json.load(mf)
                        dba_url = mdata.get("dba_url")
                        fb_url = mdata.get("fb_url")
                except:
                    pass
            if dba_url and fb_url:
                continue
            items_to_process.append((item_id, item))

    if args.limit:
        items_to_process = items_to_process[:args.limit]

    logger.info(f"Queue size: {len(items_to_process)} items to process.")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROJECT_DIR / "browser_profile"),
            headless=True,
            viewport={"width": 1920, "height": 1080}
        )

        for item_id, item in items_to_process:
            meta_path = ITEMS_DIR / item_id / "metadata.json"
            dba_url = None
            fb_url = None
            if meta_path.exists():
                try:
                    with open(meta_path) as mf:
                        mdata = json.load(mf)
                        dba_url = mdata.get("dba_url")
                        fb_url = mdata.get("fb_url")
                except:
                    pass

            logger.info(f"\n==========================================")
            logger.info(f"Processing Item #{item_id}: {item['title']} ({item['price']} DKK)")
            logger.info(f"==========================================")

            # 1. DBA.dk
            if not args.skip_dba and not dba_url:
                dba_url = await publish_item_dba(context, item)
                if dba_url:
                    item["dba_url"] = dba_url
                    update_local_metadata(item_id, dba_url=dba_url)
                await asyncio.sleep(4)

            # 2. Facebook Marketplace
            if not args.skip_fb and not fb_url:
                fb_url = await publish_item_fb(context, item)
                if fb_url:
                    item["fb_url"] = fb_url
                    update_local_metadata(item_id, fb_url=fb_url)
                await asyncio.sleep(4)

            # 3. Notion Sync
            if dba_url or fb_url:
                update_notion_item(item_id, dba_url=dba_url, fb_url=fb_url)

            # 4. Update Progress Report
            update_progress_report(catalog)

            logger.info(f"Finished #{item_id}. Pacing delay 8s before next item...")
            await asyncio.sleep(8)

        await context.close()

    logger.info("Batch publishing run finished!")

if __name__ == "__main__":
    asyncio.run(main())
