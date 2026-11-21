# Ownership — who is responsible for an item, and when

Publishing is a task. Owning an item is a responsibility. They are not the same and
they do not transfer at the same moment.

## 1. The rule

- **Anyone may create and publish.** The operator, a listing agent, another harness —
  whoever has the credentials may run the whole pipeline end to end. Nothing here
  requires permission from the other side.
- **Once a row exists in the database, the managing agent owns that item until it is
  `SOLD`.** The row appearing *is* the handover. There is no ceremony, no message, no
  acknowledgement to exchange.

The practical consequence: a publisher that walks away after `LISTED` has not finished.
An item with no owner is the failure mode this rule exists to prevent — it sits listed,
never gets delisted, never gets marked sold, and its buyer messages go unanswered.

## 2. What "owning it" means, concretely

For every row, on a schedule:

| State | The owner must |
|---|---|
| `UNLISTED` | make sure it is publishable: ad text present, photos attached, price set — or send it back with one line saying what is missing |
| `LISTED` | confirm the ad is actually live; keep the price correct; close it when it sells |
| `SOLD` | record `Sold for (DKK)` and `Sold date`; delist any duplicates; nothing else |

And in every state: **answer the buyers.** Buyer replies for an owned item belong in the
operator's production chat, not in the engineering channel.

## 3. Escalate, do not silently guess

The owner may act on: publishing, delisting, marking sold, correcting a price the
operator already recorded, writing the marketplace id and URL back.

The owner may **not** invent: a price, a description's facts, a condition, a
provenance. If those are missing, the row goes back with one explicit line naming the
missing field. A wrong price on a real listing costs money; a question costs a minute.

## 4. The mechanism

`bin/ssot_status.py` — read-only report over the sales database:

```bash
python3 bin/ssot_status.py                  # state per item + flags
python3 bin/ssot_status.py --json           # machine readable
python3 bin/ssot_status.py --stale-days 14  # what counts as stagnant
```

It flags the specific ways an owned item goes wrong: `LISTED` with no ad URL,
`UNLISTED` with no ad text, no photos, no price, and listed-but-untouched for N days.

Run it on a schedule; when there is nothing to say it prints one line and exits 0, so
it is safe to attach to a watchdog that only speaks on change.
