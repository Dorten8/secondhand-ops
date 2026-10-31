#!/usr/bin/env python3
"""registry.py — stateless read of the listing↔messaging seam. ZERO TOKENS.

The listing side owns `items.json` and writes it atomically (`os.replace`).
This module only ever READS it. That asymmetry is deliberate and is the whole
reason the two repos can be deployed, crash and be fixed independently
(see ARCHITECTURE.md §1 and §4).

CONTRACT (ARCHITECTURE.md §1)
  join key          (provider, item_id) — nothing else is stable
  statuses          draft / listed / reserved / sold / withdrawn
  unknown status    treat as `listed`  (fail open on messaging, fail closed on
                    selling — a wrong reply is recoverable, a wrong sale is not)

STATELESS BY DESIGN
  Every call re-reads the file. No cache, no mtime bookkeeping, no lock.
  A poll that crashes mid-tick cannot corrupt anything: there is nothing to
  corrupt. The file is replaced atomically by the writer, so a reader sees
  either the old bytes or the new bytes, never a half-written row.

WHAT WE SKIP, AND WHY
  item_id is null   the ad is not published yet; there is no key to join on, so
                    replying would mean guessing which item a buyer means.
  status=draft      not live — must never be polled or replied to.
  status=sold       the sale is done. A "still available?" reply after a sale is
  status=withdrawn  the single worst message in the design, hence the hard stop.

EXIT CONTRACT (as a CLI)
  registry readable -> one-line summary per actionable item, exit 0
  registry missing  -> prints nothing, exit 0   (messaging may start first)
  registry malformed-> one line to stderr, exit 2 (do not spam, do not guess)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Iterable

DEFAULT_REGISTRY = "/opt/data/marketplace/items.json"

#: Statuses where messaging must keep handling the item.
ACTIONABLE = {"listed", "reserved"}
#: Statuses where messaging must stop queueing anything new.
CLOSED = {"sold", "withdrawn"}
#: Not published yet — never poll.
UNPUBLISHED = {"draft"}


class RegistryMalformed(RuntimeError):
    """The registry exists but cannot be trusted. Caller must not guess."""


@dataclass(frozen=True)
class Item:
    provider: str
    item_id: str
    title: str = ""
    price_dkk: float | None = None
    status: str = "listed"
    ad_url: str = ""
    photo_count: int = 0
    created_at: str = ""
    status_changed_at: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.item_id)

    @property
    def actionable(self) -> bool:
        """True when a buyer message about this item may still be handled."""
        return self.status in ACTIONABLE

    @property
    def closed(self) -> bool:
        """True when the sale is over — stop queueing, close open drafts."""
        return self.status in CLOSED


def registry_path() -> str:
    return os.environ.get("MARKETPLACE_REGISTRY", DEFAULT_REGISTRY)


def load(path: str | None = None) -> list[Item]:
    """Read and normalise the registry. Missing file is not an error.

    Raises RegistryMalformed when the file exists but is not a JSON list of
    objects — silently returning [] there would look identical to "no items"
    and hide a broken writer.
    """
    p = path or registry_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        # Listing may not have run yet. Messaging is allowed to start first.
        return []
    except json.JSONDecodeError as exc:
        raise RegistryMalformed(f"{p}: not valid JSON ({exc})") from exc

    if not isinstance(raw, list):
        raise RegistryMalformed(f"{p}: expected a JSON list, got {type(raw).__name__}")

    items: list[Item] = []
    for n, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            raise RegistryMalformed(f"{p}: row {n} is {type(row).__name__}, expected object")

        provider = str(row.get("provider") or "").strip()
        item_id = row.get("item_id")
        # Unpublished: no join key yet. Skip rather than invent one.
        if item_id is None or str(item_id).strip() == "":
            continue
        if not provider:
            raise RegistryMalformed(f"{p}: row {n} has item_id but no provider")

        status = str(row.get("status") or "").strip().lower()
        known = ACTIONABLE | CLOSED | UNPUBLISHED
        if status not in known:
            # ARCHITECTURE.md §1: unknown status fails open on messaging.
            status = "listed"

        items.append(
            Item(
                provider=provider,
                item_id=str(item_id).strip(),
                title=str(row.get("title") or ""),
                price_dkk=_as_float(row.get("price_dkk")),
                status=status,
                ad_url=str(row.get("ad_url") or ""),
                photo_count=_as_int(row.get("photo_count")),
                created_at=str(row.get("created_at") or ""),
                status_changed_at=str(row.get("status_changed_at") or ""),
            )
        )
    return items


def by_key(items: Iterable[Item]) -> dict[tuple[str, str], Item]:
    return {it.key: it for it in items}


def get(provider: str, item_id: str, path: str | None = None) -> Item | None:
    """Look one item up by the join key. None means not found or unpublished."""
    return by_key(load(path)).get((provider, str(item_id)))


def actionable(path: str | None = None) -> list[Item]:
    """Items a buyer message may still be handled for."""
    return [it for it in load(path) if it.actionable]


def _as_float(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v) -> int:
    try:
        return 0 if v is None else int(v)
    except (TypeError, ValueError):
        return 0


def main(argv: list[str]) -> int:
    p = registry_path()
    try:
        items = load(p)
    except RegistryMalformed as exc:
        print(f"registry: {exc}", file=sys.stderr)
        return 2

    if not items:
        # Silent: listing has not published yet, or nothing is open.
        return 0

    for it in items:
        flag = "OK  " if it.actionable else "SKIP"
        print(f"{flag} {it.provider}/{it.item_id}  {it.status:<9} {it.title[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
