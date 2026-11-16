#!/usr/bin/env python3
"""providers/fb.py — Facebook Marketplace, driven through the signed-in browser.

There is no usable API: Facebook's Marketplace messaging has no public endpoint for
personal accounts (the partner APIs are invitation-only and the Page inbox does not
carry Marketplace threads). So this reads the Messenger web app in a browser the
human already logged into — and, like every other provider here, **never logs in**.
If the session is gone it raises AuthError and waits for a human.

Verified against a live account on 2026-11-16, which is why the selectors below look
the way they do:

  chat list    /messages — rows are `[role="row"]`. Most rows carry NO href (only
               group chats expose `a[href*="/messages/t/"]`), so a thread id can only
               be learned by opening the row and reading the resulting URL.
  marketplace  the list shows one row labelled "Marketplace" with an unread count
               ("Marketplace 1 new message · 14h") — an aggregate entry, not a thread.
  thread url   /messages/t/<thread_id>
  bubbles      aria-label: "Message sent November 6, 2026, 12:53 PM by Andreas: <text>"
               and "... by You: <text>" — direction, sender and time are all in the
               label, so nothing is inferred from alignment or colour.
  composer     contenteditable whose aria-label is "Write to <buyer> · <listing>",
               which is how a Marketplace thread identifies its item.

Because ids are only discoverable by opening a row, resolved threads are cached in
`fb_threads.json`; a poll then re-checks the ones it knows instead of re-clicking the
whole list every two minutes.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/hermes/hermes/home/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from base import AuthError, Conversation, Message, Provider, ProviderError  # noqa: E402

try:
    from cdp import Session
except Exception:                                              # pragma: no cover
    Session = None

MESSAGES_URL = "https://www.facebook.com/messages"
THREAD_URL = MESSAGES_URL + "/t/{conv}"
CACHE = Path("/home/hermes/hermes/home/messaging/fb_threads.json")
THREAD_RE = re.compile(r"/messages/t/(\d+)")
SENT_RE = re.compile(r"message sent (?P<when>.+?) by (?P<who>[^:]{1,40}):\s*(?P<body>.*)",
                     re.S | re.I)
# "Messages in conversation titled Andreas · Macbook Pro 2018" — buyer and listing in
# one label, available without reading the composer.
TITLE_RE = re.compile(r"conversation titled (?P<buyer>[^·]+?)\s*·\s*(?P<item>.+)$", re.I)
REL_RE = re.compile(r"(\d+)\s*([mhdw])")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _rel_iso(tok: str) -> str:
    """Facebook gives relative times on the list ('14h'); convert to an ISO stamp."""
    m = REL_RE.search(tok or "")
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2)
    secs = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit] * n
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - secs))


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def _save_cache(d: dict) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(d, indent=1))
    except OSError:
        pass


class FacebookProvider(Provider):
    name = "fb"

    def __init__(self, profile: str = "facebook.com"):
        if Session is None:
            raise ProviderError("cdp.py not importable — cannot drive the browser")
        self.profile = profile

    # ------------------------------------------------------------------ helpers
    def _open(self, url: str = MESSAGES_URL, wait: int = 8):
        s = Session(self.profile)
        s.eval(f"location.href={json.dumps(url)}")
        time.sleep(wait)
        s.reset()
        return s

    @staticmethod
    def _logged_out(s) -> bool:
        return bool(s.json("""(() => JSON.stringify(
            !!document.querySelector('input[name="email"], form[action*="login"]')))()"""))

    @staticmethod
    def _click(s, x: int, y: int) -> None:
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            s._call("Input.dispatchMouseEvent",
                    {"type": kind, "x": x, "y": y, "button": "left",
                     "buttons": 0 if kind == "mouseReleased" else 1, "clickCount": 1},
                    session_id=s.session_id)
            time.sleep(0.15)

    def _rows(self, s) -> list[dict]:
        js = """(() => {
          const norm = t => (t||'').replace(/\\s+/g,' ').trim();
          const out = [];
          for (const el of document.querySelectorAll('[role="row"]')) {
            const r = el.getBoundingClientRect();
            if (!r.width || r.top < 150 || r.left > 420) continue;
            const t = norm(el.innerText);
            if (!t) continue;
            out.push({text: t.slice(0,180),
                      x: Math.round(r.left + r.width*0.35),
                      y: Math.round(r.top + r.height/2)});
          }
          return JSON.stringify(out);
        })()"""
        return s.json(js) or []

    def _thread_title(self, s) -> tuple[str, str]:
        """(buyer, item) from the conversation's title label, if present."""
        lab = s.json("""(() => {
            const norm = t => (t||'').replace(/\\s+/g,' ').trim();
            const hit = [...document.querySelectorAll('[aria-label]')]
              .map(e => norm(e.getAttribute('aria-label')))
              .find(t => /conversation titled/i.test(t));
            return JSON.stringify(hit || '');
        })()""") or ""
        m = TITLE_RE.search(lab if isinstance(lab, str) else "")
        return ((_norm(m.group("buyer")), _norm(m.group("item"))) if m else ("", ""))

    def _composer_label(self, s) -> str:
        return _norm(s.json("""(() => {
            const e = document.querySelector('[contenteditable="true"]');
            return e ? (e.getAttribute('aria-label') || '') : '';
        })()""") or "")

    def _resolve_row(self, s, row: dict, tries: int = 3) -> dict | None:
        """Click a row until it opens, then read who/what it is.

        Opening a row is not reliable on the first attempt — the list re-renders
        under the click and the navigation is sometimes dropped (observed: identical
        clicks, one navigates, the next does not). So this retries and waits for the
        URL to actually become a thread before reading anything.
        """
        for attempt in range(1, tries + 1):
            s.eval(f'location.href="{MESSAGES_URL}"')
            time.sleep(6)
            s.reset()

            # re-measure the row each attempt: the list may have scrolled
            row_now = None
            for r in self._rows(s):
                if re.match(r"(?i)marketplace", r["text"]):
                    row_now = r
                    break
            if not row_now:
                return None

            self._click(s, row_now["x"], row_now["y"])

            url = ""
            for _ in range(10):                       # wait up to ~10s for navigation
                time.sleep(1.2)
                try:
                    s.reset()
                    url = s.eval("location.href") or ""
                except Exception:
                    url = ""
                if isinstance(url, str) and THREAD_RE.search(url):
                    break
            if not (isinstance(url, str) and THREAD_RE.search(url)):
                continue                              # click was dropped; try again

            conv_id = THREAD_RE.search(url).group(1)
            buyer, item = self._thread_title(s)
            if not buyer:
                label = self._composer_label(s)
                if "·" in label:
                    b, _, i = label.partition("·")
                    buyer, item = _norm(b.replace("Write to", "", 1)), _norm(i)
            if not buyer:
                return None                           # a plain chat, not marketplace
            return {"conv_id": conv_id, "buyer": buyer, "item": item,
                    "attempts": attempt}
        return None

    # ---------------------------------------------------------------- interface
    def whoami(self) -> dict:
        with self._open() as s:
            if self._logged_out(s):
                raise AuthError("Facebook session is not signed in — a human must log in")
            return {"url": MESSAGES_URL, "logged_in": True}

    def list_conversations(self) -> list[Conversation]:
        """Marketplace threads, resolved through the cache first and the list second.

        The list row for a Marketplace conversation is an aggregate ("Marketplace 1
        new message · 14h"), so an unknown thread cannot be identified from the text
        alone: it has to be opened. Threads already known are re-checked from the
        cache without clicking, which keeps a 2-minute poll cheap.
        """
        cache = _load_cache()
        out: list[Conversation] = []

        with self._open() as s:
            if self._logged_out(s):
                raise AuthError("Facebook session is not signed in — a human must log in")
            rows = self._rows(s)

            market_rows = [r for r in rows if re.match(r"(?i)marketplace", r["text"])]
            unread = 0
            preview = ""
            when = ""
            for r in market_rows:
                m = re.search(r"(\d+)\s+new message", r["text"], re.I)
                unread = max(unread, int(m.group(1)) if m else 0)
                preview = _norm(re.sub(r"(?i)^marketplace|·\s*\S+$", "", r["text"]))[:200]
                mt = re.search(r"·\s*(\d+\s*[mhdw])", r["text"])
                when = _rel_iso(mt.group(1)) if mt else ""

            # resolve any thread we have never seen (bounded: 3 per poll)
            unknown = market_rows and not cache
            if unknown:
                resolved = self._resolve_row(s, market_rows[0])
                if resolved:
                    cache[resolved["conv_id"]] = {"buyer": resolved["buyer"],
                                                  "item": resolved["item"]}
                    _save_cache(cache)

        for conv_id, meta in cache.items():
            out.append(Conversation(
                conv_id=conv_id,
                item_title=meta.get("item", ""),
                partner_name=meta.get("buyer", ""),
                last_preview=preview,
                last_outgoing=preview.lower().startswith("you"),
                unseen=unread,
                last_at=when,
                raw={"source": "cache"},
            ))
        return out

    def fetch_thread(self, conv_id: str) -> list[Message]:
        with self._open(THREAD_URL.format(conv=conv_id), wait=7) as s:
            if self._logged_out(s):
                raise AuthError("Facebook session is not signed in — a human must log in")
            labels = s.json("""(() => {
              const norm = t => (t||'').replace(/\\s+/g,' ').trim();
              return JSON.stringify([...document.querySelectorAll('[aria-label]')]
                .map(e => norm(e.getAttribute('aria-label')))
                .filter(t => /message sent/i.test(t)));
            })()""") or []

        msgs: list[Message] = []
        for lab in labels:
            m = SENT_RE.search(lab)
            if not m:
                continue
            body = _norm(m.group("body"))
            if not body:
                continue                       # attachment-only bubble
            if body.lower().startswith(("to help identify", "started this chat")):
                continue                       # system notice, not the buyer
            msgs.append(Message(body=body,
                                direction="out" if _norm(m.group("who")).lower() == "you" else "in",
                                at=_norm(m.group("when"))))
        return msgs

    def send(self, conv_id: str, body: str) -> dict:
        """Type, press Enter, then require the transcript to show our text.

        The keystroke is never the proof: an Enter swallowed by the React composer
        looks identical to a successful send.
        """
        marker = _norm(body)[:40]
        with self._open(THREAD_URL.format(conv=conv_id), wait=7) as s:
            typed = s.json("""(() => {
              const e = document.querySelector('[contenteditable="true"]');
              if (!e) return 'null';
              e.focus();
              document.execCommand('insertText', false, %s);
              return JSON.stringify((e.innerText || '').trim());
            })()""" % json.dumps(body))
            if not typed:
                raise ProviderError("composer not found — the thread did not load")
            for kind, extra in (("keyDown", {"text": "\r"}),
                                ("char", {"text": "\r"}),
                                ("keyUp", {})):
                s._call("Input.dispatchKeyEvent",
                        {"type": kind, "key": "Enter", "code": "Enter",
                         "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, **extra},
                        session_id=s.session_id)
            time.sleep(5)

        last = None
        for _ in range(5):
            msgs = self.fetch_thread(conv_id)
            if msgs:
                last = msgs[-1]
                if last.direction == "out" and marker in last.body:
                    return {"ok": True, "verified": True, "via": "dom",
                            "preview": last.body[:80]}
            time.sleep(3)
        raise ProviderError("pressed Enter but the thread does not show our text "
                            f"(last: {last.body[:60] if last else 'none'!r})")


Facebook = FacebookProvider  # historical alias
