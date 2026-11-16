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
from datetime import datetime, timedelta
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


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _label_iso(tok: str) -> str:
    """Bubble labels read "November 6, 2026, 1:06 PM" or "Sunday 9:36pm".

    poll's age filter wants an ISO stamp. Anything unparseable returns "" — poll
    treats an unknown age as not stale, which is the safe direction (better to show
    a thread than to hide it).
    """
    t = re.sub(r"\s+", " ", _norm(tok).replace(",", " ")).strip()
    if not t:
        return ""
    for fmt in ("%B %d %Y %I:%M %p", "%B %d %Y %H:%M", "%b %d %Y %I:%M %p",
                "%d %B %Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass

    now = datetime.now()
    low = t.lower()
    day = None
    if low.startswith("yesterday"):
        day = (now - timedelta(days=1)).date()
        t = t[len("yesterday"):].strip()
    else:
        m = re.match(r"(%s)\b\s*(.*)$" % "|".join(WEEKDAYS), low)
        if m:
            back = (now.weekday() - WEEKDAYS.index(m.group(1))) % 7 or 7
            day = (now - timedelta(days=back)).date()
            t = m.group(2).strip()
    tm = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", t, re.I)
    if not tm:
        return ""
    hh, mm = int(tm.group(1)), int(tm.group(2))
    ap = (tm.group(3) or "").lower()
    if ap == "pm" and hh < 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0
    d = day or now.date()
    return datetime(d.year, d.month, d.day, hh, mm).strftime("%Y-%m-%dT%H:%M:%S")


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
            for r in market_rows:
                m = re.search(r"(\d+)\s+new message", r["text"], re.I)
                unread = max(unread, int(m.group(1)) if m else 0)

            # resolve any thread we have never seen (bounded: 3 per poll)
            unknown = market_rows and not cache
            if unknown:
                resolved = self._resolve_row(s, market_rows[0])
                if resolved:
                    cache[resolved["conv_id"]] = {"buyer": resolved["buyer"],
                                                  "item": resolved["item"]}
                    _save_cache(cache)

        # The list cannot say what a thread says. "Marketplace" is ONE aggregate row
        # ("Marketplace 1 new message · 14h"); using its text as every thread's preview
        # queued all 24 cached threads with the same meaningless payload ("1 new
        # message") and no real text — a full false queue (observed 2026-11-16). Only
        # the thread knows, so read the threads — and only when the aggregate row
        # actually reports something new, so an idle poll never drives the browser.
        # A thread we know but have never read has no baseline: without one, the first
        # poll after seeding would report an empty preview and the lane would look dead
        # even though the buyer is waiting. So refresh when the aggregate row is unread
        # OR when any known thread still lacks a recorded last message.
        needs_baseline = any(not meta.get("last") for meta in cache.values())
        if unread or needs_baseline:
            cache = self._refresh_last(cache)

        for conv_id, meta in cache.items():
            last = meta.get("last") or {}
            out.append(Conversation(
                conv_id=conv_id,
                item_title=meta.get("item", ""),
                partner_name=meta.get("buyer", ""),
                last_preview=last.get("body", ""),
                last_outgoing=last.get("direction") == "out",
                unseen=unread,
                last_at=last.get("at_iso", ""),
                raw={"source": "thread"},
            ))
        return out

    def _refresh_last(self, cache: dict, cap: int = 30) -> dict:
        """Record each known thread's newest bubble in the cache.

        One page load per thread, so this runs only when the Marketplace row reports
        an unread message. A thread that fails to load keeps its previous entry — a
        single dead thread must not take the poll down with it.
        """
        for conv_id, meta in list(cache.items())[:cap]:
            try:
                msgs = self.fetch_thread(conv_id)
            except Exception:
                continue
            if not msgs:
                continue
            last = msgs[-1]
            meta["last"] = {"body": last.body, "direction": last.direction,
                            "at": last.at, "at_iso": _label_iso(last.at)}
        _save_cache(cache)
        return cache

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

    def send(self, conv_id: str, body: str, via: str = "dom", dry_run: bool = False) -> dict:
        """Type, press Enter, then require the SENT BUBBLE to carry our text.

        What actually has to happen (proven 2026-11-16, after a day of "it typed
        but never left"): the tab must be frontmost, and the composer must be
        focused with a REAL mouse click. `element.focus()` is not enough — Enter
        dispatched into an unfocused page does nothing, and the text just sits in
        the box looking sent. Verification reads the message bubbles, never
        innerText (unsent composer text appears there too).
        """
        marker = _norm(body)[:40]
        sel = '[role=textbox][aria-label^="Write to"]'
        with self._open(THREAD_URL.format(conv=conv_id), wait=8) as s:
            s._call("Page.bringToFront", {}, session_id=s.session_id)
            time.sleep(1)
            box = s.json("""(() => {const b=document.querySelector(%s);
              if(!b) return JSON.stringify({err:'no-composer'});
              b.scrollIntoView({block:'center'});
              const r=b.getBoundingClientRect();
              return JSON.stringify({x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
                                     label:(b.getAttribute('aria-label')||'')});})()"""
                          % json.dumps(sel)) or {}
            if box.get("err"):
                raise ProviderError("composer not found — the thread did not load")
            for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
                s._call("Input.dispatchMouseEvent",
                        {"type": kind, "x": box["x"], "y": box["y"],
                         "button": "left", "clickCount": 1}, session_id=s.session_id)
            time.sleep(0.6)
            active = s.eval("""(() => {const a=document.activeElement;
              return a.tagName + '|' + (a.getAttribute('aria-label')||'').slice(0,24);})()""")
            if "Write to" not in (active or ""):
                raise ProviderError(f"composer did not take focus (active: {active})")
            s._call("Input.insertText", {"text": body}, session_id=s.session_id)
            time.sleep(1.5)
            typed = s.eval("""(() => {const b=document.querySelector(%s);
              return b ? (b.innerText||'').trim().length : -1;})()""" % json.dumps(sel))
            if not isinstance(typed, int) or typed < 5:
                raise ProviderError(f"composer did not take the text (len={typed})")
            if dry_run:
                s.eval("""(() => {const b=document.querySelector(%s); b.focus();
                  const sel=window.getSelection(), r=document.createRange();
                  r.selectNodeContents(b); sel.removeAllRanges(); sel.addRange(r);
                  document.execCommand('delete'); return 1;})()""" % json.dumps(sel))
                return {"ok": False, "dry_run": True, "typed_chars": typed}
            for kind in ("rawKeyDown", "char", "keyUp"):
                s._call("Input.dispatchKeyEvent",
                        {"type": kind, "key": "Enter", "code": "Enter", "text": "\r",
                         "unmodifiedText": "\r", "windowsVirtualKeyCode": 13,
                         "nativeVirtualKeyCode": 13}, session_id=s.session_id)
            time.sleep(6)
            left = s.eval("""(() => {const b=document.querySelector(%s);
              return b ? (b.innerText||'').trim().length : -1;})()""" % json.dumps(sel))
        last = None
        for _ in range(5):
            msgs = self.fetch_thread(conv_id)
            if msgs:
                last = msgs[-1]
                if last.direction == "out" and marker in last.body:
                    return {"ok": True, "verified": True, "via": "dom",
                            "composer_left": left, "preview": last.body[:80]}
            time.sleep(3)
        raise ProviderError("pressed Enter but the thread does not show our text "
                            f"(last: {last.body[:60] if last else 'none'!r})")


Facebook = FacebookProvider  # historical alias
