"""
notion.py — Optional decoupled Notion SSoT Integration.

Synchronizes item states between the local WAL registry and a private Notion database.
- Completely decoupled: credentials and Database ID are loaded strictly from environment variables.
- Self-healing & graceful degradation: if NOTION_TOKEN or NOTION_DATABASE_ID are unset,
  operations log an info message and return gracefully without crashing.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("notion_sync")


class NotionClient:
    def __init__(self, token: Optional[str] = None, database_id: Optional[str] = None):
        self.token = token or os.getenv("NOTION_TOKEN")
        self.database_id = database_id or os.getenv("NOTION_DATABASE_ID")

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.database_id)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    def fetch_all_items(self) -> List[Dict[str, Any]]:
        """Fetches all pages from the configured Notion database."""
        if not self.is_configured:
            logger.info("Notion credentials not configured; skipping Notion sync.")
            return []

        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        results = []
        has_more = True
        next_cursor = None

        while has_more:
            body: Dict[str, Any] = {"page_size": 100}
            if next_cursor:
                body["start_cursor"] = next_cursor

            try:
                resp = requests.post(url, headers=self._headers(), json=body, timeout=15)
                if resp.status_code != 200:
                    logger.error(f"Notion API error: {resp.status_code} - {resp.text}")
                    break
                data = resp.json()
                results.extend(data.get("results", []))
                has_more = data.get("has_more", False)
                next_cursor = data.get("next_cursor")
            except Exception as e:
                logger.error(f"Failed to query Notion database: {e}")
                break

        return results

    def find_page_by_item_id(self, item_id: str) -> Optional[str]:
        """Finds page ID for a given Item ID (#WS01, WRC05, etc.)."""
        if not self.is_configured:
            return None

        clean_id = item_id.upper().strip().lstrip("#")
        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        query = {
            "filter": {
                "or": [
                    {"property": "Item ID", "title": {"contains": clean_id}},
                    {"property": "Item ID", "title": {"contains": f"#{clean_id}"}},
                ]
            }
        }
        try:
            resp = requests.post(url, headers=self._headers(), json=query, timeout=10)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    return results[0]["id"]
        except Exception as e:
            logger.error(f"Failed to look up Notion item {clean_id}: {e}")

        return None

    def mark_sold(self, item_id: str) -> bool:
        """Sets Status to 'SOLD' in Notion for the specified item."""
        if not self.is_configured:
            logger.info(f"Notion not configured; marked {item_id} sold locally only.")
            return True

        page_id = self.find_page_by_item_id(item_id)
        if not page_id:
            logger.warning(f"Could not find Notion page for item {item_id}")
            return False

        url = f"https://api.notion.com/v1/pages/{page_id}"
        payload = {
            "properties": {
                "Status": {
                    "select": {"name": "SOLD"}
                }
            }
        }
        try:
            resp = requests.patch(url, headers=self._headers(), json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to update Notion status for {item_id}: {e}")
            return False

    def update_price(self, item_id: str, new_price_dkk: float) -> bool:
        """Updates Asking Price (DKK) in Notion."""
        if not self.is_configured:
            return True

        page_id = self.find_page_by_item_id(item_id)
        if not page_id:
            return False

        url = f"https://api.notion.com/v1/pages/{page_id}"
        payload = {
            "properties": {
                "Asking Price (DKK)": {"number": float(new_price_dkk)}
            }
        }
        try:
            resp = requests.patch(url, headers=self._headers(), json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to update price for {item_id}: {e}")
            return False
