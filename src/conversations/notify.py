#!/usr/bin/env python3
"""notify.py — turn a draft into the exact message the human sees.

Kept separate from the CLI so the wording can change without touching logic, and
so another agent (Antigravity) can reuse it verbatim.

THE FOUR OPTIONS ARE JACOB'S OWN, verbatim from 2026-10-31:
  1 send as is            2 send exactly what I write
  3 ignore                4 other (tell me what to do)

ONE BUYER = ONE MESSAGE. Never bundle two buyers, even on the same ad, because
he answers with a bare number and bundling makes the number ambiguous.
"""
from pathlib import Path


TELEGRAM_API = "https://api.telegram.org"
DEFAULT_CHAT = "-1004362684061"      # Hermes group
DEFAULT_THREAD = 13                  # Inbox — must match topics.ROUTES['draft_card']


def _escape(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_html(draft: dict) -> str:
    """The same message as `draft_message`, in Telegram HTML so formatting survives
    without a Markdown parser failing on a stray underscore or asterisk."""
    title = _escape((draft.get("item_title") or "").strip())
    buyer = _escape((draft.get("partner_name") or "").strip() or "buyer")
    inbound = _escape((draft.get("inbound") or "").strip())
    body = _escape((draft.get("body") or "").strip())

    head = f"🤖 <b>[Hermes] — {buyer}"
    if title:
        head += f" · {title[:55]}"
    head += f"</b>  <code>#{draft['id']}</code>"

    parts = [head, ""]
    if inbound:
        parts += [f"“{inbound[:500]}”", ""]
    parts += ["<b>Draft:</b>", f"<blockquote>{body}</blockquote>", "",
              _escape(OPTIONS_TEXT)]
    return "\n".join(parts)


# A reply keyboard, not an inline one: the buttons type their label into the chat,
# which the gateway receives like any other message. Inline callback_data belongs to
# the gateway's own prefixes (cl:, ea:, sc:) — a script's would be ignored silently.
def keyboard_for(draft_id: int | None) -> dict:
    """Buttons that name the draft they act on.

    A reply keyboard is per-chat, so with more than one card on screen the label
    is the only thing that can say *which* card a tap answers. The id is first so
    it survives in the truncated button text.
    """
    tag = f"#{draft_id} " if draft_id else ""
    return {
        "keyboard": [
            [{"text": f"{tag}1️⃣ Send as is"}],
            [{"text": f"{tag}2️⃣ Send what I write"}],
            [{"text": f"{tag}3️⃣ Ignore"}],
            [{"text": f"{tag}4️⃣ Other"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
        "input_field_placeholder": "tap a choice, or type your own",
    }

OPTIONS_TEXT = (
    "↳ tap a button below, or type your own answer"
)

OPTIONS = (
    "1\ufe0f\u20e3 Send as is  (zero tokens)\n"
    "2\ufe0f\u20e3 Send what I write  (zero tokens)\n"
    "3\ufe0f\u20e3 Ignore\n"
    "4\ufe0f\u20e3 Other (say what you want)"
)


def draft_message(draft: dict) -> str:
    """draft = row from mstore.drafts"""
    title = (draft.get("item_title") or "").strip()
    buyer = (draft.get("partner_name") or "").strip()
    inbound = (draft.get("inbound") or "").strip()
    body = (draft.get("body") or "").strip()

    head = f"\U0001f916 **[Hermes] \u2014 {buyer or 'buyer'}"
    if title:
        head += f" \u00b7 {title[:55]}"
    head += f"**  `#{draft['id']}`"

    parts = [head, ""]
    if inbound:
        parts += [f"\u201c{inbound[:500]}\u201d", ""]
    parts += ["**Draft:**"]
    parts += [f"> {ln}" if ln.strip() else ">" for ln in body.split("\n")]
    parts += ["", OPTIONS]
    return "\n".join(parts)


def digest_message(new_convs: list) -> str:
    """Used when several threads need drafting at once."""
    lines = [f"\U0001f916 **[Hermes] \u2014 {len(new_convs)} new buyer message(s)**", ""]
    for i, c in enumerate(new_convs, 1):
        lines.append(f"**{i}. {(c.item_title or '')[:50]}**")
        lines.append(f"   {c.partner_name}: \u201c{c.last_preview[:120]}\u201d")
    return "\n".join(lines)


def send_telegram(text_html: str, chat: str = DEFAULT_CHAT,
                  thread: int | None = DEFAULT_THREAD, token: str | None = None,
                  markup: dict | None = None) -> dict:
    """Post a rendered draft to the human. Zero tokens — a plain HTTP call.

    Reads the bot token from the environment file; never takes it as an argument
    so it cannot leak into shell history.
    """
    import json as _json
    import re as _re
    import urllib.request as _url

    if token is None:
        env = Path("/home/hermes/hermes/home/.env").read_text(encoding="utf-8", errors="replace")
        m = _re.search(r"^TELEGRAM_BOT_TOKEN=(.+)$", env, _re.M)
        if not m:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not found in .env")
        token = m.group(1).strip().strip('"').strip("'")

    payload = {"chat_id": chat, "text": text_html, "parse_mode": "HTML",
               "disable_web_page_preview": True,
               "reply_markup": markup if markup is not None else keyboard_for(None)}
    if thread:
        payload["message_thread_id"] = thread

    req = _url.Request(f"{TELEGRAM_API}/bot{token}/sendMessage",
                       data=_json.dumps(payload).encode(),
                       headers={"Content-Type": "application/json"})
    res = _json.load(_url.urlopen(req, timeout=25))
    return {"ok": res.get("ok"), "message_id": res.get("result", {}).get("message_id")}


if __name__ == "__main__":
    import argparse
    import json as _json
    import sys as _sys

    ap = argparse.ArgumentParser(description="Render or deliver a draft to the human.")
    ap.add_argument("--draft", type=int, help="draft id in the store")
    ap.add_argument("--send", action="store_true", help="post it to the chat (zero tokens)")
    ap.add_argument("--chat", default=DEFAULT_CHAT)
    ap.add_argument("--thread", type=int, default=DEFAULT_THREAD)
    a = ap.parse_args()

    if not a.draft:
        demo = {"id": 7, "item_title": 'Rear Wheel / Baghjul - 28"', "partner_name": "Gunnar N",
                "inbound": "Passer dette hjul til min 7-speed racer?",
                "body": "Hej Gunnar,\n\nNej, det passer desværre ikke. 126 mm mod 135 mm.\n\nMvh Jakub"}
        print(draft_message(demo))
        _sys.exit(0)

    from mstore import Store
    d = Store().draft(a.draft)
    if not d:
        print(f"no draft #{a.draft}", file=_sys.stderr)
        _sys.exit(3)

    html = to_html(d)
    if not a.send:
        print(html)
        _sys.exit(0)

    res = send_telegram(html, chat=a.chat, thread=a.thread,
                        markup=keyboard_for(a.draft))
    if res.get("ok"):
        Store().mark_notified(a.draft)
    print(_json.dumps(res))
    _sys.exit(0 if res.get("ok") else 4)
