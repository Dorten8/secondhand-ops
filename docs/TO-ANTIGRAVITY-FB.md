# Work order — Facebook Marketplace message pickup

Written 2026-11-16 by Hermes on the host. For Antigravity.

## Goal

`src/conversations/providers/fb.py` must make Facebook Marketplace behave like DBA:
detect a new buyer message, draft a reply, send only after a human taps, and verify
the send against the thread. Everything else in the lane already works — the store,
the poll, the Telegram card, the approval router, the send verification pattern. This
is one provider file.

## The hard constraint

**You cannot test this from the Legion.** The Facebook session lives in a Brave
profile on the host, reachable only over loopback CDP (`127.0.0.1:9222`), which is
deliberately not exposed to the LAN. So the loop is:

1. you write the code,
2. I run it against the live session,
3. I paste the exact failure back.

Do not mock the DOM and call it done — the previous version of this file was written
against an imagined DOM (`a[href*="/messages/t/"]` rows) and never worked once.

## What is verified against a live account

Chat list — `https://www.facebook.com/messages`

- Rows are `[role="row"]`, positioned in the left column (`left < 420`, `top > 150`).
- A row's text is the whole row: `"Marketplace 1 new message · 14h"`,
  `"Pavel Masek · 2h"`, `"Filip Bęben You: well dont think about it now! …"`.
- **1:1 and Marketplace rows carry no `href`.** Only group chats expose
  `a[href*="/messages/t/"]`. Any code that enumerates anchors will find 2–3 group
  chats and zero marketplace threads. This is the bug that made the lane look dead.
- The Marketplace entry is an **aggregate row**, not a thread: clicking it opens the
  most recent marketplace conversation.

Thread — `https://www.facebook.com/messages/t/<thread_id>`

- Message bubbles carry `aria-label`, prefixed with `"Enter, "`:
  `"Enter, Message sent November 6, 2026, 12:53 PM by Andreas: <text>"`
  and `"... by You: <text>"` for ours. Direction, sender and timestamp are all in the
  label — never infer direction from alignment or colour.
- The thread also exposes `"Messages in conversation titled Andreas · Macbook Pro
  2018 (defekt batteri), 32GB ram, 1TB"` — buyer and listing in one string.
- Composer is `[contenteditable="true"]` with
  `aria-label="Write to <buyer> · <listing>"`.
- Reading works today: 11 messages parsed with correct directions.

Sending

- Type with `document.execCommand('insertText')`, then dispatch `keyDown`/`char`/`keyUp`
  with `text="\r"`.
- **A swallowed Enter looks exactly like a successful send.** Verification must
  re-read the thread and require our marker as the last `direction="out"` bubble.

Network capture (new, works)

- `cdp.py` now buffers CDP events; `Session.pump(seconds)` reads the socket without
  sending, and `take_events("Network.responseReceived")` returns them.
- Enabling `Network.enable`, navigating, and pumping for ~12 s yields ~340 responses.
- One 780 KB GraphQL body contained `snippet` 473 times — but it was **typeahead
  data**, not the thread list (`thread_fbid` appeared zero times).

## What is not solved

**Discovering thread ids.** Because rows have no href, the only ways to learn an id are:

1. **UI scan** — click a row, read the resulting URL. Implemented in `fb_scan.py`.
   **Result: it does not work. 4 rows × 2 attempts = 0 threads learned.** Synthetic
   mouse events on these rows do not trigger navigation under automation; the single
   successful redirect seen while probing came from a different click position and did
   not reproduce. Treat the UI path as dead unless you find the React handler's own
   trigger (`pointerdown`/`pointerup` with a real `pointerId`, or Enter on a focused
   row).
2. **GraphQL thread list** — the right answer, not yet found. The list must be fetched
   by some `doc_id` with variables carrying the folder/marketplace filter. Find it via
   `Network.requestWillBeSent` `postData` (look for `doc_id`, and strings like
   `ThreadList`, `Montage`, `MWInbox`, `thread_fbid`), then replay that request from
   the page with `fb_dtsg` read out of the document.

## What to build

Preferred: a `list_conversations()` that gets threads from the GraphQL response rather
than from clicking, returning `conv_id`, `partner_name`, `item_title`, `last_preview`,
`unseen`, `last_at`. Cache the discovered id set in `fb_threads.json` so a poll is
cheap even if the payload is expensive.

Fallback: make `fb_scan.py` reliable (click at the row's title text node, wait for the
URL to change, retry with a scroll nudge), and accept a 30-minute discovery cadence.

## Rules this file must keep

- **Never log in.** Raise `AuthError` and let a human authenticate.
- No credentials anywhere in the repo — everything personal comes from `.env`.
- A send is verified against the transcript, never against a keystroke or an HTTP 200.
- One buyer = one card. One live draft per conversation (the store enforces it).
- Don't touch `poll.py`, `msgctl.py`, `notify.py`, `approvals.py` — the provider is the
  seam, and the rest of the lane is already tested.
