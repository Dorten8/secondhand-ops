# Architecture — how listing and messaging fit together

Two systems, one item. This repo owns the **conversation** side; the **listing**
side (ad creation, photos, pricing, publishing) is a separate codebase. They meet
at exactly one place, and this document defines it so neither side has to guess.

```
  ┌────────────────────────┐            ┌──────────────────────────┐
  │  LISTING SIDE          │            │  MESSAGING SIDE          │
  │  (own repo)            │            │  (this repo)             │
  │                        │            │                          │
  │  create ad ──┐         │            │   ┌── poll ──┐           │
  │  photos      │         │            │   │  draft   │           │
  │  price       ├─▶ item ─┼── items ──▶│   │ approve  │           │
  │  publish     │         │    .json   │   └── send ──┘           │
  │  status ─────┘         │            │                          │
  └────────────────────────┘            └──────────────────────────┘
               │                                      │
               └────────── same marketplace ──────────┘
                        (DBA, Facebook, …)
```

---

## 1. The seam is one table

The only shared state is an **item registry**. Keep it deliberately boring: a
JSON file or a SQLite table, readable by both sides, writable by the listing side
only. Messaging treats it as read-only input.

```json
{
  "provider": "dba",
  "item_id": "25983773",
  "title": "Rear Wheel / Baghjul - 28\", 7-Speed Rulle/Skivebremse",
  "price_dkk": 150,
  "status": "listed",
  "ad_url": "https://www.dba.dk/my-items/details/18961393",
  "photo_count": 4,
  "created_at": "2026-10-24T09:12:00Z",
  "status_changed_at": "2026-10-24T09:12:00Z"
}
```

### Join key

```
(provider, item_id)
```

Nothing else. Not the conversation id, not the ad URL, not the title — those are
provider-internal and change. `item_id` is stable for the life of the ad.

### Status vocabulary — five words, no synonyms

| status | meaning for messaging |
|---|---|
| `draft` | not published yet — **never** poll or reply |
| `listed` | live — normal handling |
| `reserved` | live, someone committed — still reply, but do not sell twice |
| `sold` | **stop replying**; close open drafts |
| `withdrawn` | stop replying; treat like sold |

If the listing side invents a new status, messaging must treat unknown values as
`listed` (fail open on messaging, fail closed on selling). Say the word and this
rule changes, but it must be written down.

---

## 2. What the listing side must guarantee

Three promises, all cheap:

1. **Emit `item_id` at creation** — before or at the moment the ad goes live.
2. **Emit status transitions** — `sold` must land within a minute of the sale, or
   messy things happen (see below).
3. **Keep the file/table readable without a lock held open** — atomic rename or a
   WAL-mode SQLite, so a read never sees half an ad.

## 3. What the messaging side guarantees

1. **Never sends without human approval.** Enforced in `msgctl approve`, not by
   convention. There is no code path that sends an unapproved draft.
2. **Verified delivery.** After a send, delivery is confirmed by re-reading the
   marketplace API; an unverifiable send is reported as failed and is **never**
   retried automatically, because a retry can duplicate a message.
3. **Idempotent polling.** The same message never queues twice; keys are stable
   hashes, not process-local values.
4. **No login automation.** If a session dies, it raises and waits for a human.

---

## 4. Why this split is the right one

**Different failure modes.** Listing is batch, human-paced, and a mistake costs a
mis-priced ad — annoying but fixable. Messaging is real-time, talks to strangers,
and a mistake sends a wrong price or a wrong promise to a real buyer. They should
not share a deploy, a process, or a crash.

**Different cadence.** Listing runs when Jacob publishes. Messaging runs on a
timer, forever, and must be silent when idle. Coupling them means a listing bug
silences the inbox.

**Different trust.** Messaging needs a credential that can speak as him. Listing
needs one that can publish. Keeping them in separate processes with separate
cookie files limits what one leak can do.

**Different reusability.** Friends will wire this to *their* marketplaces with
*their* browsers. A single repo that does everything is one they cannot partially
adopt. The seam above lets someone take messaging only — which is the more useful
half on its own.

---

## 5. How a sale actually flows across the seam

```
buyer writes           messaging: poll queues it
      │
      ▼
draft ──▶ Jacob approves ──▶ send (verified)
      │
      │ buyer commits to buy
      ▼
Jacob marks sold       listing side writes status=sold
      │
      ▼
messaging sees sold    closes open drafts, stops queueing new ones
                       (a "still available?" message after a sale is the
                        single worst message to send — hence promise #2)
```

The gap between "sold in the real world" and "`status=sold` on disk" is the only
dangerous window in the whole design. That is why the listing side must emit the
transition promptly rather than on the next full sync.

---

## 6. Adding a marketplace

Both sides add a provider; neither touches the other. On the messaging side that
is `providers/<name>.py` implementing six methods (`providers/base.py`). The seam
does not change: still `(provider, item_id)`, still five statuses.

---

## 7. Open questions for the merge discussion

- **Who owns the registry file?** Proposal: listing side writes, messaging reads.
  If messaging ever needs to write (e.g. "buyer asked a question, flag the ad"),
  it writes a *separate* sidecar file, not the registry.
- **Sold detection fallback.** If the listing side is down, should messaging infer
  `sold` from the marketplace (ad no longer found)? Proposal: yes, but only as a
  warning to Jacob — never silently.
- **Multi-listing threads.** One conversation can span two ads (buyer asks about
  two wheels). Today messaging keys on conversation; the registry lookup must
  tolerate a conversation touching several `item_id`s.
- **Where drafts live.** Messaging owns them. Listing must never write a message.
