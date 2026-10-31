#!/usr/bin/env python3
"""providers/base.py — the contract every marketplace must satisfy.

The whole point of this file is that `fb.py` can be written later without
touching poll.py, cli.py, the store, or the agent. A provider only translates
between one marketplace and these six methods.

A provider must NOT:
  * decide whether to reply               (that is the drafting stage)
  * talk to Telegram                      (that is notify.py)
  * keep state in memory across calls      (that is mstore.py)

It may assume it is called from a short-lived process.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Conversation:
    """One thread, normalised. `raw` keeps the provider payload for debugging."""
    conv_id: str
    item_id: str = ""
    item_title: str = ""
    partner_name: str = ""
    partner_id: str = ""
    last_preview: str = ""
    last_outgoing: bool = False      # True = WE spoke last, ball is with them
    unseen: int = 0
    sold: bool = False
    last_at: str = ""                # provider's lastMessageDate, ISO-ish
    raw: dict = field(default_factory=dict)

    def ball_is_with_us(self) -> bool:
        """A thread needs a reply when they spoke last and the item is not sold."""
        return (not self.last_outgoing) and (not self.sold)


@dataclass
class Message:
    body: str
    direction: str                   # 'in' | 'out'
    at: str = ""


class ProviderError(RuntimeError):
    pass


class AuthError(ProviderError):
    """Session expired or missing. The caller must NOT retry blindly — it has to
    re-authenticate a human. Never auto-login any marketplace."""


class Provider:
    """Subclass and implement all six. `name` is the store key."""

    name: str = "base"

    # --- discovery -------------------------------------------------------
    def list_conversations(self) -> list[Conversation]:
        """Every thread the account has, newest first. Must be cheap: this is
        the hot path called on a timer."""
        raise NotImplementedError

    def fetch_thread(self, conv_id: str) -> list[Message]:
        """Full transcript, oldest first. May be expensive; call it only for
        threads that actually need attention."""
        raise NotImplementedError

    # --- action ----------------------------------------------------------
    def send(self, conv_id: str, body: str) -> dict:
        """Send `body` into `conv_id`. MUST verify delivery and raise on doubt.

        Returning {"ok": True} without proof is the single worst thing a
        provider can do — the caller will mark a message sent that never left.
        """
        raise NotImplementedError

    # --- identity --------------------------------------------------------
    def whoami(self) -> dict:
        """Who we are on this marketplace (id/name), for audit and sanity checks."""
        raise NotImplementedError

    def auth_ok(self) -> bool:
        """Cheap liveness check of the stored credentials."""
        try:
            self.whoami()
            return True
        except Exception:
            return False
