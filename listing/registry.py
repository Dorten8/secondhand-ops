"""
registry.py — Shared item registry storage for marketplace listings.

Implements the single-table seam defined in ARCHITECTURE.md §1:
- Storage: Single atomic JSON file at /opt/data/marketplace/items.json (configurable via MARKETPLACE_REGISTRY_PATH)
- Join key: (provider, item_id)
- Statuses: draft, listed, reserved, sold, withdrawn
- Concurrency: Atomic writes via write-to-temp + os.replace ensures readers
  (e.g. the messaging daemon) never observe locked, partial, or corrupt data.
- Self-Healing: Automatically falls back to local directory if system path is not writable.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("registry")

DEFAULT_REGISTRY_PATH = Path("/opt/data/marketplace/items.json")
FALLBACK_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "items.json"

VALID_PROVIDERS = {"dba", "facebook"}
VALID_STATUSES = {"draft", "listed", "reserved", "sold", "withdrawn"}


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Item:
    provider: str
    item_id: Optional[str]  # None for un-published drafts
    title: str
    price_dkk: float
    status: str
    ad_url: Optional[str] = None
    photo_count: int = 0
    created_at: str = ""
    status_changed_at: str = ""
    local_draft_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.provider not in VALID_PROVIDERS:
            raise ValueError(f"Invalid provider: {self.provider}. Must be one of {sorted(VALID_PROVIDERS)}")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {self.status}. Must be one of {sorted(VALID_STATUSES)}")
        if not self.created_at:
            self.created_at = utc_now_iso()
        if not self.status_changed_at:
            self.status_changed_at = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.metadata is None:
            d.pop("metadata", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Item:
        return cls(
            provider=data["provider"],
            item_id=data.get("item_id"),
            title=data.get("title", "Untitled"),
            price_dkk=float(data.get("price_dkk", 0.0)),
            status=data.get("status", "draft"),
            ad_url=data.get("ad_url"),
            photo_count=int(data.get("photo_count", 0)),
            created_at=data.get("created_at", utc_now_iso()),
            status_changed_at=data.get("status_changed_at", utc_now_iso()),
            local_draft_id=data.get("local_draft_id"),
            metadata=data.get("metadata"),
        )


class ItemRegistry:
    def __init__(self, path: Optional[Path | str] = None):
        if path:
            self.path = Path(path)
        else:
            env_path = os.getenv("MARKETPLACE_REGISTRY_PATH")
            self.path = Path(env_path) if env_path else DEFAULT_REGISTRY_PATH

        self._ensure_writable_path()

    def _ensure_writable_path(self) -> None:
        """Self-healing path validation: falls back to local workspace if target is unwritable."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Test write access
            test_file = self.path.parent / ".write_test"
            test_file.touch(exist_ok=True)
            test_file.unlink()
        except (PermissionError, OSError) as e:
            logger.warning(
                f"Configured registry path {self.path} is not writable ({e}). "
                f"Falling back to local path: {FALLBACK_REGISTRY_PATH}"
            )
            self.path = FALLBACK_REGISTRY_PATH
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> List[Item]:
        """Read all items from the registry file. Returns empty list if missing."""
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                raw_list = json.loads(content)
                if not isinstance(raw_list, list):
                    return []
                return [Item.from_dict(item) for item in raw_list]
        except Exception as e:
            logger.error(f"Error loading registry from {self.path}: {e}")
            return []

    def save_all(self, items: List[Item]) -> None:
        """
        Atomic WAL write: writes to temporary file in the same directory,
        then atomically replaces target via os.replace.
        """
        self._ensure_writable_path()
        tmp_path = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        try:
            payload = [i.to_dict() for i in items]
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
            # Ensure proper group/world read permissions if running on shared hosts
            try:
                os.chmod(self.path, 0o664)
            except OSError:
                pass
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def get_by_item_id(self, provider: str, item_id: str) -> Optional[Item]:
        items = self.load_all()
        for i in items:
            if i.provider == provider and i.item_id == item_id:
                return i
        return None

    def get_by_draft_id(self, local_draft_id: str) -> Optional[Item]:
        items = self.load_all()
        for i in items:
            if i.local_draft_id == local_draft_id:
                return i
        return None

    def upsert(self, item: Item) -> None:
        items = self.load_all()
        updated = False
        now = utc_now_iso()

        for idx, existing in enumerate(items):
            # Match by (provider, item_id) if item_id exists
            if item.item_id and existing.provider == item.provider and existing.item_id == item.item_id:
                if existing.status != item.status:
                    item.status_changed_at = now
                else:
                    item.status_changed_at = existing.status_changed_at
                item.created_at = existing.created_at
                items[idx] = item
                updated = True
                break
            # Match by local_draft_id for drafts
            elif item.local_draft_id and existing.local_draft_id == item.local_draft_id:
                if existing.status != item.status:
                    item.status_changed_at = now
                else:
                    item.status_changed_at = existing.status_changed_at
                item.created_at = existing.created_at
                items[idx] = item
                updated = True
                break

        if not updated:
            items.append(item)

        self.save_all(items)

    def set_status(self, provider: str, item_id: str, new_status: str) -> Optional[Item]:
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {new_status}. Must be one of {sorted(VALID_STATUSES)}")
        items = self.load_all()
        target = None
        for i in items:
            if i.provider == provider and i.item_id == item_id:
                if i.status != new_status:
                    i.status = new_status
                    i.status_changed_at = utc_now_iso()
                target = i
                break
        if target:
            self.save_all(items)
        return target
