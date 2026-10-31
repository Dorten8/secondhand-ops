#!/usr/bin/env python3
"""poll.py — the trigger stage. ZERO TOKENS. Silent when nothing is new.

WHY POLLING AND NOT THE NOTIFICATION
Measured on the live DBA page (2026-10-31):

  Notification.permission     = 'default'   -> push was NEVER granted
  serviceWorker registrations = 1 active    (https://www.dba.dk/sw.js)
  pushManager subscription    = null        -> there is nothing to receive

DBA's "you have a message" alert is WebPush, and it is not even enabled. Even if
it were, a WebPush payload is encrypted to the browser's subscription key which
never leaves the browser — CDP does not expose it, so it cannot be intercepted
from outside. Hooking it is not a shortcut we missed; it is not possible here.

WHAT WE USE INSTEAD — and it is better
DBA's own JSON API exposes `totalUnseenCounter` and `lastMessageOutgoing` per
conversation. That IS the notification, as structured data:

  * one HTTP GET, no browser, ~35 KB
  * zero tokens
  * tells us WHO spoke last without parsing anything

Polling that on a 2-minute timer is cheaper and more reliable than push would
have been, and it runs anywhere that holds the cookies — including Antigravity.

TWO GUARDS THAT KEEP IT USEFUL
  1. PRIME ON FIRST RUN. An empty store would otherwise emit an event for every
     historical thread at once — 12 messages is a flood, not a notification.
     First run records everything and says nothing.
  2. AGE FILTER. Threads whose last message is older than `max_age_days`
     (default 14) are ignored. A December thread is not news.

EXIT CONTRACT
  nothing new   -> print NOTHING, exit 0     (cron stays silent, wakes nobody)
  something new -> print a digest, exit 0    (this is what wakes the agent)
  auth/network  -> one line to stderr, exit 2, never spam a human
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "providers"))

from mstore import Store  # noqa: E402
from base import AuthError, ProviderError  # noqa: E402

MAX_AGE_DAYS = 14


def get_provider(name: str):
    if name == "dba":
        from dba import DBA
        return DBA()
    if name == "fb":
        from fb import Facebook
        return Facebook()
    raise SystemExit(f"unknown provider {name!r}")


def _parse_when(s: str):
    """lastMessageDate looks like 2026-10-30T11:20:52.239384 (no tz). Treat as UTC."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def is_stale(last_at: str, max_age_days: int = MAX_AGE_DAYS) -> bool:
    d = _parse_when(last_at)
    if d is None:
        return False                      # unknown age: do not hide it
    age = (datetime.now(timezone.utc) - d).total_seconds() / 86400
    return age > max_age_days


def poll(provider_name: str = "dba", store: Store | None = None,
         max_age_days: int | None = None) -> int:
    store = store or Store()
    max_age = MAX_AGE_DAYS if max_age_days is None else max_age_days
    priming = len(store.conversations(provider_name)) == 0
    run_id = store.run("prime" if priming else "poll", provider_name)

    try:
        prov = get_provider(provider_name)
        convos = prov.list_conversations()
    except AuthError as exc:
        store.end_run(run_id, "auth_error", str(exc))
        print(f"AUTH: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        store.end_run(run_id, "error", f"{type(exc).__name__}: {exc}")
        print(f"POLL ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    queued = stale = sold = 0
    for c in convos:
        store.upsert_conversation(
            provider_name, c.conv_id,
            item_id=c.item_id, item_title=c.item_title,
            partner_name=c.partner_name, partner_id=c.partner_id,
            last_preview=c.last_preview, last_outgoing=c.last_outgoing,
            unseen=c.unseen, sold=c.sold, last_at=c.last_at)

        if c.last_preview:
            store.add_message(provider_name, c.conv_id, c.last_preview,
                              "out" if c.last_outgoing else "in")

        if not c.ball_is_with_us() or not c.last_preview:
            continue
        if c.sold:
            sold += 1
            continue
        if is_stale(c.last_at, max_age):
            stale += 1
            continue
        if store.push_event("needs_draft", provider_name, c.conv_id, c.last_preview):
            queued += 1
            if priming:
                # remember it so the next run does not announce history as news
                for ev in store.open_events("needs_draft"):
                    if ev["provider"] == provider_name and ev["conv_id"] == c.conv_id:
                        store.close_event(ev["id"])

    store.end_run(run_id, "ok",
                  f"{len(convos)} conversations, prime={priming}, "
                  f"queued={queued}, stale={stale}, sold={sold}")

    if priming:
        # silent, but say it on stderr so a human running it by hand understands
        print(f"PRIMED {len(convos)} conversations, queued none "
              f"(stale={stale}, sold={sold})", file=sys.stderr)
        return 0

    # Report the whole OPEN queue, not only what this tick created. Otherwise a
    # drafting run that crashed would leave a thread queued but invisible, and
    # the monitor would keep printing an unchanged (empty) digest forever.
    open_ev = store.open_events("needs_draft")
    if not open_ev:
        return 0                          # SILENT

    by_conv = {c["conv_id"]: c for c in store.conversations(provider_name)}
    print("DBA MESSAGES NEEDING A REPLY")
    print("=" * 48)
    for ev in open_ev:
        c = by_conv.get(ev["conv_id"], {})
        print(f"\nAD:    {c.get('item_title') or '?'}")
        print(f"ITEM:  {c.get('item_id') or '?'}")
        print(f"FROM:  {c.get('partner_name') or '?'}")
        print(f"CONV:  {ev['conv_id']}")
        print(f"WHEN:  {str(c.get('last_at') or '')[:16]}")
        print(f"SAYS:  {(ev['payload'] or c.get('last_preview') or '')[:400]}")
    print(f"\nCOUNT {len(open_ev)}  (draft these, then: msgctl drafts)")
    return 0


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "dba"
    sys.exit(poll(name))
