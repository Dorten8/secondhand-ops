# Communication channels

Jakub's Telegram group is split into topics on purpose: bikes, gets, inbox, jobs,
todoes, personal, development. The split only works if every message lands in the
right one. It kept failing — not because the map was unknown, but because each
sender picked a thread at send time, and a choice can always be made wrong.

## The rule

**No agent picks a destination.** A message is posted through `bin/say` and the
destination is derived from what the message *is*.

```bash
bin/say --kind buyer_message --text-file /tmp/draft.md     # -> Inbox (13)
bin/say --kind shopping      --text "GPU dropped to 1.999" # -> Gets (10)
bin/say --kind academic_jobs --text "3 new PhD calls"      # -> Jobs (16)
bin/say --kind ops           --text "backup ok 12:00"      # -> logged, never posted
bin/say --list                                             # the map, read-only
bin/say --kind buyer_message --text "…" --dry-run          # resolve, send nothing
```

## What makes it drift-proof

1. **There is no default.** Omitting `--kind`/`--topic` is an error, not a
   fallback. A default is how buyer messages end up in Development.
2. **Unknown kinds are errors.** A new kind of message is added once to `ROUTES`
   in `src/shared/topics.py`, and every caller of that kind is then correct
   forever — nobody has to remember a thread id.
3. **`Personal` (44) is refused** unless `--allow-personal` is passed. It is the
   local-model lane; an agent posting there is a policy violation, not a routing
   accident.
4. **`ops` resolves to the `local` pseudo-topic** — the message is logged and not
   posted at all. Infrastructure output has no human value and should not be
   visible anywhere.
5. **Every delivery is audited** to `~/hermes/home/deliveries.jsonl`: timestamp,
   kind, resolved thread, size, message ids, and the result. A misroute can be
   found by reading the log instead of by asking an agent what it did.
6. **Over 4096 characters splits cleanly** on paragraph boundaries. Telegram's
   `400 message is too long` otherwise reads like an auth failure.

## The map

| kind | topic | thread |
|---|---|---|
| `buyer_message` · `marketplace_reply` · `draft_card` · `unsorted` | inbox | 13 |
| `shopping` · `price_watch` · `housing` | gets | 10 |
| `academic_jobs` | jobs | 16 |
| `task_capture` | todoes | 19 |
| `hermes_work` | development | 45 |
| `ops` · `backup` | *(local only)* | — |

Thread ids are not permanent: deleting and re-creating a topic changes them. If a
send fails with `message thread not found`, re-resolve the id by reading what was
actually said in that thread — never by guessing and never by trusting this table
blind.

## Adding a new kind

One line, in one file:

```python
# src/shared/topics.py
ROUTES["invoice_chase"] = "inbox"
```

Then `bin/say --kind invoice_chase` works everywhere, including in cron jobs and
scripts that know nothing about Telegram.

## Why not just let the agent choose

Because it already did, repeatedly, and the failure was silent: the message
arrived, looked fine, and was in the wrong place. A wrong send that succeeds is
worse than a refused send that fails loudly — the refusal is the feature.

## Choices in chat

Draft cards carry a reply keyboard with **three** buttons, each prefixed by the
draft id (`#7 3️⃣ Ignore`) so a chat-wide keyboard is never ambiguous:

| tap | what happens |
|---|---|
| `#N 1️⃣ Send as is` | the draft is sent verbatim, then verified against the marketplace |
| `#N 2️⃣ Send what I write` | waits for his text, then sends exactly that |
| `#N 3️⃣ Ignore` | closes the draft, nothing is sent |

**Anything else is typed, not tapped.** There is deliberately no "Other" button:
Telegram has no API that opens the input field, so such a button could only send a
message that costs a model turn to reply "what do you want?" — the same question
the placeholder already asks, for free. The freeform path is the input field's
placeholder text.

Inline (callback) buttons are not an option for scripts: the gateway's callback
dispatch only recognises its own prefixes (`cl:`, `ea:`, `sc:`…), so a script's
`callback_data` is accepted by Telegram and then silently ignored on tap — a
button that looks live and does nothing.
