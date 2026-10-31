#!/usr/bin/env python3
"""
inventory_relations.py
Defines bidirectional relationships between wheelsets and constituent wheels,
and handles cascading inventory status changes (mutual invalidation on sale).
"""

import json
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalog.inventory import InventoryGraph, DEFAULT_RELATIONS
from registry.integrations.notion import NotionClient

logger = logging.getLogger("inventory_relations")
logging.basicConfig(level=logging.INFO, format="%(message)s")

PROJECT_DIR = Path(__file__).resolve().parent
ITEMS_DIR = PROJECT_DIR / "items"

# Expose WHEELSET_MAP for backwards compatibility
WHEELSET_MAP = DEFAULT_RELATIONS
graph = InventoryGraph()
CHILD_TO_SET = graph.child_to_bundle


def get_item_urls(item_id):
    meta_path = ITEMS_DIR / item_id / "metadata.json"
    if meta_path.exists():
        try:
            with open(meta_path) as f:
                data = json.load(f)
                return {
                    "dba_url": data.get("dba_url"),
                    "fb_url": data.get("fb_url")
                }
        except Exception:
            pass
    return {"dba_url": None, "fb_url": None}


def generate_cross_links(item_id, platform="dba"):
    """
    Generates cross-linking text for DBA or Facebook listings:
    - If item is a wheelset: links to individual front & rear listings.
    - If item is an individual wheel: links to parent wheelset listing.
    """
    def resolve_url(target_id):
        if not target_id:
            return None
        urls = get_item_urls(target_id)
        return urls.get(f"{platform}_url")

    return graph.generate_cross_links(item_id, url_resolver_fn=resolve_url, platform=platform)


def cascade_sold(sold_item_id):
    """
    Executes cascading status update when an item is sold.
    - If constituent wheel sold -> marks parent wheelset SOLD.
    - If wheelset sold -> marks constituent wheels SOLD.
    """
    sold_item_id = sold_item_id.upper().strip()
    invalidated = graph.get_related_items_on_sold(sold_item_id)
    if not invalidated:
        logger.info(f"Item {sold_item_id} has no relational dependencies.")
        return []

    logger.info(f"Cascading sale for {sold_item_id}: marking {invalidated} as SOLD.")
    notion = NotionClient()
    if notion.is_configured:
        for item_id in invalidated:
            notion.mark_sold(item_id)

    return invalidated


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].upper()
        if cmd == "CASCADE" and len(sys.argv) > 2:
            cascade_sold(sys.argv[2])
        elif cmd == "CROSSLINKS" and len(sys.argv) > 2:
            print(generate_cross_links(sys.argv[2]))
        else:
            print("Usage: inventory_relations.py [CASCADE <item_id> | CROSSLINKS <item_id>]")
    else:
        print("Usage: inventory_relations.py [CASCADE <item_id> | CROSSLINKS <item_id>]")
