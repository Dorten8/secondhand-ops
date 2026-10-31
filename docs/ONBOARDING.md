# Onboarding — read this second, after `docs/architecture.md`

Written for an LLM or a human who has just cloned this repo and needs to know
**how it works, what exists, and what to implement** without reading 3,000 lines.

---

## 1. What this is, in five sentences

It sells second-hand bike parts on DBA.dk and Facebook Marketplace. Materials
(voice memos, photos, text) become structured item folders, which become a
catalogue, which becomes live ads. Buyers then message, and a stage reads those
messages, drafts replies, and waits for a human to approve before anything is
sent. Nothing reaches a stranger without explicit approval, and no send is
believed until the marketplace's own API confirms it.

---

## 2. Read order (about 20 minutes, and enough to work on it)

| # | file | why |
|---|---|---|
| 1 | `docs/architecture.md` | the diagrams; every node is a directory here |
| 2 | `src/conversations/poll.py` | the zero-token trigger; the whole contract in one file |
| 3 | `src/conversations/providers/dba.py` | the reference provider: read over HTTP, send with proof |
| 4 | `src/registry/registry.py` + `src/conversations/registry.py` | the seam, and why one side is read-only |
| 5 | `docs/DBA-API.md` | the measured endpoints, and the four things that are *not* true |

Everything else is either a stage you only touch when adding a marketplace, or
legacy you can ignore.

---

## 3. Invariants — breaking any of these is a bug, not a trade-off

1. **A human approves every outbound message.** Enforced in
   `msgctl.approve` by a state machine (`pending → sent|ignored|superseded`),
   not by prompt wording. A draft leaves `pending` exactly once.
2. **A send is not real until verified.** Re-read the provider's API and require
   the message to be the last outgoing one. HTTP 200 is not proof.
3. **No automatic retry of an unverified send.** A retry can duplicate a message
   to a real person; the correct behaviour is to fail loudly and ask.
4. **Polling is silent and free.** Empty output = nobody wakes, no tokens spent.
   The trigger stage must never print anything when there is nothing to do.
5. **First run primes.** An empty store records history and queues nothing, or
   the first tick announces every old thread as news.
6. **The registry is written by `publishing`, read by `conversations`.**
   Never add a write to the read-only door in `src/conversations/registry.py`.
7. **Never automate a marketplace login.** Expired session → raise → a human logs in.

---

## 4. What actually exists

| stage | state | entry point |
|---|---|---|
| `intake` | works — local Whisper, no API key | `src/intake/transcribe.py` |
| `catalog` | works — builds the listing dict + wheelset relations | `src/catalog/build_catalog.py` |
| `publishing` | works for DBA; FB provider is a stub | `src/publishing/listctl.py`, `providers/` |
| `conversations` | **DBA fully working**: poll → draft → approve → send → verified | `bin/msgctl`, `src/conversations/poll.py` |
| `registry` | works — atomic JSON, Notion mirror optional | `src/registry/registry.py` |
| `auth` | works — headed login + cookie import | `src/auth/login_marketplace.py` |

### What is genuinely missing (do these first)

| # | gap | where | why it matters |
|---|---|---|---|
| 1 | **Facebook provider** | `src/conversations/providers/fb.py`, `src/publishing/providers/fb.py` | stubs raise `NotImplementedError` with implementation notes inline |
| 2 | **DBA transcript endpoint** | `src/conversations/providers/dba.py::fetch_thread` | the messages endpoint is not captured, so a thread returns only the last message; the store accumulates history instead |
| 3 | **Registry wiring into poll** | `src/conversations/poll.py` | the read-only door exists but `poll` does not yet skip `sold` rows from it; today it relies on the provider's own sold flag |
| 4 | **HTTP send not yet primary** | `providers/dba.py::send` | `send_http()` works; default is still `via="dom"` until one real HTTP send is verified |
| 5 | **No fixture tests** | `tests/` | a captured JSON payload is enough to test parsing without a marketplace |
| 6 | **`bin/listctl` vs `src/publishing/listctl.py`** | both | the root shim predates the `src/` move; verify it still forwards |

---

## 5. Adding a marketplace — the only change that is routine

Two files, four methods each. Nothing else in the repo moves.

```
src/conversations/providers/<name>.py     # list_conversations, fetch_thread, send, whoami
src/publishing/providers/<name>.py        # publish, update, delete (see base.py)
```

Then:
1. Add the provider name to `VALID_PROVIDERS` in `src/registry/registry.py`.
2. Add a branch in `src/conversations/poll.py::get_provider`.
3. Set `provider` on the item rows that belong to it.

Rules that save a day:
- **Read through the marketplace's own API**, not the DOM, if one exists. DBA's
  `conversationgroups` endpoint gives `lastMessageOutgoing`, which removes every
  "who spoke last" heuristic.
- **Verify sends against the API**, never against the fact that a request returned 200.
- **Paginate and cache nothing.** Every call re-reads; a crashed tick must be harmless.

---

## 6. Traps already paid for — do not rediscover these

| trap | symptom | fix |
|---|---|---|
| Synthetic `.click()` on DBA | URL changes, conversation pane never renders, composer never appears | dispatch a real `Input.dispatchMouseEvent` at the element's coordinates |
| DBA composer selector | `placeholder="Skriv en besked..."` matches nothing | it is now `textarea[aria-label^="Skriv din besked"]` |
| Python `hash()` as a dedupe key | every run re-inserts every message | `hashlib.sha1` — `hash()` is salted per process |
| Cloudflare on `discord.com` | `403 error code: 1010` — looks like a permission error | send a `User-Agent` on every API call |
| Discord/Telegram webhooks as a notifier | nothing arrives | DBA's alert is WebPush with no subscription; the JSON API poll *is* the notification |
| Reading `executions.db` mid-run | "job failed" that later completed | read at the end, not in-flight |

---

## 7. Definition of done for a change here

A change is finished when:

1. `python3 -c "import ast"` parses every touched file (trivial) **and** the CLI
   verb it affects runs (`bin/msgctl --help` at minimum).
2. If it can send anything: the approval gate still refuses a second decision,
   and an unverified send still raises instead of returning `ok: true`.
3. If it polls: an empty run prints **nothing** and exits 0.
4. The relevant `docs/` page is updated — this repo is read by agents, and a doc
   that contradicts the code is a bug.
5. Comments explain **why**, not what. A line like
   `# retry once` is useless; `# a retry can duplicate a message to a real
   buyer, so fail loudly instead` is the standard.

---

## 8. Where the live instance runs (context, not requirements)

The reference deployment is a home server, user `hermes`, systemd user unit
`hermes-gateway-*`, with a cron tick that runs the poll stage every 5 minutes
under a change-detector so the agent only wakes when the output changes.
Nothing in `src/` depends on that arrangement — any scheduler works.
