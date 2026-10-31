#!/usr/bin/env python3
"""
notion_ssot.py
Single Source of Truth (SSoT) CLI & helper module for Hermes and Antigravity.

Synchronizes local inventory status with Notion.
Decoupled: uses NOTION_TOKEN and NOTION_DATABASE_ID from environment (.env).
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from registry.integrations.notion import NotionClient
from catalog.inventory import InventoryGraph

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("notion_ssot")


def main():
    parser = argparse.ArgumentParser(description="Notion SSoT CLI bridge.")
    parser.add_argument("--lookup", help="Look up item by Item ID (e.g. WRC05, WS01)")
    parser.add_argument("--list-active", action="store_true", help="List active listed items in Notion")
    parser.add_argument("--sold", help="Mark item as SOLD in Notion SSoT and trigger relational cascade")
    parser.add_argument("--set-price", nargs=2, metavar=("ITEM_ID", "PRICE"), help="Update asking price in DKK")

    args = parser.parse_args()
    client = NotionClient()

    if not client.is_configured:
        logger.warning("NOTION_TOKEN or NOTION_DATABASE_ID not configured in .env. Skipping Notion sync.")
        sys.exit(0)

    if args.lookup:
        page_id = client.find_page_by_item_id(args.lookup)
        if page_id:
            logger.info(f"Found Notion page for {args.lookup}: {page_id}")
        else:
            logger.warning(f"Item {args.lookup} not found in Notion.")

    elif args.list_active:
        items = client.fetch_all_items()
        logger.info(f"Retrieved {len(items)} items from Notion.")

    elif args.sold:
        item_id = args.sold.upper()
        logger.info(f"Marking {item_id} as SOLD in Notion SSoT...")
        client.mark_sold(item_id)

        # Relational cascade
        graph = InventoryGraph()
        related = graph.get_related_items_on_sold(item_id)
        if related:
            logger.info(f"Cascading sale to related items: {related}")
            for r in related:
                client.mark_sold(r)
                logger.info(f"  ↳ Marked {r} as SOLD")

    elif args.set_price:
        item_id, price = args.set_price
        logger.info(f"Updating price for {item_id} to {price} DKK...")
        client.update_price(item_id, float(price))


if __name__ == "__main__":
    main()
