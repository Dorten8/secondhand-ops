#!/usr/bin/env python3
"""providers/dba.py — DBA.dk provider.

READ  : pure HTTP against DBA's own JSON API, using cookies exported from the
        logged-in Brave profile. No browser, no DOM, no scraping.
        Endpoint (verified 2026-10-31):
          GET /messages/api/conversations/users/<loginId>/conversationgroups
SEND  : the POST endpoint has not been observed yet, so sending goes through
        the DOM over CDP (type + Enter). Delivery is then verified back through
        the JSON API — proof comes from DBA, not from the click.

Why this split matters: reading is the hot path (polled on a timer) and is now
free and browser-free. Writing is rare, human-approved, and can keep using the
browser until the POST endpoint is captured.

AUTH
  Cookies live in dba_cookies.json next to this file. `refresh_cookies()` pulls
  them from Brave over CDP. If the session dies, AuthError is raised and the
  caller must involve the human — never auto-login.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, "/home/hermes/hermes/home/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from base import (AuthError, Conversation, Message, Provider, ProviderError)  # noqa: E402

HERE = Path(__file__).resolve().parent
HERMES_HOME = Path("/home/hermes/hermes/home")
COOKIE_FILE = HERMES_HOME / "messaging" / "dba_cookies.json"
ACCOUNT_FILE = HERMES_HOME / "messaging" / "dba_account.json"
BASE = "https://www.dba.dk"
CDP_HTTP = "http://127.0.0.1:9222"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/155.0.0.0 Safari/537.36")

# DBA renamed the composer: the placeholder is gone, the aria-label is the stable
# hook ("Skriv din besked, og tryk på Enter for at sende" = type, press Enter to send).
COMPOSER_SEL = 'textarea[aria-label^="Skriv din besked"]'
# Old placeholder, kept only so a page change can be detected instead of silently
# failing to find the composer.
LEGACY_COMPOSER_SEL = 'textarea[placeholder="Skriv en besked..."]'


# --------------------------------------------------------------------------- auth
def refresh_cookies() -> int:
    """Export Brave's cookies over CDP. Requires Brave on 127.0.0.1:9222."""
    import urllib.request
    from websockets.sync.client import connect

    try:
        ver = json.load(urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=8))
    except Exception as exc:
        raise AuthError(f"cannot reach Brave on {CDP_HTTP}: {exc}") from exc
    ws = connect(ver["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024, open_timeout=15)
    try:
        ws.send(json.dumps({"id": 1, "method": "Storage.getCookies", "params": {}}))
        while True:
            msg = json.loads(ws.recv(timeout=20))
            if msg.get("id") == 1:
                cookies = (msg.get("result") or {}).get("cookies") or []
                break
    finally:
        ws.close()

    keep = [c for c in cookies
            if any(d in (c.get("domain") or "") for d in ("dba.dk", "vend.dk"))]
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps(keep, indent=1))
    COOKIE_FILE.chmod(0o600)
    return len(keep)


def _load_cookies() -> list[dict]:
    if not COOKIE_FILE.exists():
        raise AuthError(f"no cookies at {COOKIE_FILE} — run: msgctl auth")
    return json.loads(COOKIE_FILE.read_text())


def _account() -> dict:
    if ACCOUNT_FILE.exists():
        return json.loads(ACCOUNT_FILE.read_text())
    return {}


class DBA(Provider):
    name = "dba"

    def __init__(self, login_id: str | None = None):
        self.login_id = login_id or _account().get("login_id")
        if not self.login_id:
            raise AuthError(
                "DBA login_id unknown — put {\"login_id\": \"<id>\"} in "
                f"{ACCOUNT_FILE} (find it in the conversationgroups URL while logged in)")
        self._sess: requests.Session | None = None

    # ---------- plumbing ----------
    def session(self) -> requests.Session:
        if self._sess is not None:
            return self._sess
        s = requests.Session()
        s.headers.update({
            "accept": "application/json",
            "user-agent": UA,
            "referer": f"{BASE}/messages/",
            "x-requested-with": "XMLHttpRequest",
        })
        for c in _load_cookies():
            try:
                s.cookies.set(c["name"], c["value"],
                              domain=c.get("domain"), path=c.get("path") or "/")
            except Exception:
                continue
        self._sess = s
        return s

    def _get(self, path: str):
        r = self.session().get(f"{BASE}{path}", timeout=25)
        if r.status_code in (401, 403):
            raise AuthError(f"DBA returned {r.status_code} for {path} — session expired")
        if r.status_code != 200:
            raise ProviderError(f"DBA {r.status_code} for {path}")
        ctype = r.headers.get("content-type") or ""
        if "json" not in ctype:
            raise AuthError(f"expected JSON from {path}, got {ctype} — session likely expired")
        return r.json()

    # ---------- Provider API ----------
    def list_conversations(self) -> list[Conversation]:
        data = self._get(
            f"/messages/api/conversations/users/{self.login_id}/conversationgroups")
        out: list[Conversation] = []
        for group in data:
            info = (group.get("groupBasis") or {}).get("itemInfo") or {}
            unseen_total = int(group.get("totalUnseenCounter") or 0)
            convs = group.get("conversations") or []
            for i, c in enumerate(convs):
                out.append(Conversation(
                    conv_id=c.get("conversationId") or "",
                    item_id=str(info.get("itemId") or c.get("itemId") or ""),
                    item_title=info.get("title") or "",
                    partner_name=c.get("partnerName") or "",
                    partner_id=str(c.get("partnerId") or ""),
                    last_preview=c.get("lastMessagePreview") or "",
                    # lastMessageOutgoing True == WE spoke last
                    last_outgoing=bool(c.get("lastMessageOutgoing")),
                    unseen=unseen_total if i == 0 else 0,
                    sold=str(info.get("status") or "").upper() in ("SOLGT", "SOLD", "SOLGT_ANDET"),
                    last_at=str(c.get("lastMessageDate") or ""),
                    raw={"group": group.get("groupBasis"), "conv": c},
                ))
        return out

    def fetch_thread(self, conv_id: str) -> list[Message]:
        """DBA loads the transcript separately from the thread metadata. Until the
        messages endpoint is captured, this returns what the metadata gives us:
        the last message. Callers should treat a short thread as partial."""
        data = self._get(
            f"/messages/api/conversations/users/{self.login_id}/conversations/{conv_id}")
        out = []
        if data.get("lastMessagePreview"):
            out.append(Message(body=data["lastMessagePreview"],
                               direction="out" if data.get("lastMessageOutgoing") else "in",
                               at=str(data.get("lastMessageDate") or "")))
        return out

    def whoami(self) -> dict:
        data = self._get(
            f"/messages/api/conversations/users/{self.login_id}/conversationgroups")
        return {"login_id": self.login_id, "groups": len(data),
                "unseen": sum(int(g.get("totalUnseenCounter") or 0) for g in data)}

    # ---------- send (DOM via CDP, verified via API) ----------
    # ---------- verification (shared by both send paths) ----------
    def _verify_sent(self, conv_id: str, marker: str, tries: int = 6) -> dict:
        """Ask DBA whether our message is now the last outgoing one.

        A click, or a 200, is not proof. This re-reads the conversation list and
        requires lastMessageOutgoing AND our text in the preview.
        """
        import time
        for attempt in range(tries):
            time.sleep(2)
            try:
                for c in self.list_conversations():
                    if c.conv_id == conv_id:
                        if c.last_outgoing and marker in (c.last_preview or ""):
                            return {"ok": True, "verified": True, "attempts": attempt + 1,
                                    "preview": c.last_preview[:80]}
            except ProviderError:
                continue
        raise ProviderError(
            "sent but NOT verified: DBA does not show our message as the last "
            "outgoing one. Treat as failed — do not retry blindly, a second send "
            "may duplicate.")

    # ---------- transport 1: HTTP (no browser) ----------
    def send_http(self, conv_id: str, body: str) -> dict:
        """POST straight to DBA's own send endpoint.

        Captured from a real send on 2026-10-31:
          POST /messages/api/conversations/users/<loginId>/conversations/<convId>/messages
          {"clientMessageId": "<uuid>", "body": "...", "messageType": "textMessage",
           "sent": "<iso>", "attachments": []}
        Auth is cookies only — no CSRF header, no token.
        """
        import datetime
        import uuid

        payload = {
            "clientMessageId": str(uuid.uuid4()),
            "body": body,
            "messageType": "textMessage",
            "sent": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            "attachments": [],
        }
        url = (f"{BASE}/messages/api/conversations/users/{self.login_id}"
               f"/conversations/{conv_id}/messages")
        r = self.session().post(
            url, json=payload, timeout=25,
            headers={"content-type": "application/json",
                     "referer": f"{BASE}/messages/id/{conv_id}"})
        if r.status_code in (401, 403):
            raise AuthError(f"DBA rejected the send with {r.status_code} — session expired")
        if r.status_code not in (200, 201, 202, 204):
            raise ProviderError(f"send returned HTTP {r.status_code}: {r.text[:200]}")
        return {"ok": True, "via": "http", "status": r.status_code,
                "client_message_id": payload["clientMessageId"]}

    # ---------- send (default: DOM, proven; http available) ----------
    def send(self, conv_id: str, body: str, via: str = "dom") -> dict:
        body = (body or "").strip()
        if len(body) < 8:
            raise ProviderError(f"refusing to send {len(body)} chars")

        marker = body.split("\n")[0][:40]

        if via == "http":
            res = self.send_http(conv_id, body)
            verified = self._verify_sent(conv_id, marker)
            res.update(verified)
            return res

        from cdp import Session  # scripts/cdp.py

        with Session("dba.dk") as s:
            opened = self._open_thread(s, conv_id)
            if not opened:
                raise ProviderError(f"could not open conversation {conv_id[:16]}…")

            typed = s.eval(f"""(() => {{
                const ta = document.querySelector('textarea[aria-label^="Skriv din besked"]');
                if (!ta) return 'no-composer';
                const set = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value').set;
                ta.focus();
                set.call(ta, {json.dumps(body)});
                ta.dispatchEvent(new Event('input', {{bubbles:true}}));
                ta.dispatchEvent(new Event('change', {{bubbles:true}}));
                return ta.value === {json.dumps(body)};
            }})()""")
            if typed is not True:
                raise ProviderError(f"composer rejected text ({typed})")

            for kind in ("keyDown", "char", "keyUp"):
                params = {"type": kind, "key": "Enter", "code": "Enter",
                          "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
                if kind == "char":
                    params["text"] = "\r"
                s._call("Input.dispatchKeyEvent", params, session_id=s.session_id)

        res = self._verify_sent(conv_id, marker)
        res["via"] = "dom"
        return res

    def _open_thread(self, s, conv_id: str) -> bool:
        """Open a conversation by clicking its row in the inbox list.

        Two things this must not assume:
          * that the browser is already on the inbox list — it may be sitting in
            a thread from a previous attempt, and then there are no rows to click;
          * that the row can be matched on the message preview — short replies like
            "Hey" collide across buyers. Match the ad title first, partner second.
        """
        import time

        target = next((c for c in self.list_conversations() if c.conv_id == conv_id), None)
        if not target:
            return False

        def row_count() -> int:
            return s.eval("document.querySelectorAll('a[href^=\"/messages/id/\"]').length") or 0

        # 1. make sure we are on the list
        if row_count() == 0:
            s.eval("location.href='https://www.dba.dk/messages/'")
            for _ in range(20):
                time.sleep(1.5)
                try:
                    s.reset()
                    if row_count() > 0:
                        break
                except Exception:
                    continue
        if row_count() == 0:
            return False

        # 2. locate the row: ad title is unique, buyer name is the fallback
        for needle in (target.item_title[:35], target.partner_name):
            if not needle:
                continue
            rect = s.json(f"""(() => {{
                const rows = [...document.querySelectorAll('a[href^="/messages/id/"]')];
                const hit = rows.find(a => (a.innerText || '').includes({json.dumps(needle)}));
                if (!hit) return 'null';
                hit.scrollIntoView({{block: 'center'}});
                const r = hit.getBoundingClientRect();
                return JSON.stringify({{x: r.left + r.width / 2, y: r.top + r.height / 2}});
            }})()""")
            if not rect or rect == "null":
                continue

            # 3. real mouse event — a synthetic .click() is untrusted and the SPA
            #    silently ignores it (observed: URL changes, pane never hydrates)
            for kind in ("mousePressed", "mouseReleased"):
                s._call("Input.dispatchMouseEvent",
                        {"type": kind, "x": rect["x"], "y": rect["y"],
                         "button": "left", "clickCount": 1},
                        session_id=s.session_id)

            # 4. wait for the composer, do not guess a fixed delay
            for _ in range(20):
                time.sleep(1.5)
                try:
                    s.reset()
                    if s.eval("!!document.querySelector('textarea[aria-label^=\"Skriv din besked\"]')") is True:
                        return True
                except Exception:
                    continue
        return False
