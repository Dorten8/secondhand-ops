"""
dba.py — DBA.dk listing provider and API reference.

Includes:
1. Ad validation rules specific to DBA.dk.
2. Active ad synchronization via DBA's REST API.
3. Complete specification & helper for DBA's authenticated message send endpoint.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from .base import BaseListingProvider

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


class DbaListingProvider(BaseListingProvider):
    @property
    def name(self) -> str:
        return "dba"

    def validate_ad_spec(self, spec: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        title = spec.get("title", "").strip()
        if not title:
            return False, "Title cannot be empty."
        if len(title) < 5:
            return False, "Title must be at least 5 characters."

        try:
            price = float(spec.get("price_dkk", 0.0))
            if price < 0:
                return False, "Price cannot be negative."
        except (ValueError, TypeError):
            return False, "Invalid price."

        return True, None

    def sync_active_ads(self, cookies: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Fetch active listings from DBA messages conversationgroups.
        Each group represents an ad (itemInfo) with active or recent buyer interest.
        """
        login_id = resolve_login_id(cookies)
        if not login_id:
            raise RuntimeError("Could not resolve loginId from DBA session.")

        url = f"https://www.dba.dk/messages/api/conversations/users/{login_id}/conversationgroups"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.dba.dk/messages",
        }
        r = requests.get(url, cookies=cookies, headers=headers, timeout=12)
        if r.status_code != 200:
            raise RuntimeError(f"Failed to fetch conversation groups: HTTP {r.status_code}")

        groups = r.json()
        seen_items = {}
        for g in groups:
            basis = g.get("groupBasis", {})
            item_info = basis.get("itemInfo")
            if not item_info:
                continue
            item_id = str(item_info.get("itemId"))
            if item_id and item_id not in seen_items:
                seen_items[item_id] = {
                    "provider": "dba",
                    "item_id": item_id,
                    "title": item_info.get("title", ""),
                    "price_dkk": float(item_info.get("price", 0.0) or 0.0),
                    "status": "listed",
                    "ad_url": item_info.get("itemUrl") or f"https://www.dba.dk/my-items/details/{item_id}",
                    "photo_count": 1 if item_info.get("imageUrl") else 0,
                }
        return list(seen_items.values())


# ===========================================================================
# DBA REST API SPECIFICATION & SEND HELPER (SHARED WITH MESSAGING)
# ===========================================================================

def resolve_login_id(cookies: Dict[str, str]) -> Optional[int]:
    """Extract numeric loginId from https://www.dba.dk/messages page config."""
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get("https://www.dba.dk/messages", cookies=cookies, headers=headers, timeout=10)
    if r.status_code != 200:
        return None

    # Primary: Base64 encoded JSON in data-config attribute
    m = re.search(r"data-config=[\x27\x22]([^\x27\x22]+)[\x27\x22]", r.text)
    if m:
        try:
            cfg = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
            if "loginId" in cfg:
                return int(cfg["loginId"])
        except Exception:
            pass

    # Fallback: Meta tracking tag
    m_id = re.search(r"name=[\x27\x22]nmp:tracking:login-id[\x27\x22]\s+content=[\x27\x22](\d+)[\x27\x22]", r.text)
    if m_id:
        return int(m_id.group(1))

    return None


def send_dba_message_post(
    login_id: int,
    conversation_id: str,
    item_id: str,
    body: str,
    cookies: Dict[str, str],
    timeout: float = 12.0,
) -> Tuple[bool, int, Dict[str, Any], str]:
    """
    Direct authenticated HTTP POST to DBA messaging endpoint.

    Endpoint:
      POST https://www.dba.dk/messages/api/conversations/users/{loginId}/conversations/{conversationId}/messages

    Returns:
      (success, http_status_code, response_json, client_message_id)
    """
    client_message_id = str(uuid.uuid4())
    sent_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

    payload = {
        "clientMessageId": client_message_id,
        "body": body,
        "messageType": "textMessage",
        "sent": sent_timestamp,
        "attachments": [],
    }

    url = f"https://www.dba.dk/messages/api/conversations/users/{login_id}/conversations/{conversation_id}/messages"
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": f"https://www.dba.dk/messages/group/ad/{item_id}/id/{conversation_id}",
        "Origin": "https://www.dba.dk",
    }

    resp = requests.post(url, json=payload, cookies=cookies, headers=headers, timeout=timeout)
    try:
        data = resp.json()
    except Exception:
        data = {"raw_text": resp.text[:400]}

    return (resp.status_code in (200, 201), resp.status_code, data, client_message_id)
