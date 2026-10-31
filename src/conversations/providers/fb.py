#!/usr/bin/env python3
"""providers/fb.py — Facebook Marketplace provider (STUB, not implemented).

This file exists to prove the interface is honest. Everything it must do is
already written down; nothing about the rest of the lane (store, poll, drafts,
approvals, Telegram) needs to change to make it work.

To implement it, fill in the four methods below. Notes from what we already know
about FB Marketplace — hard-won, so do not start from zero:

  * `html`/`text` reads on the seller inbox return only nav chrome: the thread
    list is virtualised and lazily hydrated. A DOM read is unreliable.
  * FB's own GraphQL is used by the web app and carries the data, but it needs
    the `fb_dtsg` token, the `__req` counter and the LSD, all of which change
    per page load — extract them from the page on each session, never cache.
  * Login is a hard wall. Never automate credentials. A human signs in once in a
    headed browser; the profile on disk keeps the session.
  * The seller inbox lives at https://www.facebook.com/marketplace/you/selling
    and individual threads open in a right-hand pane, not a separate page.

Recommended shape: reuse the DBA approach — READ over an authenticated HTTP
session (extract the FB tokens + cookies once per run), and SEND through the DOM
over CDP with verification by re-reading the thread. That keeps the expensive
path cheap and the risky path human-approved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base import Conversation, Message, Provider, ProviderError  # noqa: E402

NOT_IMPLEMENTED = ("fb provider is not implemented yet — the interface is stable, "
                   "so implementing it does not require touching poll/cli/store")


class Facebook(Provider):
    name = "fb"

    def list_conversations(self) -> list[Conversation]:
        raise NotImplementedError(NOT_IMPLEMENTED)

    def fetch_thread(self, conv_id: str) -> list[Message]:
        raise NotImplementedError(NOT_IMPLEMENTED)

    def send(self, conv_id: str, body: str) -> dict:
        raise NotImplementedError(NOT_IMPLEMENTED)

    def whoami(self) -> dict:
        raise NotImplementedError(NOT_IMPLEMENTED)
