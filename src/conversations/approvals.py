#!/usr/bin/env python3
"""approvals.py — the router that was missing. ZERO TOKENS.

The human's answer comes back as a short message. This module turns that message
into an action **deterministically**, so the decision never costs a model call and
never depends on an LLM reading prose correctly.

Accepted answers (Danish or English, case-insensitive):

    yes · ja · y · ok · 1 · "send as is"        -> send the drafted text unchanged
    no  · nej · n · 3 · "ignore"                -> close the thread, send nothing
    2 · "send: <text>" · "skriv: <text>"        -> send exactly the given text instead
    anything else                               -> freeform: it is an INSTRUCTION

The fourth case is not "invalid". Jacob's own words: the options are shortcuts,
not a closed set. A freeform answer is returned to the caller as an instruction
(redraft, answer a side question, mark sold, chase another thread, stop), and the
agent acts on it. Whatever ends in a send still passes through the same
verification as `msgctl approve`.

CLI
    python3 approvals.py --draft 5 --text "yes"            # dry-run: prints the action
    python3 approvals.py --draft 5 --text "send: Hej, den er solgt" --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "providers"))

from mstore import Store  # noqa: E402

ACTION_SEND_AS_IS = "send_as_is"
ACTION_SEND_TEXT = "send_text"
ACTION_IGNORE = "ignore"
ACTION_FREEFORM = "freeform"
ACTION_NEED_TEXT = "need_text"      # "2 -> Send what I write": wait for his text

_YES = {"yes", "y", "ja", "j", "ok", "okay", "1", "send", "send as is", "send as-is"}
_NO = {"no", "n", "nej", "3", "ignore", "drop", "skip"}

# "2: text", "2 text", "send: text", "skriv: text", "send <text>"
_EXPLICIT_TEXT = re.compile(
    r"^(?:2|send|skriv|send instead|svar)\s*[:\-]?\s*(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL)


_NORM = re.compile(r"[^a-z0-9 ]+")


def _ref(text: str):
    m = re.search(r"#(\d+)", text or "")
    return int(m.group(1)) if m else None


def _norm(text: str) -> str:
    """Button labels arrive as '1️⃣ Send as is'. Strip emoji and punctuation so a
    tap and a typed 'send as is' take the same path."""
    return re.sub(r"\s+", " ", _NORM.sub(" ", (text or "").lower())).strip()


def parse_reply(text: str) -> dict:
    """Message -> {action, body}. Pure function; no I/O, no model.

    Returns one of the four actions above, with `body` set for SEND_TEXT and the
    raw instruction for FREEFORM.
    """
    raw = (text or "").strip()
    if not raw:
        return {"action": ACTION_FREEFORM, "body": "", "reason": "empty"}

    # Button labels arrive as "1️⃣ Send as is"; typed answers arrive as "1" or
    # "send as is". Both must reach the same rule, so normalise first and compare
    # against the digits-stripped form.
    norm = _norm(re.sub(r"#\d+", " ", raw))
    low = norm.lstrip("0123456789 ").strip() or norm

    if low in _YES:
        return {"action": ACTION_SEND_AS_IS, "body": None, "draft_id": _ref(raw),
                "reason": f"matched yes-token {low!r}"}

    if low in _NO:
        return {"action": ACTION_IGNORE, "body": None, "draft_id": _ref(raw),
                "reason": f"matched no-token {low!r}"}

    if low in {"2", "send what i write", "send what i type", "my text"}:
        return {"action": ACTION_NEED_TEXT, "body": None, "draft_id": _ref(raw),
                "reason": "button tapped: waiting for the text to send"}

    if low == "other":
        # only reachable from a card sent before 2026-11-02; typing is the freeform path
        return {"action": ACTION_FREEFORM, "body": "", "draft_id": _ref(raw),
                "reason": "old card's Other button — ask once, briefly, what he wants"}

    # "  #7 · 1️⃣ Send as is" — the label names its draft so a chat-wide keyboard
    # can still be unambiguous.
    idm = re.search(r"#(\d+)", raw)
    draft_ref = int(idm.group(1)) if idm else None

    m = _EXPLICIT_TEXT.match(raw)
    if m:
        body = m.group("body").strip()
        if len(body) >= 8:
            return {"action": ACTION_SEND_TEXT, "body": body,
                    "reason": "explicit replacement text after the 2/send prefix"}
        return {"action": ACTION_FREEFORM, "body": raw,
                "reason": "looked like a replacement but is too short to be a message"}

    return {"action": ACTION_FREEFORM, "body": raw,
            "reason": "not one of the shortcuts — treat as an instruction"}


_IMPLIED_HISTORY = re.compile(
    r"\b(correction|correcting|as agreed|as discussed|as promised|som aftalt|"
    r"som lovet|retter|rettelse)\b", re.IGNORECASE)


def content_guard(store: Store, draft: dict, body: str) -> str | None:
    """A draft that was never sent does not exist in the buyer's world.

    Refuses text that implies a shared history ("Correction — …", "as agreed")
    when the conversation has no outbound message at all. Deterministic: no model
    is asked whether it looks wrong.
    """
    m = _IMPLIED_HISTORY.search(body or "")
    if not m:
        return None
    if store.outbound_count(draft["provider"], draft["conv_id"]):
        return None
    return (f"the draft says {m.group(0)!r} but this conversation has no outbound "
            f"message yet — it would read as a correction to something he never "
            f"received. Rewrite it as a first message.")


def apply(store: Store, provider, draft_id: int, decision: dict, by: str = "Jacob") -> dict:
    """Carry out a parsed decision. Every write goes through mstore's legal-transition
    check, so a second decision on the same draft is refused, not silently sent twice."""
    d = store.draft(draft_id)
    if not d:
        return {"ok": False, "error": f"no draft #{draft_id}"}
    if d["status"] != "pending":
        return {"ok": False, "error": f"draft #{draft_id} is already {d['status']}"}

    act = decision["action"]

    if act == ACTION_IGNORE:
        store.decide(draft_id, "ignored", by, result=decision.get("reason", ""))
        return {"ok": True, "action": "ignored", "draft_id": draft_id}

    if act == ACTION_FREEFORM:
        # No state change: an instruction is for the agent to act on, not a decision.
        return {"ok": True, "action": "freeform", "draft_id": draft_id,
                "instruction": decision.get("body", ""), "applied": False}

    body = decision["body"] if act == ACTION_SEND_TEXT else d["body"]

    blocked = content_guard(store, d, body)
    if blocked and not decision.get("allow_implied_history"):
        return {"ok": False, "action": "refused", "draft_id": draft_id, "error": blocked}

    try:
        res = provider.send(d["conv_id"], body)
    except Exception as exc:
        store.decide(draft_id, "failed", by, result=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "action": "send_failed", "draft_id": draft_id,
                "error": f"{type(exc).__name__}: {exc}"}

    store.decide(draft_id, "sent", by, result=json.dumps(res)[:500])
    store.add_message(d["provider"], d["conv_id"], body, "out")
    for ev in store.open_events("needs_draft"):
        if ev["provider"] == d["provider"] and ev["conv_id"] == d["conv_id"]:
            store.close_event(ev["id"])
    return {"ok": True, "action": "sent", "draft_id": draft_id, "result": res}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Route a human reply to an approve/ignore/send.")
    ap.add_argument("--draft", type=int, required=True)
    ap.add_argument("--text", required=True, help="what the human replied")
    ap.add_argument("--by", default="Jacob")
    ap.add_argument("--provider", default="dba")
    ap.add_argument("--apply", action="store_true", help="actually act; without it, dry-run")
    a = ap.parse_args(argv)

    decision = parse_reply(a.text)
    out = {"draft_id": a.draft, "parsed": decision}

    if a.apply:
        if decision["action"] == "freeform":
            out["note"] = "instruction — hand this back to the agent; nothing was sent"
        else:
            from poll import get_provider           # local import: keeps parse() dependency-free
            store = Store()
            out["result"] = apply(store, get_provider(a.provider), a.draft, decision, a.by)

    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if out.get("result", {}).get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
