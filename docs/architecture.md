# Architecture

Second-hand marketplace ops, end to end: **materials in → listings out → buyer
conversations handled → item marked sold**, with a human approving anything that
reaches a stranger.

Every top-level directory under `src/` is a **node in the diagram below**, and the
directory name is identical to the node name. If you are looking at a folder, you
can find it in the diagram; if you are looking at a node, you know the folder.

| node | directory | one line |
|---|---|---|
| `intake` | `src/intake/` | turn voice memos, photos and text into a structured item folder |
| `catalog` | `src/catalog/` | build the item catalogue and the relations between items |
| `publishing` | `src/publishing/` | push listings to each marketplace and keep them in sync |
| `conversations` | `src/conversations/` | read buyer messages, draft, get approval, send, verify |
| `registry` | `src/registry/` | the single seam both halves agree on, plus the Notion mirror |
| `auth` | `src/auth/` | marketplace sessions — human logins only, never automated |

---

## 1. End-to-end pipeline

```mermaid
flowchart LR
    H(["Jacob<br/>human"]):::human

    subgraph intake["intake/"]
        I1["Telegram bot<br/>voice · photo · text"]
        I2["transcribe.py<br/>Whisper, local"]
        I3["item folder<br/>transcription · metadata · photos"]
        I1 --> I2 --> I3
    end

    subgraph catalog["catalog/"]
        C1["build_catalog.py<br/>structured listing dict"]
        C2["inventory_relations.py<br/>wheelset ↔ wheel links"]
        C3["catalog.py · inventory.py"]
        C1 --> C3
        C2 --> C3
    end

    subgraph publishing["publishing/"]
        P1["batch_publisher.py<br/>post to DBA + FB"]
        P2["fix_past_dba_listings.py<br/>repair live ads"]
        P3["update_crosslinks_dba.py<br/>crosslinks in descriptions"]
        P4["providers/<br/>dba.py · fb.py"]
        P1 --> P4
        P2 --> P4
        P3 --> P4
    end

    subgraph registry["registry/"]
        R1["items.json<br/>status · item_id · price"]
        R2["notion_ssot.py<br/>human-readable mirror"]
        R1 -.-> R2
    end

    subgraph conversations["conversations/"]
        M1["poll.py<br/>zero tokens, silent"]
        M2["draft<br/>LLM, the only paid stage"]
        M3["approval gate<br/>msgctl approve"]
        M4["send<br/>verified against the API"]
        M5["providers/<br/>dba.py · fb.py"]
        M6["extractors/<br/>DOM fallbacks"]
        M1 --> M2 --> M3 --> M4 --> M5
        M6 -.-> M2
    end

    subgraph auth["auth/"]
        A1["login_marketplace.py<br/>headed, human-driven"]
        A2["import_cookies.py<br/>cookie jar"]
    end

    MK[["Marketplaces<br/>DBA · Facebook"]]:::ext

    H -->|"voice memo"| I1
    I3 --> C1
    C3 --> P1
    P1 --> MK
    MK -->|"buyer message"| M1
    R1 -->|"sold ⇒ stop replying"| M1
    P4 -->|"item_id after publish"| R1
    A1 --> A2 --> P4
    A2 --> M5
    M3 <-->|"approve / ignore / rewrite"| H
    M4 --> MK
    MK -->|"replies"| M1

    classDef human fill:#1f6feb,stroke:#0b3d91,color:#fff
    classDef ext fill:#30363d,stroke:#8b949e,color:#fff
```

**Reading it as a pipeline of packages:** `intake` produces an item folder →
`catalog` turns folders into a catalogue → `publishing` puts the catalogue on the
marketplaces and writes `item_id` back into `registry` → `conversations` polls the
marketplaces for buyer messages, drafts, waits for the human, sends, and reads
`registry` so a sold item stops being answered.

---

## 2. `conversations` — the stage worth understanding

The only stage that spends tokens, and the only one allowed to talk to a stranger.

