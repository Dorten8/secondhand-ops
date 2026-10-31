# secondhand-ops — architecture

**Purpose.** Sell second-hand items without living inside a marketplace inbox. An
agent does the repetitive work — watch the listings, watch the inbox, draft replies,
know who owes a photo — and **a human approves every outbound message**. Nothing is
sent on its own authority.

This document is written from above the code. If a different harness, agent or person
picks this up: the *point* is here, the *mechanics* are in the modules. Keep the
point, replace the mechanics.

---

## 1. One pipeline, modular stages — not a monolith

The pipeline is a **sequence of stages, each a script with a single job**. A stage
reads its input, does one thing, writes its output, exits. Stages meet through
**files and rows, not through each other's internals**.

```
 capture  →  register  →  list    →  poll   →  draft  →  approve  →  send   →  verify
 ad text     items      publish     inbox      reply     human       deliver   prove
             .json
```

| Stage | Script | In | Out |
|---|---|---|---|
| capture | `messaging/capture_post.py` | URL / pasted text | ad fields |
| register | `messaging/registry.py` | ad fields | `items.json` row |
| list | `messaging/lane_listing.py` *(separate lane, same project)* | row | published ad, `status=listed` |
| poll | `messaging/poll.py` | provider inbox | queued threads |
| draft | `messaging/msgctl.py` | thread | draft awaiting approval |
| approve | `msgctl.py` — human step | draft | approved text |
| send | `msgctl.py` | approved text | delivered message |
| verify | `notify.py` + provider API | delivery | confirmed record |

**The rule that makes it modular:** a stage may only depend on the *format of the
file it reads*. If a stage needs another stage's internals, the seam is wrong —
widen the file, never the coupling.

**Why modular matters here, concretely:** the listing lane is batch and human-paced;
the messaging lane runs on a timer forever and must be silent when idle. A bug in one
must not silence the other. Separate scripts give that for free; one monolith with a
shared process does not.

---

## 2. The seam is one registry

The only shared state is an **item registry** — a JSON file (`items.json`) or a
SQLite table. Deliberately boring.

- **The listing lane writes it.** That is the only writer.
- **Everything else reads it.** Messaging treats it as read-only input.

```json
{
  "provider": "dba",
  "item_id": "18961393",
  "title": "Rear Wheel / Baghjul - 28\", 7-Speed Rulle/Skivebremse",
  "price_dkk": 150,
  "status": "listed",
  "ad_url": "https://www.dba.dk/my-items/details/18961393",
  "photo_count": 4,
  "created_at": "2026-10-24T09:12:00Z",
  "status_changed_at": "2026-10-24T09:12:00Z"
}
```

**Join key: `(provider, item_id)`.** Nothing else — not the conversation id, not the
ad URL, not the title. Those are provider-internal and change; `item_id` is stable for
the life of the ad.

### Status vocabulary — five words, no synonyms

| status | meaning for messaging |
|---|---|
| `draft` | not published yet — **never** poll or reply |
| `listed` | live — normal handling |
| `reserved` | live, someone committed — reply, but do not sell twice |
| `sold` | **stop replying**; close open drafts |
| `withdrawn` | stop replying; treat like sold |

Unknown values are treated as `listed` — fail open on messaging, fail closed on
selling. If that rule changes, change it here in writing.

**Read-only is a feature, not a permission bug.** If messaging could write the
registry, two lanes could disagree about what is on sale and an item the human
believes is sold stays listed. One writer, many readers.

---

## 3. How it talks to a marketplace — access strategy

Ordered by preference. Written as policy, because this is the part most likely to
need adapting by whoever picks the project up.

**Primary — HTTP with an exported cookie jar.**
Replay the same authenticated HTTP calls the browser makes, with cookies the human
exported once. Headless, fast, scriptable, diffable, no browser needed. When it
breaks, the failure is a status code — easy to read and easy to assert on.

**Fallback — native CDP against a real browser.**
Attach over the Chrome DevTools Protocol to a browser the harness controls. Use it
when a flow genuinely cannot be replayed: JS-gated forms, anti-bot handshakes, pages
that only work after real interaction.

