#!/usr/bin/env python3
"""providers/fb.py — Facebook Marketplace, driven through the signed-in browser.

There is no usable API: Facebook's Marketplace messaging has no public endpoint for
personal accounts (the partner APIs are invitation-only and the Page inbox does not
carry Marketplace threads). So this reads the Messenger web app in a browser the
human already logged into — and, like every other provider here, **never logs in**.
If the session is gone it raises AuthError and waits for a human.

Verified against a live account on 2026-11-16:

  chat list    https://www.facebook.com/messages
  marketplace  In modern Messenger Comet web (MAW), Marketplace is a folder view
               in the sidebar (role="grid", aria-label="Marketplace").
               When expanding the Marketplace folder, each conversation row is
               rendered directly with an anchor tag:
                 <a href="/messages/t/<id>/">
                 aria-label="Group chat: <buyer> · <item_title>"
                 innerText="<buyer> · <item_title> [Unread message:] [<sender>:] <preview> · <when>"
               No synthetic mouse navigation across thread URLs is needed to discover
               threads — the full thread list (with IDs, buyer names, item titles,
               unread status, and timestamps) is read directly from the DOM folder list.
  thread url   /messages/t/<thread_id>
  bubbles      aria-label: "Message sent November 6, 2026, 12:53 PM by Andreas: <text>"
               and "... by You: <text>" — direction, sender and time are all in the
               label, so nothing is inferred from alignment or colour.
  composer     contenteditable whose aria-label is "Write to <buyer> · <listing>",
               which is how a Marketplace thread identifies its item.

Discovered threads are cached in `fb_threads.json` for resilience and fallback.
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
    def _open(self, url: str = MESSAGES_URL, wait: int = 6):
        s = Session(self.profile)
        cur = ""
        try:
            cur = s.eval("location.href") or ""
        except Exception:
            pass
        if cur.rstrip("/") != url.rstrip("/"):
            s.eval(f"location.href={json.dumps(url)}")
            time.sleep(wait)
            s.reset()
        return s

    @staticmethod
    def _logged_out(s) -> bool:
        return bool(s.json("""(() => JSON.stringify(
            !!document.querySelector('input[name="email"], form[action*="login"]')))()"""))

    def _ensure_marketplace_open(self, s, wait: float = 3.0) -> bool:
        """Ensure the Marketplace folder view is active in the sidebar.

        In Messenger Comet, Marketplace is a sidebar folder (`role="grid"` with
        `aria-label="Marketplace"`). If not yet open, clicking the root Marketplace
        entry expands the folder in-place without changing location.href away from /messages.
        """
        is_open = s.eval("""(() => {
            const g = document.querySelector('[role="grid"][aria-label="Marketplace"]');
            if (g && window.getComputedStyle(g).visibility !== 'hidden') return true;
            const a = document.querySelector('a[href*="/messages/t/"][aria-label*="·"]');
            return !!a;
        })()""")
        if is_open:
            return True

        try:
            s._call("Page.bringToFront", {}, session_id=s.session_id)
        except Exception:
            pass

        clicked = s.eval("""(() => {
            const rows = Array.from(document.querySelectorAll('[role="row"]'));
            const mp = rows.find(r => /marketplace/i.test(r.innerText || ''));
            if (!mp) return false;
            const btn = mp.querySelector('[role="button"]') || mp;
            btn.click();
            return true;
        })()""")
        if not clicked:
            return False

        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(0.3)
            has_grid = s.eval("""(() => {
                const g = document.querySelector('[role="grid"][aria-label="Marketplace"]');
                return !!(g && window.getComputedStyle(g).visibility !== 'hidden');
            })()""")
            if has_grid:
                return True
        return False

    def _extract_marketplace_threads(self, s) -> list[dict]:
        """Extract all conversation rows from the Marketplace folder in the sidebar."""
        js = """(() => {
            const norm = t => (t||'').replace(/\s+/g, ' ').trim();
            const anchors = Array.from(document.querySelectorAll('a[href*="/messages/t/"]'));
            const mp = anchors.filter(a => {
                const aria = a.getAttribute('aria-label') || '';
                const href = a.getAttribute('href') || '';
                return /\/messages\/t\/\d+/.test(href) && aria.includes('·');
            });
            return JSON.stringify(mp.map(a => ({
                href: a.getAttribute('href') || '',
                aria: a.getAttribute('aria-label') || '',
                text: norm(a.innerText)
            })));
        })()"""
        return s.json(js) or []

    # ---------------------------------------------------------------- interface
    def whoami(self) -> dict:
        with self._open() as s:
            if self._logged_out(s):
                raise AuthError("Facebook session is not signed in — a human must log in")
            return {"url": MESSAGES_URL, "logged_in": True}

    def list_conversations(self) -> list[Conversation]:
        """Marketplace threads, discovered directly from the sidebar folder view."""
        cache = _load_cache()
        out: list[Conversation] = []
        seen_ids: set[str] = set()

        with self._open() as s:
            if self._logged_out(s):
                raise AuthError("Facebook session is not signed in — a human must log in")

            self._ensure_marketplace_open(s)
            raw_threads = self._extract_marketplace_threads(s)

            for t in raw_threads:
                m_id = THREAD_RE.search(t.get("href", ""))
                if not m_id:
                    continue
                conv_id = m_id.group(1)
                if conv_id in seen_ids:
                    continue
                seen_ids.add(conv_id)

                aria = t.get("aria", "")
                text = t.get("text", "")

                clean_aria = re.sub(r"(?i)^group chat:\s*", "", aria)
                buyer, sep, item_title = clean_aria.partition(" · ")
                buyer = _norm(buyer)
                item_title = _norm(item_title)

                is_unread = bool(re.search(r"unread message|ulæst|new message", text, re.I))

                m_when = re.search(r"·\s*(\d+\s*[mhdw])$", text)
                when_tok = m_when.group(1) if m_when else ""

                preview = text
                if m_when:
                    preview = preview[:m_when.start()].strip()
                prefix = f"{buyer} · {item_title}"
                if preview.startswith(prefix):
                    preview = preview[len(prefix):].strip()
                elif " · " in preview:
                    parts = preview.split(" · ")
                    rem = " · ".join(parts[1:])
                    if rem.startswith(item_title):
                        preview = rem[len(item_title):].strip()

                preview = re.sub(r"(?i)^unread message:\s*", "", preview).strip()
                if any(s in preview.lower() for s in ("to help identify", "started this chat")):
                    preview = ""

                last_out = preview.lower().startswith("you:")

                cache[conv_id] = {"buyer": buyer, "item": item_title}

                out.append(Conversation(
                    conv_id=conv_id,
                    item_title=item_title,
                    partner_name=buyer,
                    last_preview=preview[:200],
                    last_outgoing=last_out,
                    unseen=1 if is_unread else 0,
                    last_at=_rel_iso(when_tok),
                    raw={"source": "dom", "href": t.get("href"), "raw_aria": aria, "raw_text": text},
                ))

            if cache:
                _save_cache(cache)

        if not out and cache:
            for conv_id, meta in cache.items():
                out.append(Conversation(
                    conv_id=conv_id,
                    item_title=meta.get("item", ""),
                    partner_name=meta.get("buyer", ""),
                    last_preview="",
                    last_outgoing=False,
                    unseen=0,
                    last_at="",
                    raw={"source": "cache_fallback"},
                ))

        return out

    def fetch_thread(self, conv_id: str) -> list[Message]:
        with self._open(THREAD_URL.format(conv=conv_id), wait=7) as s:
            if self._logged_out(s):
                raise AuthError("Facebook session is not signed in — a human must log in")
            labels = s.json("""(() => {
              const norm = t => (t||'').replace(/\s+/g,' ').trim();
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
                continue
            if any(s in body.lower() for s in ("to help identify", "started this chat")):
                continue
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