```mermaid
stateDiagram-v2
    [*] --> polled: poll.py · zero tokens
    polled --> queued: buyer spoke last<br/>status = listed
    polled --> skipped: we spoke last<br/>or sold or stale

    queued --> drafted: LLM writes one reply
    drafted --> pending: recorded in the queue (SQLite)
    pending --> sent: human approves
    pending --> ignored: human ignores
    pending --> superseded: human rewrites<br/>→ new pending draft

    sent --> verified: API confirms<br/>lastMessageOutgoing = true
    verified --> [*]
    sent --> failed: cannot prove delivery
    failed --> pending: human retries deliberately

    skipped --> [*]
```

Five rules that are code, not intentions:

1. **Prime on first run** — an empty store records history and queues nothing.
2. **Age filter** — 14 days; a December thread is not news.
3. **Report the whole open queue** — a crashed drafting run cannot orphan a thread.
4. **Approval gate** — a draft leaves `pending` exactly once; deciding twice is refused.
5. **No auto-retry on an unverified send** — a retry can duplicate a message to a real person.

---

## 3. `registry` — the seam

`items.json` is the only thing `publishing` and `conversations` share.

```json
{"provider":"dba","item_id":"25983773","title":"Rear Wheel / Baghjul - 28\"",
 "price_dkk":150,"status":"listed","ad_url":"https://www.dba.dk/my-items/details/18961393",
 "created_at":"2026-10-24T09:12:00Z","status_changed_at":"2026-10-24T09:12:00Z"}
```

- **Join key:** `(provider, item_id)`. Nothing else — not the ad URL, not the title.
- **Statuses:** `draft · listed · reserved · sold · withdrawn`. Unknown values fail open for messaging and closed for selling.
- **Written by** `publishing` (atomically, via `os.replace`). **Read by** `conversations`, statelessly, once per tick.
- **`item_id` is only known after publish** — DBA assigns `itemInfo.itemId`. Rows with `status=draft` or `item_id=null` are skipped, never guessed.

The dangerous window is between "sold in the real world" and `status=sold` on disk.
A "still available?" message *after* a sale is the worst message this system can
send, which is why the transition must land within a minute.

---

## 4. Worked example — the bike parts, as actually sold

This is not a hypothetical. It is what ran on 2026-10-31.

```mermaid
sequenceDiagram
    actor J as Jacob
    participant I as intake
    participant C as catalog
    participant P as publishing
    participant R as registry
    participant M as marketplaces
    participant V as conversations
    actor B as Buyer

    J->>I: voice memo + photos (Telegram)
    I->>I: transcribe.py → transcription.txt
    I->>C: item folder
    C->>C: build catalogue + wheelset relations
    C->>P: 46 listings
    P->>M: publish (DBA + FB)
    M-->>P: item_id 18961393
    P->>R: status=listed, item_id set

    B->>M: “jeg mangler netop sådan et baghjul… bor i Odense?”
    V->>M: poll.py (one HTTP GET, zero tokens)
    M-->>V: lastMessageOutgoing=false → buyer spoke last
    V->>V: draft reply (the only token spend)
    V->>J: draft + four options
    J-->>V: “send as is”
    V->>M: POST …/conversations/{id}/messages
    M-->>V: verified — our text is now the last outgoing message
    V->>J: sent, confirmed

    B->>J: collects, pays
    J->>P: mark sold
    P->>R: status=sold
    R-->>V: sold ⇒ stop queueing replies
```

---

## 5. Failure contract

Each stage fails in a way the next stage can see, never silently.

| stage | on failure | what it must NOT do |
|---|---|---|
| `intake` | leaves the item folder marked incomplete | invent a transcription |
| `catalog` | refuses to emit a half-built record | write a partial item |
| `publishing` | reports the ad that failed, keeps the rest | claim a listing exists |
| `conversations` poll | one line to stderr, exit 2 | wake the agent or message a human |
| `conversations` send | reports **failed**, records why | auto-retry, or claim success |
| `registry` | fails open for messaging, closed for selling | crash both halves |

---

## 6. Adding a marketplace

One provider file per stage, never a change to the pipeline:

```
src/publishing/providers/<name>.py     # post an ad
src/conversations/providers/<name>.py  # read threads, send a message
```

`providers/base.py` in each defines the methods. Facebook is stubbed in
`conversations/providers/fb.py` with the traps documented inline (virtualised
inbox, per-load `fb_dtsg`/LSD tokens, hard login wall).
