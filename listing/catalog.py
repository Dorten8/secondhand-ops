"""
catalog.py — Catalog generation and bilingual listing builder.

Strictly enforces:
1. Title standard: WHAT - WHATTYPE - MAKE (e.g., 'Wheelset / Hjulsæt - 29", MTB Disc (9-Speed) - Busetto Grande')
2. Bilingual description: (English below) -> Danish -> '---' -> English -> Configurable Pickup Terms
3. Specifications extraction and bundle calculations.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CatalogEntry:
    item_id: str
    title: str
    price_dkk: float
    description_dk: str
    description_en: str
    specs: List[str] = field(default_factory=list)
    photos: List[str] = field(default_factory=list)
    category: Optional[str] = None
    dba_url: Optional[str] = None
    fb_url: Optional[str] = None

    def build_full_listing(
        self,
        postal_area: str = "2300 København S",
        payment_methods: str = "MobilePay",
        cross_links_text: str = ""
    ) -> str:
        """Assembles a full bilingual listing description ready for marketplace publication."""
        lines = [
            "(English below)\n",
            self.description_dk.strip(),
            "\nSpecifikationer:"
        ]
        lines.append(f"• Vare-ID: #{self.item_id}")
        for s in self.specs:
            lines.append(f"• {s}")

        if cross_links_text:
            lines.append(cross_links_text.strip())

        lines.extend([
            "\nAfhentning og betaling:",
            f"• Sendes ikke – kun personlig afhentning i {postal_area}.",
            f"• {payment_methods} modtages.",
            "\n---",
            f"\n{self.description_en.strip()}",
            "\nSpecifications:"
        ])
        lines.append(f"• Item ID: #{self.item_id}")
        for s in self.specs:
            lines.append(f"• {s}")

        lines.extend([
            "\nPickup & Payment:",
            f"• No shipping – personal pickup only in {postal_area}.",
            f"• {payment_methods} accepted."
        ])

        return "\n".join(lines)


def format_title(what: str, what_type: str, make: str) -> str:
    """Enforces title standard: WHAT - WHATTYPE - MAKE"""
    return f"{what.strip()} - {what_type.strip()} - {make.strip()}"
