"""
inventory.py — Relational inventory management and cascading status updates.

Handles bidirectional relationships between bundles (e.g., wheelsets) and
constituent items (e.g., front/rear wheels), and enforces mutual invalidation:
- When a constituent item is sold -> the parent bundle is marked sold/withdrawn.
- When a bundle is sold -> all constituent items are marked sold/withdrawn.
- Dynamic cross-link generator for listing descriptions.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("inventory")

# Default bidirectional wheelset mappings (can be overridden via config)
DEFAULT_RELATIONS = {
    "WS01": {
        "front": "WF03",
        "rear": "WRC06",
        "name": '29" MTB Disc Wheelset (Busetto Grande)'
    },
    "WS02": {
        "front": "WF04",
        "rear": "WRC04",
        "name": '26" MTB Disc Wheelset (Alexrims XD 2500)'
    },
    "WS03": {
        "front": "WF01",
        "rear": "WRS04",
        "name": '28" E-Bike 36V / Nexus 7 Wheelset (Ryde Zac 421)'
    },
    "WS04": {
        "front": "WF07",
        "rear": "WRC01",
        "name": '28" City Commuter 7-Speed Wheelset (Shimano)'
    }
}


class InventoryGraph:
    """Manages bundle/component dependencies and cascading state invalidation."""

    def __init__(self, relations: Optional[Dict[str, Dict[str, str]]] = None):
        self.relations = relations or DEFAULT_RELATIONS
        self.child_to_bundle: Dict[str, Dict[str, str]] = {}
        self._build_reverse_index()

    def _build_reverse_index(self):
        self.child_to_bundle.clear()
        for bundle_id, info in self.relations.items():
            if "front" in info:
                self.child_to_bundle[info["front"]] = {
                    "bundle_id": bundle_id,
                    "position": "front",
                    "sibling": info.get("rear", "")
                }
            if "rear" in info:
                self.child_to_bundle[info["rear"]] = {
                    "bundle_id": bundle_id,
                    "position": "rear",
                    "sibling": info.get("front", "")
                }

    def get_related_items_on_sold(self, item_id: str) -> List[str]:
        """
        Returns all item IDs that must be invalidated when `item_id` is sold.
        - If bundle sells -> returns constituent components.
        - If component sells -> returns parent bundle.
        """
        item_id = item_id.upper().strip()
        invalidated = []

        if item_id in self.relations:
            # Bundle sold -> invalidate constituent parts
            info = self.relations[item_id]
            if "front" in info and info["front"]:
                invalidated.append(info["front"])
            if "rear" in info and info["rear"]:
                invalidated.append(info["rear"])
        elif item_id in self.child_to_bundle:
            # Constituent sold -> invalidate parent bundle
            bundle_id = self.child_to_bundle[item_id]["bundle_id"]
            invalidated.append(bundle_id)

        return invalidated

    def generate_cross_links(
        self,
        item_id: str,
        url_resolver_fn=None,
        platform: str = "dba"
    ) -> str:
        """
        Generates cross-linking markdown text for listings.
        - If item is a bundle: links to constituent listings.
        - If item is a component: links to parent bundle listing.
        """
        item_id = item_id.upper().strip()
        resolver = url_resolver_fn or (lambda i: None)

        if item_id in self.relations:
            info = self.relations[item_id]
            f_id = info.get("front")
            r_id = info.get("rear")
            f_url = resolver(f_id)
            r_url = resolver(r_id)

            lines = ["\nSælges også separat:"]
            if f_url:
                lines.append(f"• Forhjul (#{f_id}): {f_url}")
            else:
                lines.append(f"• Forhjul (#{f_id}) kan købes separat")
            if r_url:
                lines.append(f"• Baghjul (#{r_id}): {r_url}")
            else:
                lines.append(f"• Baghjul (#{r_id}) kan købes separat")
            return "\n".join(lines)

        elif item_id in self.child_to_bundle:
            parent_info = self.child_to_bundle[item_id]
            set_id = parent_info["bundle_id"]
            sibling_id = parent_info["sibling"]
            set_url = resolver(set_id)
            bundle_name = self.relations.get(set_id, {}).get("name", "Hjulsæt")

            lines = ["\nDel af matchende sæt:"]
            if set_url:
                lines.append(f"• Komplet hjulsæt (#{set_id}): {set_url}")
            else:
                lines.append(f"• Kan også købes samlet som hjulsæt #{set_id} ({bundle_name})")
            if sibling_id:
                sib_url = resolver(sibling_id)
                if sib_url:
                    lines.append(f"• Matchende modstykke (#{sibling_id}): {sib_url}")
            return "\n".join(lines)

        return ""