**Bootstrap — CDP is also how the cookie jar is obtained in the first place.**
There is no jar on day one. Drive a browser over CDP, let the human log in, export the
cookies, then run everything over HTTP. Cookies expire → re-bootstrap. This is the one
place a browser is mandatory.

**Harness note.** A harness that already owns a browser (Antigravity and similar agent
runtimes) should launch **its own** Chromium for the CDP steps rather than borrowing
the human's desktop session. That keeps the login flow inside the harness and removes
a dependency on whatever the human happens to have open.

**Never:** credentials or cookies in the repo. The jar lives outside the tree,
referenced by path, readable only by the running user.

---

## 4. Guarantees

**The listing lane must:** emit `item_id` at creation; emit status transitions
promptly (`sold` within a minute); keep the registry readable without a held lock
(atomic rename, or WAL-mode SQLite).

**The messaging lane must:**

1. **Never send without human approval** — enforced in `msgctl approve`, not by
   convention. No code path sends an unapproved draft.
2. **Verify delivery** by re-reading the provider API. An unverifiable send is
   reported as failed and is **never** retried automatically — a retry can duplicate
   a message.
3. **Poll idempotently** — the same message never queues twice; keys are stable
   hashes, not process-local values.
4. **Not automate login.** If a session dies, it raises and waits for a human.
5. **Stay silent and free when idle** — polling is a cheap conditional check; the
   model wakes only when something changed.

---

## 5. How a sale flows

```
buyer writes           poll queues it
      │
      ▼
draft ──▶ human approves ──▶ send (verified)
      │
      │ buyer commits to buy
      ▼
human marks sold       listing lane writes status=sold
      │
      ▼
messaging sees sold    closes open drafts, stops queueing new ones
```

The gap between "sold in the real world" and `status=sold` on disk is the only
dangerous window in the design. A "still available?" message after a sale is the
worst message this system can send — hence the prompt-transition guarantee.

---

## 6. Adding a marketplace

Add a provider; change nothing else. On the messaging side that is
`providers/<name>.py` implementing the six methods in `providers/base.py`. The seam
does not change: still `(provider, item_id)`, still five statuses.

---

## 7. Open questions

- **Registry ownership** — listing writes, everything else reads. If messaging ever
  needs to record something about an item, it writes a *sidecar* file, never the
  registry.
- **Sold detection when the listing lane is down** — infer from the marketplace (ad
  gone) as a *warning to the human* only, never silently.
- **Multi-listing threads** — one conversation can span two ads; registry lookup must
  tolerate several `item_id`s per conversation.
- **Where drafts live** — messaging owns them. Listing must never write a message.

---

## 8. Picking this up as a different harness

Read in this order: §1 for the shape, §2 for the contract, §3 for access, §4 for the
limits. Then replace any single stage without reading the rest.

**Preserve:** the stage boundaries, the registry contract, the approval gate, and the
prove-the-send rule.

**Free to replace:** the provider adapter, the storage backend (JSON ↔ SQLite), the
notification channel, the transport (HTTP ↔ CDP), and the language each stage is
written in.

---

## 9. Where the output goes

The destination of a message is **deployment configuration, not code**. Any user of
this pipeline must be able to route it at their own chat without editing a script, so
no chat id, channel id, topic or webhook may be hardcoded anywhere in the tree.

The pipeline emits events (`buyer_message`, `draft_ready`, `delivery_verified`,
`delivery_failed`, `item_sold`); a configurable **sink** consumes them. Sinks worth
supporting: Telegram chat (optionally a forum topic), Discord channel, HTTP webhook,
`stdout` for dry runs.

A sink must be idempotent on the event id, must stay silent when there is nothing to
say, and must **fail loudly at startup** if its destination is missing — never fall
back to a default chat, because that sends production traffic somewhere unintended.

Full config shape, sink guarantees and the split between production and development
channels: [`docs/DELIVERY-ROUTING.md`](docs/DELIVERY-ROUTING.md).
