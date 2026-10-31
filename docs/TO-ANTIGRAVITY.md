# Handoff — listing side (Antigravity) ↔ messaging side (Hermes)

Written 2026-10-31 so the two halves can be merged on Discord without either side
guessing. Read `ARCHITECTURE.md` first; this file is the practical asks.

---

## What Jacob wants overall

A friend-installable setup where an agent **lists** items and **handles the
buyers** on a marketplace, with a human approving anything that reaches a stranger.
Registration/listing is Antigravity's half. Conversations are this repo.

---

## What I need from the listing side

Only three things, and all are small:

1. **The item registry, in the shape described in `ARCHITECTURE.md` §1.**
   `(provider, item_id, title, price_dkk, status, ad_url, created_at,
   status_changed_at)`. A JSON file or a SQLite table is fine — I read it, I never
   write it.

2. **`status=sold` emitted promptly** (within a minute of the real sale). This is
   the only genuinely dangerous window in the design: a buyer asking "still
   available?" *after* a sale is the worst message we can send. Everything else
   can be minutes late; this cannot.

3. **`item_id` recorded at creation**, using the marketplace's own id — not a
   local uuid. On DBA that is `itemInfo.itemId` (e.g. `18961393`); it is the join
   key and it cannot be reconstructed from the ad URL.

---

## What I give you

- **A working conversation lane** with a human approval gate that cannot be
  bypassed in code, verified delivery, and idempotent polling.
- **`providers/base.py`** — the six-method contract. Adding Facebook is one file.
- **The DBA API facts** in `docs/DBA-API.md`, including the four things that are
  *not* true, so you do not lose a day to them.

---

## Questions I need answered to finish the merge

1. **Where does the registry live on disk**, and who creates it first? I need a
   path, or a decision that messaging may create it empty on first run.
2. **What are your status values**, exactly? If they differ from
   `draft/listed/reserved/sold/withdrawn`, we need a mapping table — say which
   side owns it.
3. **Do you emit updates, or do I poll the registry?** Polling is fine and simpler
   (one file read per tick, no daemon).
4. **Does your listing flow already know the marketplace item id, or does it only
   learn it after publish?** If only after, the registry row is briefly without a
   join key and messaging must skip it rather than guess.

---

## Things I deliberately did NOT build

Stated so nobody assumes they exist:

- **No send automation on Facebook.** Provider stub only, with notes on the
  virtualised inbox, per-load `fb_dtsg`/LSD tokens, and the hard login wall.
- **No credential automation for any marketplace.** Session expiry raises and
  waits for a human; there is no login code path.
- **No transcript endpoint for DBA** — it has not been captured yet, so
  `fetch_thread` returns the last message and the store accumulates history.
- **No auto-retry on an unverified send**, because a retry can duplicate a message
  to a real buyer.
