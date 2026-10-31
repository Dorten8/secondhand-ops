#!/usr/bin/env python3
"""
listctl.py — Command-line controller for marketplace listing lifecycle.

Manages the item registry seam defined in ARCHITECTURE.md:
- add: Register an item draft or existing listing
- publish: Transition draft -> listed with marketplace item_id and URL
- status: Update item status (e.g. mark sold, reserved, withdrawn)
- sold: Mark item as sold and trigger cascading invalidation of related bundles/components
- ls: List registered items and current states
- sync-dba: Ingest active ads from DBA into the registry
- sync-notion: Optional sync with private Notion database (if configured)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

# src/ is the package root: registry/, catalog/ and publishing/ are siblings.
SRC = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from registry.registry import Item, ItemRegistry, VALID_PROVIDERS, VALID_STATUSES
from catalog.inventory import InventoryGraph
from registry.integrations.notion import NotionClient
from publishing.providers.dba import DbaListingProvider


def cmd_add(args, registry: ItemRegistry):
    draft_id = str(uuid.uuid4())[:8]
    item = Item(
        provider=args.provider,
        item_id=args.item_id,
        title=args.title,
        price_dkk=args.price,
        status="listed" if args.item_id else "draft",
        ad_url=args.url,
        photo_count=args.photos or 0,
        local_draft_id=draft_id if not args.item_id else None,
    )
    registry.upsert(item)
    print(f"Added item: [{item.provider}] {item.title} — status={item.status} (id={item.item_id or 'draft:' + item.local_draft_id})")


def cmd_publish(args, registry: ItemRegistry):
    item = None
    if args.draft_id:
        item = registry.get_by_draft_id(args.draft_id)
    elif args.item_id:
        item = registry.get_by_item_id(args.provider, args.item_id)

    if not item:
        print(f"Error: item not found for draft_id={args.draft_id} / item_id={args.item_id}", file=sys.stderr)
        sys.exit(1)

    item.item_id = str(args.marketplace_id)
    item.status = "listed"
    item.ad_url = args.url or item.ad_url
    registry.upsert(item)
    print(f"Published item: [{item.provider}] {item.title} -> status=listed (item_id={item.item_id})")


def cmd_status(args, registry: ItemRegistry):
    target = registry.set_status(args.provider, args.item_id, args.status)
    if not target:
        print(f"Error: item not found for provider={args.provider} item_id={args.item_id}", file=sys.stderr)
        sys.exit(1)
    print(f"Status updated: [{target.provider}] {target.item_id} -> {target.status} at {target.status_changed_at}")


def cmd_sold(args, registry: ItemRegistry):
    item_id = args.item_id.strip()
    provider = args.provider

    target = registry.set_status(provider, item_id, "sold")
    if target:
        print(f"✅ Marked sold in registry: [{provider}] {item_id} ({target.title})")
    else:
        print(f"ℹ️ Item {item_id} not currently in registry; checking relational inventory...")

    # Relational cascading
    graph = InventoryGraph()
    related = graph.get_related_items_on_sold(item_id)
    if related:
        print(f"🔄 Relational cascade triggered. Related items to invalidate: {related}")
        for rel_id in related:
            rel_target = registry.set_status(provider, rel_id, "sold")
            if rel_target:
                print(f"   ↳ Invalidate [{provider}] {rel_id}: marked SOLD")
            else:
                print(f"   ↳ Invalidate [{provider}] {rel_id}: (not registered in {provider})")

    # Optional Notion Sync
    notion = NotionClient()
    if notion.is_configured:
        print("📡 Syncing sale to Notion SSoT...")
        notion.mark_sold(item_id)
        for rel_id in related:
            notion.mark_sold(rel_id)
        print("✅ Notion SSoT updated.")


def cmd_ls(args, registry: ItemRegistry):
    items = registry.load_all()
    if args.provider:
        items = [i for i in items if i.provider == args.provider]
    if args.status:
        items = [i for i in items if i.status == args.status]

    if args.json:
        print(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False))
        return

    print(f"{'PROVIDER':<10} {'ITEM ID':<14} {'PRICE':<8} {'STATUS':<11} {'CHANGED AT':<20} {'TITLE'}")
    print("-" * 85)
    for i in items:
        iid = i.item_id or (f"draft:{i.local_draft_id}" if i.local_draft_id else "-")
        ts = i.status_changed_at[:19] if i.status_changed_at else ""
        print(f"{i.provider:<10} {iid:<14} {int(i.price_dkk):<8} {i.status:<11} {ts:<20} {i.title[:30]}")


def cmd_sync_dba(args, registry: ItemRegistry):
    cookie_path = os.path.expanduser(args.cookies or "~/.dba_cookies.json")
    if not os.path.exists(cookie_path):
        print(f"Error: cookie file {cookie_path} not found.", file=sys.stderr)
        sys.exit(1)

    with open(cookie_path) as f:
        cookies = json.load(f)

    provider = DbaListingProvider()
    ads = provider.sync_active_ads(cookies)
    count = 0
    for ad in ads:
        item = Item.from_dict(ad)
        registry.upsert(item)
        count += 1
    print(f"Successfully synced {count} active ads from DBA into {registry.path}")


def cmd_sync_notion(args, registry: ItemRegistry):
    notion = NotionClient()
    if not notion.is_configured:
        print("Error: NOTION_TOKEN or NOTION_DATABASE_ID environment variables are not set.", file=sys.stderr)
        print("Configure them in .env or run without Notion.", file=sys.stderr)
        sys.exit(1)

    print("Fetching active items from Notion...")
    pages = notion.fetch_all_items()
    print(f"Retrieved {len(pages)} items from Notion SSoT.")


def main():
    p = argparse.ArgumentParser(description="Marketplace listing and item registry controller.")
    p.add_argument("--registry-file", default=None, help="Custom registry path (default: /opt/data/marketplace/items.json)")
    sub = p.add_subparsers(dest="cmd", required=True)

    # add
    s_add = sub.add_parser("add", help="Add an item draft or existing listing")
    s_add.add_argument("--provider", choices=sorted(VALID_PROVIDERS), required=True)
    s_add.add_argument("--title", required=True)
    s_add.add_argument("--price", type=float, required=True)
    s_add.add_argument("--item-id", help="Marketplace item ID (leave empty if draft)")
    s_add.add_argument("--url", help="Ad URL")
    s_add.add_argument("--photos", type=int, default=0)

    # publish
    s_pub = sub.add_parser("publish", help="Bind marketplace item ID upon publish")
    s_pub.add_argument("--provider", choices=sorted(VALID_PROVIDERS), default="dba")
    s_pub.add_argument("--draft-id", help="Local draft UUID")
    s_pub.add_argument("--item-id", help="Existing local ID")
    s_pub.add_argument("--marketplace-id", required=True, help="New marketplace item ID")
    s_pub.add_argument("--url", help="Live ad URL")

    # status
    s_st = sub.add_parser("status", help="Update item status")
    s_st.add_argument("--provider", choices=sorted(VALID_PROVIDERS), required=True)
    s_st.add_argument("--item-id", required=True)
    s_st.add_argument("--status", choices=sorted(VALID_STATUSES), required=True)

    # sold
    s_sold = sub.add_parser("sold", help="Mark item sold with automatic relational cascade")
    s_sold.add_argument("--provider", choices=sorted(VALID_PROVIDERS), default="dba")
    s_sold.add_argument("item_id", help="Item ID or Marketplace ID to mark sold")

    # ls
    s_ls = sub.add_parser("ls", help="List items in registry")
    s_ls.add_argument("--provider", choices=sorted(VALID_PROVIDERS))
    s_ls.add_argument("--status", choices=sorted(VALID_STATUSES))
    s_ls.add_argument("--json", action="store_true")

    # sync-dba
    s_sync = sub.add_parser("sync-dba", help="Sync active ads from DBA API")
    s_sync.add_argument("--cookies", help="Path to DBA cookies JSON")

    # sync-notion
    sub.add_parser("sync-notion", help="Sync active items from Notion SSoT")

    args = p.parse_args()
    reg = ItemRegistry(args.registry_file) if args.registry_file else ItemRegistry()

    if args.cmd == "add":
        cmd_add(args, reg)
    elif args.cmd == "publish":
        cmd_publish(args, reg)
    elif args.cmd == "status":
        cmd_status(args, reg)
    elif args.cmd == "sold":
        cmd_sold(args, reg)
    elif args.cmd == "ls":
        cmd_ls(args, reg)
    elif args.cmd == "sync-dba":
        cmd_sync_dba(args, reg)
    elif args.cmd == "sync-notion":
        cmd_sync_notion(args, reg)


if __name__ == "__main__":
    main()
