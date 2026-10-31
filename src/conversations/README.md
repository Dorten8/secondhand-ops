# Marketplace messaging lane

One durable queue, four stateless stages, one provider per marketplace.
Built 2026-10-31 after the container→bare-metal migration, to replace a
collection of ad-hoc scraping scripts with something that can be reasoned about.

---

## The shape

```
             ┌──────────┐   HTTP + cookies, no browser, ~0 cost
   DBA  ────▶│ provider │──▶ list_conversations()  ─┐
   FB   ────▶│          │──▶ fetch_thread()         │
             └──────────┘──▶ send(conv, body)       │
                                                    ▼
                                              ┌──────────┐
                                              │  store   │  SQLite: one source of truth
                                              └──────────┘
                                                    │
              ┌──────────────┬──────────────────────┘
              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
        │  poll    │   │  draft   │   │ approve  │   │  send    │
        │ 0 tokens │   │  LLM     │   │  human   │   │ verified │
        └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

Only **draft** costs tokens. Everything else is a script.

**Why it is portable:** no stage holds state in memory. Every stage reads and
writes the same SQLite file and can run on any machine that has the file. That is
what makes it runnable on Antigravity, or from a shell, or by a different agent,
without rewriting anything.

---

## The "notification" question — answered with measurements

Jacob asked why we poll instead of hooking DBA's new-message notification.
Measured on the live page:

```
Notification.permission      = 'default'   -> push was never granted
serviceWorker registrations  = 1 active    (https://www.dba.dk/sw.js)
pushManager subscription     = null        -> nothing to receive
```

So DBA's alert is **WebPush**, it is **not enabled**, and even if it were, the
payload is encrypted to a subscription key that never leaves the browser — CDP
cannot see it. It is not a shortcut we missed; it is not interceptable here.

**The replacement is better.** DBA's own JSON API already exposes the two facts a
notification would give us:

| field | meaning |
|---|---|
| `totalUnseenCounter` | how many unseen messages in that conversation group |
| `lastMessageOutgoing` | `true` = **we** spoke last, ball is with the buyer |
| `lastMessagePreview` | the message text |
| `lastMessageDate` | when |
| `itemId` / `itemInfo.title` | which listing |
| `conversationId` | how to reply |

One `GET`, ~35 KB, no browser, zero tokens:

```
GET https://www.dba.dk/messages/api/conversations/users/<loginId>/conversationgroups
```

Polled every 5 minutes that is cheaper and more reliable than push would have
been, and it works from any machine holding the cookies.

---

## Stages

### 1. `poll.py` — trigger (zero tokens, silent)

```bash
python3 poll.py dba
```

- Prints **nothing** when nothing is new → cron wakes nobody.
- Prints a digest when a buyer spoke since last run.
- **Prime:** on an empty store it records everything and emits nothing. Without
  this, first run announces every historical thread at once (measured: 12).
- **Age filter:** threads older than 14 days are ignored. A December thread is
  not news.
- Exit 2 on auth/network problems, one line on stderr, never a human-facing spam.

### 2. Draft (LLM) — the only paid stage

The agent reads `msgctl pending`, reads the thread, and records a proposal:

```bash
msgctl pending                       # what needs drafting
msgctl draft --conv <id> --body-file /tmp/body.txt --inbound "<their message>"
```

`msgctl draft` returns the exact message to send to Jacob via `notify.py`.

### 3. Approve — human gate, enforced in code

```bash
msgctl approve --id 7 --action send      --by Jacob
msgctl approve --id 7 --action ignore    --by Jacob
msgctl approve --id 7 --action supersede --body-file /tmp/new.txt --by Jacob
```

- `send` calls `provider.send()`, which **verifies delivery against the API** and
  raises if it cannot prove the message left.
- `ignore` closes the thread's queue entry.
- `supersede` marks the old draft and creates a new pending one (the "Other" path).
- A draft can only leave `pending` once. Deciding twice is refused — that is what
  stops a double send when a message is retried.

### 4. Provider — per marketplace

`providers/base.py` is the contract; six methods. `providers/fb.py` is a stub with
the hard-won FB notes inline (virtualised inbox, `fb_dtsg`/LSD tokens, hard login
wall). **Implementing FB touches nothing else.**

---

## Files

```
messaging/
  mstore.py            SQLite schema + all state transitions
  poll.py              trigger stage (cron entry point)
  msgctl.py            CLI: auth, poll, pending, draft, drafts, show, approve, stats
  notify.py            the exact message Jacob sees (4 options)
  providers/base.py    Provider contract + Conversation/Message types
  providers/dba.py     DBA: HTTP read, DOM send, API-verified delivery
  providers/fb.py      Facebook stub + implementation notes
  dba_account.json     {"login_id": "..."}          (chmod 600)
  dba_cookies.json     exported cookies             (chmod 600)
  state.db             the queue
```

---

## Setup / recovery

```bash
cd <repo>/src/conversations
P=/home/hermes/hermes-agent/.venv/bin/python3

$P msgctl.py auth          # export cookies from Brave over CDP (Brave must be on 9222)
$P msgctl.py whoami        # prove the session works
$P msgctl.py poll          # first run primes; silent afterwards
$P msgctl.py stats
```

**Session expired?** `msgctl auth` again — it re-reads Brave's cookies. If Brave
is not running with `--remote-debugging-port=9222`, that fails and the answer is a
human login, never an automated one.

---

## Adding Facebook later

1. `providers/fb.py` — implement the four methods.
2. Nothing else changes. `poll.py fb`, `msgctl --provider fb ...` already work.
3. Watch for: the FB inbox is virtualised, so read through the authenticated HTTP
   session rather than the DOM; extract `fb_dtsg` and LSD fresh per run; never
   automate credentials.

---

## Known limits (stated, not hidden)

- **DBA transcript is partial.** DBA loads messages separately from thread
  metadata; that endpoint has not been captured yet, so `fetch_thread` returns the
  last message only. The store accumulates history as we poll, which closes the
  gap over time.
- **DBA send uses the DOM**, because the POST endpoint is unobserved. Delivery is
  still verified through the JSON API, so a click that does nothing cannot be
  mistaken for a send. Capturing the POST (one approved real send with Network
  enabled) would let sending become HTTP-only too.
- **No retry on unverified send.** A retried send can duplicate a message, so it
  fails loudly and waits for a human instead.
