# DBA's private API — what is known, what is missing

Everything here was measured against a logged-in session on 2026-10-31, not
guessed. Keep it accurate: it is the reason this lane needs no scraping.

Base: `https://www.dba.dk`
Auth: cookies only (`dba.dk` + `vend.dk` domains). No API key, no OAuth.
Login id: found in the URL of the conversation endpoint, e.g. `1880114669`.

---

## 1. List conversations — ✅ verified, in use

```
GET /messages/api/conversations/users/{loginId}/conversationgroups
Accept: application/json
Referer: https://www.dba.dk/messages/
```

Returns an array of **groups**, one per ad:

```json
[{
  "groupBasis": {
    "itemInfo": { "itemId": 18961393, "title": "...", "imageUrl": "...", "isOwner": true,
                  "url": "/my-items/details/18961393", "status": null }
  },
  "totalUnseenCounter": 0,
  "totalNumberOfConversations": 2,
  "lastMessagePreview": "...",
  "lastMessageDate": "2026-10-30T11:20:52.239384",
  "conversations": [{
    "conversationId": "L6fJPbic...",
    "itemId": 18961393,
    "partnerId": 19448886,
    "partnerName": "...",
    "partnerProfilePictureUrl": "...",
    "lastMessagePreview": ") nej )",
    "lastMessageDate": "2026-10-30T11:20:52.239384",
    "lastMessageOutgoing": true,
    "lastMessageAttachmentsCount": 0
  }]
}]
```

**The two fields that carry the whole design:**

- `totalUnseenCounter` — per group unread count. This *is* the notification.
- `lastMessageOutgoing` — `true` means **we** sent the last message, so the ball
  is with the buyer. Removes all need for "who said what" DOM heuristics.

⚠️ `itemId` appears twice with **different values**: `groupBasis.itemInfo.itemId`
(18961393 — the seller-side / my-items id) and `conversations[].itemId`. Use
`itemInfo.itemId` for ad identity and keep the conversation id for replies.

---

## 2. Conversation metadata — ✅ verified

```
GET /messages/api/conversations/users/{loginId}/conversations/{conversationId}
```

Returns conversation fields (`partnerName`, `lastMessagePreview`,
`lastMessageOutgoing`, `pageHash`, `partnerShopPageUrl`) but **`messages: []`**.
The transcript is loaded by a separate call that has not been captured yet.

---

## 3. Send a message — ✅ CAPTURED 2026-10-31

Observed on a real send (network watched while the message left):

```
POST /messages/api/conversations/users/{loginId}/conversations/{conversationId}/messages
Content-Type: application/json

{"clientMessageId": "<uuid4>",
 "body": "text, newlines are \n",
 "messageType": "textMessage",
 "sent": "2026-10-31T14:30:55.076",
 "attachments": []}
```

**Auth is cookies only** — no CSRF token, no `fb_dtsg`-style per-load token. That is
why sending can be HTTP-only, with no browser at all.

`messaging/providers/dba.py` has `send_http()` wired to this, and `send(via="http")`
to use it. Default is still `via="dom"` until the HTTP path has done one verified
send in production.

### Three things that must be true before you believe a send happened

1. **A synthetic `.click()` is not trusted.** DBA's SPA ignores it — the URL
   changes and the conversation pane never hydrates, so every open silently
   "fails". Dispatch a real mouse event at the element's coordinates
   (`Input.dispatchMouseEvent`).
2. **The composer has no placeholder any more.** Match
   `textarea[aria-label^="Skriv din besked"]`. With the old
   `placeholder="Skriv en besked..."` selector, the composer is never found.
3. **A 200 is not proof.** Re-read `conversationgroups` and require
   `lastMessageOutgoing == true` **and** our text in `lastMessagePreview`.
   Anything less is reported as failed, and never retried automatically.

## 4. Endpoints seen in the app but not investigated

Captured from the network while browsing, not yet called:

- `/messages/api/conversations/users/{loginId}/blocks/{partnerId}` — blocking
- `/messages/api/listing` — listing lookup

---

## 5. Things that are NOT true

Recorded so nobody re-derives them:

- **`/messages/api/me` does not exist** — returns the SPA HTML, HTTP 200. Do not
  treat a 200 as proof of a JSON API; check `content-type`.
- **The login id cannot be guessed.** A plausible-looking 10-digit id from page
  HTML returned `401`. Only the id in the conversation endpoint URL works.
- **Push notifications are not a shortcut.** `Notification.permission` is
  `default` and there is no `pushManager` subscription, so nothing is delivered;
  and a WebPush payload is encrypted to a browser-held key that CDP cannot read.
- **`hash()` is not a stable dedupe key.** Python salts it per process; the first
  version re-inserted every message on every run. Use `hashlib`.
- **A 40 s silent hang is not a slow page.** It was a detached CDP session. Any
  client here needs a `recv` timeout so it fails loudly.
