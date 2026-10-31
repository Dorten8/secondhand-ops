# marketplace-messaging

A messaging lane for second-hand marketplace sellers: **read the inbox, draft a
reply, let a human approve it, send it, prove it left.**

Provider-agnostic by construction — DBA.dk is implemented, Facebook Marketplace is
a documented stub. Point it at any marketplace by writing one file.

> Companion repo: the **listing/registration** side lives elsewhere; the two meet
> at a single item registry. Read [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Why it exists

Selling on marketplaces is not the listing — it is the 40 conversations after it.
An agent can read them, but it must never send to a stranger unchecked, and it
must never claim a message went out when it did not. This repo is the small,
boring machinery that makes those two guarantees structural rather than hopeful.

## What it is not

- **Not an auto-responder.** Nothing reaches a buyer without an explicit approval.
- **Not a chatbot.** It drafts; a human decides; then it sends exactly what was
  approved.
- **Not a scraper.** It uses the marketplace's own JSON API where one exists.

---

## Quickstart

Requires Python 3.11+ (`requests`, `websockets`) and a browser you have logged
into the marketplace by hand, once.

```bash
git clone https://github.com/Dorten8/marketplace-messaging
cd marketplace-messaging/messaging

# 1. who you are on the marketplace (the id appears in the conversation URL)
cp ../examples/dba_account.example.json dba_account.json

# 2. start the browser with a debug port, log in by hand, then export cookies
$P msgctl.py auth
$P msgctl.py whoami

# 3. first run PRIMES (records history, queues nothing), then stays silent
$P msgctl.py poll
```

Then wire it to a timer. `scripts/msg_poll.sh` is the cron entry point: it prints
nothing when there is nothing to do, which is what keeps a scheduled agent quiet
and cheap.

### Daily use

```bash
msgctl.py pending              # buyer wrote, we owe a reply (JSON)
msgctl.py unanswered --days 14 # we wrote, they never answered
msgctl.py draft --conv <id> --body-file /tmp/b.txt --inbound "<their message>"
msgctl.py show --id 7          # exactly what the human sees, with the options
msgctl.py approve --id 7 --action send|ignore|supersede --by Jacob
```

---

## The four stages

| stage | cost | what it does |
|---|---|---|
| `poll.py` | **zero tokens** | one HTTP GET, queues threads where the buyer spoke last |
| draft | LLM | writes the reply, stores it as `pending` |
| `approve` | zero tokens | human gate, enforced in code |
| `send` | zero tokens | sends, then **verifies via the API** |

Only drafting costs tokens. Everything else is a script.

## Guarantees (tests, not promises)

- **Approval gate in code.** A draft leaves `pending` exactly once. Deciding twice
  is refused — that is what makes a retry unable to double-send.
- **Verified delivery.** After sending, the lane re-reads the marketplace and
  confirms the message is the last outgoing one. If it cannot prove that, it
  reports failure.
- **No auto-retry on unverified send.** A retry can duplicate a message to a real
  person, so it stops and asks a human instead.
- **Idempotent polling.** Stable content hashes, so a message never queues twice.
- **Prime on first run.** History is never announced as news.
- **No login automation, ever.** Session expiry raises and waits for a human.

---

## Adding a marketplace

One file: `messaging/providers/<name>.py`, implementing six methods from
`base.py` — `list_conversations`, `fetch_thread`, `send`, `whoami`, `auth_ok`.
Nothing else in the lane changes. See `providers/fb.py` for the Facebook notes.

The read side of `providers/dba.py` is a good template: authenticated HTTP against
the marketplace's own API, no DOM, no scraping.

---

## Status

- ✅ DBA: read via JSON API (verified), send via DOM + API-verified delivery
- ⏳ DBA: send endpoint not yet captured — `capture_post.py` will catch it
- ⏳ DBA: full transcript endpoint not yet captured (store accumulates history)
- ⏳ Facebook: stub with implementation notes

Details and the "things that are not true" list: [`docs/DBA-API.md`](docs/DBA-API.md).

## License

MIT.
