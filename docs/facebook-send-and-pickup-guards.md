# Facebook send path + pickup guards (verified 2026-11-16)

## Two silent traps in the Messenger composer

1. **`document.execCommand('insertText')` types nothing and returns success.** Read back the
   composer and it is empty while the send "worked". Text must go in through CDP:
   `Input.insertText` on the focused box, then read `innerText` back and refuse to press Enter
   if it holds fewer than ~5 characters. `providers/fb.py::send()` does this and raises
   `ProviderError("composer rejected the text")` otherwise.
2. **There is no Send button.** The composer shows a hint, `Press enter to send`. A loose
   `[aria-label*="Send"]` selector matches that hint and clicks nothing useful — match
   `button[aria-label="Send"]` / `[role="button"][aria-label="Send"]` only, and treat a
   missing control as "Enter is the only path".

Find the composer by label (`Write to <buyer> · <listing>`, Danish: `Skriv …`), never by
`querySelector('[contenteditable]')` — that grabs the first editable element on the page and
produced the report "composer not reachable" while the real box was visible.

Enter is dispatched as `keyDown` + `char` with `text="\r"` + `keyUp`, then the transcript is
re-read: an Enter swallowed by React looks exactly like a successful send, so the **thread**
is the only proof.

## Pickup details are gated by code now

`msgctl draft` refuses a body containing `PICKUP_MAP` or `PICKUP_PHONE` unless the buyer's
message contains an agreed time (`kl. 19`, `19:00`, `i morgen`, `today`…). Same input shape:

```
REFUSED: the draft contains the pickup map/phone but nothing in the buyer's message agrees a time.
Address only after a specific day + time.
```

Written after a reply to a buyer who was walking away on price ended with the map link and the
pickup postcode. The skill already said not to do this; the rule now does not depend on
remembering it.

## The card queue can starve

One card at a time is deliberate (one reply keyboard per chat), but an unanswered card used to
block every other buyer indefinitely — eight real buyer messages sat undelivered behind one
ignored card. `msgctl notify` now releases the gate after `MSG_CARD_TTL_HOURS` (default 3h) and
says so on stderr; the old card stays pending and decidable. Every card also ends with
`↳ N more waiting: #95 #96 …` so a queue is never invisible again.
