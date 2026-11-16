#!/usr/bin/env python3
"""msgctl — control surface for the marketplace messaging lane.

Every stage is a separate verb so it can be run by a human, a cron job, a shell
script, or a different agent. Nothing here talks to Telegram; the caller decides
how to deliver `notify.py` output. That is what makes the lane portable.

  msgctl auth                      refresh DBA cookies from Brave over CDP
  msgctl whoami                    prove the session works
  msgctl poll [--provider dba]     trigger stage, silent when nothing is new
  msgctl pending                   what the agent must draft next (JSON)
  msgctl unanswered [--days 14]    threads where WE spoke last and got no answer
  msgctl list                      all known conversations
  msgctl draft --conv ID --body-file F [--inbound TEXT]   record a proposal
  msgctl drafts                    proposals awaiting a decision
  msgctl show --id N               render one draft as the message the human sees
  msgctl approve --id N --action send|ignore|supersede [--body-file F] [--by NAME]
  msgctl decide --id N --text "<what the human replied>"   # deterministic router
  msgctl notify --id N [--no-send]                          # deliver the draft card
  msgctl stats

Exit codes: 0 ok, 2 auth/network, 3 refused (gate), 4 send unverified.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

# How long a delivered-but-unanswered card may block the next one. The rule exists so
# two keyboards never point at once; it must not become a starvation trap, because that
# is what it was: eight real buyer messages sat undelivered behind one ignored card
# (observed 2026-11-16). Past this age the old card no longer blocks — it stays pending
# and is still decidable — and the next card goes out.
CARD_TTL_HOURS = float(os.environ.get("MSG_CARD_TTL_HOURS", "3"))

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "providers"))

import approvals                                                   # noqa: E402
import snippets
import notify                                                      # noqa: E402
from mstore import Store                                            # noqa: E402
from base import AuthError, ProviderError                           # noqa: E402


def prov(name: str):
    if name == "dba":
        from dba import DBA
        return DBA()
    if name == "fb":
        from fb import Facebook
        return Facebook()
    raise SystemExit(f"unknown provider {name!r}")


# ---------------------------------------------------------------- auth
def cmd_auth(a):
    from dba import refresh_cookies, ACCOUNT_FILE
    n = refresh_cookies()
    print(f"exported {n} dba/vend cookies -> {HERE.parent}/dba_cookies.json")
    if not ACCOUNT_FILE.exists():
        print(f"NOTE: {ACCOUNT_FILE} missing. Create it with "
              '{"login_id": "<id>"} — the id appears in the conversationgroups URL.')
    return 0


def cmd_whoami(a):
    try:
        print(json.dumps(prov(a.provider).whoami(), indent=1))
    except (AuthError, ProviderError) as e:
        print(f"AUTH/PROVIDER: {e}", file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------- polling
def cmd_poll(a):
    from poll import poll
    return poll(a.provider)


def cmd_pending(a):
    st = Store()
    events = st.open_events("needs_draft")
    if not events:
        print("NOTHING_PENDING")
        return 0
    convs = {f"{c['provider']}:{c['conv_id']}": c for c in st.conversations()}
    out = []
    for e in events:
        c = convs.get(f"{e['provider']}:{e['conv_id']}", {})
        out.append({"event_id": e["id"], "provider": e["provider"],
                    "conv_id": e["conv_id"], "inbound": e["payload"],
                    "item_title": c.get("item_title"),
                    "partner_name": c.get("partner_name"),
                    "item_id": c.get("item_id")})
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- drafts
def _pickup_leak(body: str, inbound: str) -> str | None:
    """Refuse a draft that hands out the pickup details before a time is agreed.

    The rule already exists in the skill ("no address before a specific day + time"),
    and it was still broken by hand on 2026-11-16: a reply to a buyer who was walking
    away on price ended with the map link and the pickup postcode. A rule that depends
    on remembering is not a rule, so this is mechanical:

      * does the body contain the configured map link or phone number?
      * does the inbound message contain an agreed time ("kl. 19", "19:00", "i morgen")?
    """
    import os
    import re as _re
    from pathlib import Path as _Path

    env = _Path(os.environ.get("HERMES_ENV_FILE", "/home/hermes/hermes/home/.env"))
    secrets = []
    try:
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(("PICKUP_MAP=", "PICKUP_PHONE=")):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    secrets.append(v)
    except OSError:
        return None
    hit = next((v for v in secrets if v and v in body), None)
    if not hit:
        return None
    if _re.search(r"\b\d{1,2}[:.]\d{2}\b|\bkl\.?\s*\d|\bi dag\b|\bi morgen\b|\btoday\b|\btomorrow\b", inbound or "", _re.I):
        return None
    return ("REFUSED: the draft contains the pickup map/phone but nothing in the buyer's "
            "message agrees a time. Address only after a specific day + time.")


def cmd_draft(a):
    st = Store()
    body = Path(a.body_file).read_text(encoding="utf-8").rstrip("\n") if a.body_file else (a.body or "")
    if len(body.strip()) < 8:
        print(f"REFUSED: body is {len(body.strip())} chars", file=sys.stderr)
        return 3
    leak = _pickup_leak(body, a.inbound or "")
    if leak:
        print(leak, file=sys.stderr)
        return 3
    if getattr(a, "pickup", False):
        # language follows the message being sent, unless the caller pins it
        body = snippets.with_pickup(body, lang=getattr(a, "lang", None) or None)
    # Resolve the provider FROM the conversation: a Facebook thread handed to
    # `draft` with the default provider made the send stage pick the DBA driver
    # and fail with "no page matching 'dba.dk'" (2026-11-15).
    convs = {c["conv_id"]: c for c in st.conversations(a.provider)}
    if a.conv not in convs:
        for c0 in st.conversations():
            if c0["conv_id"] == a.conv:
                a.provider, convs = c0["provider"], {c0["conv_id"]: c0}
                break
    c = convs.get(a.conv, {})
    live = st.open_draft_for(a.provider, a.conv)
    did = st.create_draft(a.provider, a.conv, body,
                          inbound=a.inbound or "",
                          item_title=c.get("item_title") or a.title or "",
                          partner_name=c.get("partner_name") or a.buyer or "")
    # 🔴 Say OUT LOUD whether the body just written is the body that came back.
    # Verified failure 2026-11-07: a cron draft was live for the same conversation,
    # `create_draft` returned that existing id, and the caller then approved it —
    # sending a message nobody had written on purpose. `reused: true` is the signal
    # to supersede instead of approving.
    reused = bool(live) and live != did
    print(json.dumps({"draft_id": did, "reused": reused,
                      "note": ("existing live draft returned — body NOT stored"
                               if reused else "created"),
                      "message": notify.draft_message(st.draft(did))},
                     indent=1, ensure_ascii=False))
    return 0


def cmd_drafts(a):
    st = Store()
    rows = st.pending_drafts()
    if not rows:
        print("NO_PENDING_DRAFTS")
        return 0
    for d in rows:
        print(f"#{d['id']}  {d['provider']}  {d['partner_name']}  {d['item_title'][:40]}")
    return 0


def cmd_show(a):
    st = Store()
    d = st.draft(a.id)
    if not d:
        print(f"no draft #{a.id}", file=sys.stderr)
        return 3
    print(notify.draft_message(d))
    return 0


DAY_WORDS = ("i dag", "i morgen", "i aften", "søndag", "mandag", "tirsdag",
             "onsdag", "torsdag", "fredag", "lørdag", "sunday", "monday", "tuesday",
             "wednesday", "thursday", "friday", "saturday", "today", "tomorrow")
TIME_RE = re.compile(r"\b(?:kl\.?\s*)?\d{1,2}[.:]\d{2}\b")


def _window_in(body: str) -> str:
    """Return the day/time phrasing if this message promises a time, else ''.

    Used to record a reservation (a `slot`) so that a second buyer on the same
    item can be flagged. Deliberately conservative: a bare "kom forbi" is not a
    slot; a day word or a clock time is.
    """
    if not body:
        return ""
    low = body.lower()
    has_time = bool(TIME_RE.search(low))
    has_day = any(w in low for w in DAY_WORDS)
    if not (has_time or has_day):
        return ""
    for line in body.split("\n"):
        l = line.lower()
        if TIME_RE.search(l) or any(w in l for w in DAY_WORDS):
            return line.strip()[:80]
    return ""


def _maybe_clear_keyboard(st, note: str) -> None:
    """Strip the sticky reply keyboard once no card is left to replace it.

    A reply keyboard lives on the chat; a new card's `keyboard_for(id)` replaces
    it, so the removal is only needed when the queue is empty. Never let this
    raise — a failed receipt must not undo a recorded decision.
    """
    try:
        if st.pending_drafts():
            return
        res = notify.clear_keyboard(note)
        print(f"KEYBOARD cleared (msg {res.get('message_id')})")
    except Exception as exc:  # noqa: BLE001
        print(f"KEYBOARD clear failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def cmd_approve(a):
    st = Store()
    d = st.draft(a.id)
    if not d:
        print(f"no draft #{a.id}", file=sys.stderr)
        return 3
    if d["status"] != "pending":
        print(f"REFUSED: draft #{a.id} is already {d['status']}", file=sys.stderr)
        return 3

    if a.action == "ignore":
        st.decide(a.id, "ignored", a.by)
        print(f"IGNORED draft #{a.id}")
        notify.ping(f"\u2705 #{a.id} ignored")
        return 0

    if a.action == "supersede":
        body = (Path(a.body_file).read_text(encoding="utf-8").rstrip("\n")
                if a.body_file else (a.body or ""))
        if len(body.strip()) < 8:
            print("REFUSED: replacement body too short", file=sys.stderr)
            return 3
        st.decide(a.id, "superseded", a.by, result="replaced")
        new_id = st.create_draft(d["provider"], d["conv_id"], body,
                                 inbound=d["inbound"], item_title=d["item_title"],
                                 partner_name=d["partner_name"])
        print(json.dumps({"superseded": a.id, "draft_id": new_id,
                          "message": notify.draft_message(st.draft(new_id))},
                         indent=1, ensure_ascii=False))
        notify.ping(f"\u270e\ufe0f #{a.id} rewritten \u2014 new card #{new_id}")
        return 0

    if a.action == "send":
        body = (Path(a.body_file).read_text(encoding="utf-8").rstrip("\n")
                if a.body_file else d["body"])
        notify.ping(f"\u23f3 #{a.id} \u2014 sending\u2026")
        try:
            res = prov(d["provider"]).send(d["conv_id"], body, via=a.via)
        except AuthError as e:
            st.decide(a.id, "failed", a.by, result=f"auth: {e}")
            print(f"AUTH: {e}", file=sys.stderr)
            notify.ping(f"\u274c #{a.id} failed \u2014 auth: {str(e)[:80]}")
            return 2
        except Exception as e:
            st.decide(a.id, "failed", a.by, result=f"{type(e).__name__}: {e}")
            print(f"SEND FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            notify.ping(f"\u274c #{a.id} failed \u2014 {type(e).__name__}: {str(e)[:80]}")
            return 4
        st.decide(a.id, "sent", a.by, result=json.dumps(res)[:500])
        st.add_message(d["provider"], d["conv_id"], body, "out")
        # the thread is handled — clear its queue entry
        for e in st.open_events("needs_draft"):
            if e["conv_id"] == d["conv_id"] and e["provider"] == d["provider"]:
                st.close_event(e["id"])
        print(json.dumps({"sent": True, "draft_id": a.id, "result": res},
                         indent=1, ensure_ascii=False))
        # remember the reservation: the item is now spoken for, which is what lets
        # a second buyer on the same item be flagged instead of double-booked.
        try:
            convo = next((c for c in st.conversations(d["provider"])
                          if c["conv_id"] == d["conv_id"]), None)
            win = _window_in(body)
            if convo and convo.get("item_id") and win:
                st.add_slot(d["provider"], convo["item_id"], d["conv_id"],
                            convo.get("partner_name") or "", win)
                print(f"SLOT recorded: item {convo['item_id']} -> {convo.get('partner_name')} ({win})")
        except Exception as exc:  # noqa: BLE001
            print(f"SLOT record failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        notify.ping(f"\u2705 #{a.id} sent")
        return 0

    print(f"unknown action {a.action!r}", file=sys.stderr)
    return 3


# ---------------------------------------------------------------- misc
def cmd_list(a):
    st = Store()
    rows = st.conversations(a.provider)
    for c in rows:
        who = "YOU  " if c["last_outgoing"] else "BUYER"
        print(f"{who} {c['provider']:4s} {c['item_title'][:45]:45s} "
              f"| {(c['partner_name'] or '')[:14]:14s} | {c['last_preview'][:40]}")
    openish = sum(1 for c in rows if not c["last_outgoing"])
    print(f"\nconversations {len(rows)} | ball with you {openish}")
    return 0


def cmd_unanswered(a):
    """Threads where Jacob spoke last and the buyer never came back.

    This is the mirror of `pending`: `pending` is "buyer spoke, we owe a reply",
    `unanswered` is "we spoke, they owe a reply". Useful for follow-ups and for
    deciding what to close out.
    """
    from datetime import datetime, timedelta, timezone

    st = Store()
    cutoff = datetime.now(timezone.utc) - timedelta(days=a.days)
    rows = []
    for c in st.conversations(a.provider):
        if not c.get("last_outgoing"):
            continue
        when = c.get("last_at") or ""
        try:
            d = datetime.fromisoformat(when.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
        except Exception:
            continue                       # unknown date: do not guess it into the window
        if d < cutoff:
            continue
        rows.append((d, c))

    rows.sort(key=lambda t: t[0], reverse=True)
    if not rows:
        print(f"none — no unanswered outbound messages in the last {a.days} days")
        return 0

    if a.json:
        out = [{"item_id": c["item_id"], "item_title": c["item_title"],
                "buyer": c["partner_name"], "sent": c["last_at"],
                "days_waiting": round((cutoff + timedelta(days=a.days) - d).total_seconds() / 86400, 1),
                "my_message": c["last_preview"], "conv_id": c["conv_id"]}
               for d, c in rows]
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0

    print(f"UNANSWERED — you spoke last, no reply ({a.days}d window) — {len(rows)}")
    print("=" * 66)
    for d, c in rows:
        waited = (cutoff + timedelta(days=a.days) - d).total_seconds() / 86400
        print(f"\n{c['item_title'][:58]}")
        print(f"  buyer   {c['partner_name']}   (waiting {waited:.0f}d)")
        print(f"  item    {c['item_id']}   sent {c['last_at'][:16]}")
        print(f"  you said \u201c{(c['last_preview'] or '')[:110]}\u201d")
        print(f"  conv    {c['conv_id']}")
    print(f"\nCOUNT {len(rows)}")
    return 0


_IMPLIED_HISTORY = re.compile(
    r"\b(correction|correcting|as agreed|as discussed|as promised|som aftalt|"
    r"som lovet|retter|rettelse|lige en rettelse)\b", re.IGNORECASE)


def _content_guard(st, draft) -> str | None:
    """Refuse a reply that refers to a conversation history the buyer never had.

    A draft is not a message. When the drafting step 'corrects' something, the only
    thing it can be correcting is one of its own superseded drafts - which the buyer
    never saw - so the text opens with a correction to nothing. That happened to a
    real buyer on 2026-11-02; this check makes the state that allowed it impossible.
    """
    body = draft.get("body") or ""
    m = _IMPLIED_HISTORY.search(body)
    if not m:
        return None
    prior = st.outbound_count(draft["provider"], draft["conv_id"])
    if prior:
        return None
    return (f"refusing to send: the draft says {m.group(0)!r} but nothing has ever been "
            f"sent in this conversation (0 outbound). Either rewrite it as a first "
            f"message, or send it with --allow-implied-history after reading the thread.")


def cmd_deliver(a):
    """Post any draft that was written but never put in front of the human.

    Drafting is an LLM step; delivery must not depend on that same run finishing.
    A draft that exists with no card is invisible, and the buyer's message is
    effectively unanswered - which is what 'lack of timely relaying' was: latency
    to draft was under a minute, latency to *show* had no bound at all.
    """
    st = Store()
    queued = [d for d in st.pending_drafts() if not d.get("notified_at")]
    if not queued:
        return 0

    out = st.outstanding_card()
    if out:
        if not a.quiet:
            print(f"holding {len(queued)} draft(s): card #{out['id']} ({out['partner_name']}) "
                  f"is still unanswered")
        return 0

    d = sorted(queued, key=lambda x: x["id"])[0]
    res = notify.send_telegram(notify.to_html(d), chat=a.chat, thread=a.thread,
                               markup=notify.keyboard_for(d["id"]))
    if res.get("ok"):
        st.mark_notified(d["id"])
        if not a.quiet:
            print(f"delivered #{d['id']} ({d['partner_name']}) as message {res['message_id']}")
        return 0
    if not a.quiet:
        print(f"delivery failed for #{d['id']}: {res}", file=sys.stderr)
    return 4


def cmd_sweep(a):
    """Drop drafts whose conversation went cold.

    Jacob, 2026-11-16: "ignore any messages more than 1 day old, because this was an old
    exchange". A weeks-old thread in the queue is not work, it is noise that also blocks
    the fresh cards. Age comes from the conversation's own last activity, not from when
    the draft was written.
    """
    st = Store()
    cutoff = time.time() - a.hours * 3600
    db = st.db
    # Address the columns by position: Store's connection does not always hand back
    # sqlite3.Row, and a KeyError here was silently swallowed into "nothing stale".
    cur = db.execute(
        """SELECT d.id, d.partner_name, c.last_at
             FROM drafts d LEFT JOIN conversations c
               ON c.provider = d.provider AND c.conv_id = d.conv_id
            WHERE d.status = 'pending'""")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    dropped, unparsed = [], 0
    for r in rows:
        last_at = (r["last_at"] or "")
        try:
            ts = datetime.datetime.fromisoformat(last_at).timestamp()
        except Exception:
            unparsed += 1
            continue
        age_h = (time.time() - ts) / 3600.0
        if ts >= cutoff:
            continue
        db.execute("UPDATE drafts SET status='ignored', decided_at=?, decided_by=?, result=? "
                   "WHERE id=?",
                   (time.time(), "auto-stale",
                    f"conversation last active {age_h:.0f}h ago (>{a.hours:g}h) — old exchange",
                    r["id"]))
        dropped.append((r["id"], r["partner_name"], age_h))
    db.commit()
    if not dropped:
        print(f"NO_STALE_DRAFTS ({len(rows)} pending, {unparsed} without a readable date)")
        return 0
    print(f"swept {len(dropped)} stale draft(s) older than {a.hours:g}h:")
    for did, who, age_h in dropped:
        print(f"  #{did:<4} {(who or '?')[:14]:<15} last active {age_h:.0f}h ago")
    return 0


def cmd_notify(a):
    """Deliver the draft card to the human. This is the script the chat receives —
    not the agent writing prose, so the wording cannot drift and it costs nothing."""
    st = Store()
    d = st.draft(a.id)
    if not d:
        print(f"no draft #{a.id}", file=sys.stderr)
        return 3
    # one live card at a time: a chat-wide keyboard cannot point at two cards
    out = st.outstanding_card()
    if out and out["id"] != a.id:
        age_h = (time.time() - float(out.get("notified_at") or 0)) / 3600.0
        stale = age_h >= CARD_TTL_HOURS
        if not (a.force or stale):
            print(f"not sending #{a.id}: card #{out['id']} ({out['partner_name']}) is still "
                  f"unanswered ({age_h:.1f}h, releases after {CARD_TTL_HOURS:g}h). "
                  f"Decide it, or pass --force.", file=sys.stderr)
            return 5
        if stale:
            print(f"note: card #{out['id']} unanswered for {age_h:.1f}h — not blocking any more; "
                  f"it stays pending and decidable.", file=sys.stderr)

    html = notify.to_html(d)
    # Say how much is behind this card. Silence about the queue is what made eight real
    # buyer messages look like "Facebook is not sending anything" (2026-11-16).
    waiting = [x for x in st.pending_drafts() if x["id"] != a.id]
    if waiting:
        ids = " ".join(f"#{x['id']}" for x in waiting[:8])
        more = f" (+{len(waiting) - 8})" if len(waiting) > 8 else ""
        html += f"\n\n<i>↳ {len(waiting)} more waiting: {ids}{more}</i>"
    if a.no_send:
        print(html)
        return 0
    res = notify.send_telegram(html, chat=a.chat, thread=a.thread,
                               markup=notify.keyboard_for(a.id))
    if res.get("ok"):
        st.mark_notified(a.id)
    print(json.dumps(res))
    return 0 if res.get("ok") else 4


def cmd_decide(a):
    """Route a human reply without a model in the loop.

    'yes'/'1' -> send the draft as is;  'no'/'3' -> ignore;
    '2: <text>' -> send that text;  anything else -> an instruction for the agent.
    """
    st = Store()
    d = st.draft(a.id)
    if not d:
        print(f"no draft #{a.id}", file=sys.stderr)
        return 3
    decision = approvals.parse_reply(a.text)
    if decision.get("draft_id"):
        a.id = decision["draft_id"]          # the label named its card; trust the label
    out = {"draft_id": a.id, "parsed": decision}

    if decision["action"] in (approvals.ACTION_FREEFORM, approvals.ACTION_NEED_TEXT):
        out["note"] = ("instruction — hand back to the agent; nothing was sent")
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0

    out["result"] = approvals.apply(st, prov(d["provider"]), a.id, decision, a.by)
    if out["result"].get("ok") and decision["action"] in (approvals.ACTION_SEND_AS_IS,
                                                          approvals.ACTION_SEND_TEXT,
                                                          approvals.ACTION_IGNORE):
        nxt = [x for x in st.pending_drafts() if not x.get("notified_at")]
        if nxt:
            n = sorted(nxt, key=lambda x: x["id"])[0]
            r = notify.send_telegram(notify.to_html(n), markup=notify.keyboard_for(n["id"]))
            if r.get("ok"):
                st.mark_notified(n["id"])
                out["next_card"] = {"draft_id": n["id"], "message_id": r["message_id"]}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if out["result"].get("ok") else 4


def cmd_stats(a):
    print(json.dumps(Store().stats(), indent=1))
    return 0


def main():
    p = argparse.ArgumentParser(prog="msgctl", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", default="dba")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth").set_defaults(fn=cmd_auth)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    sub.add_parser("poll").set_defaults(fn=cmd_poll)
    sub.add_parser("pending").set_defaults(fn=cmd_pending)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("drafts").set_defaults(fn=cmd_drafts)
    sw = sub.add_parser("sweep")
    sw.add_argument("--hours", type=float, default=24.0)
    sw.set_defaults(fn=cmd_sweep)

    un = sub.add_parser("unanswered")
    un.add_argument("--days", type=int, default=14)
    un.add_argument("--json", action="store_true")
    un.set_defaults(fn=cmd_unanswered)

    dv = sub.add_parser("deliver")
    dv.add_argument("--quiet", action="store_true")
    dv.add_argument("--chat", default=notify.DEFAULT_CHAT)
    dv.add_argument("--thread", type=int, default=notify.DEFAULT_THREAD)
    dv.set_defaults(fn=cmd_deliver)

    nt = sub.add_parser("notify")
    nt.add_argument("--id", type=int, required=True)
    nt.add_argument("--no-send", action="store_true")
    nt.add_argument("--force", action="store_true")
    nt.add_argument("--chat", default=notify.DEFAULT_CHAT)
    nt.add_argument("--thread", type=int, default=notify.DEFAULT_THREAD)
    nt.set_defaults(fn=cmd_notify)

    dc = sub.add_parser("decide")
    dc.add_argument("--id", type=int, required=True)
    dc.add_argument("--text", required=True)
    dc.add_argument("--by", default="Jacob")
    dc.set_defaults(fn=cmd_decide)

    sub.add_parser("stats").set_defaults(fn=cmd_stats)

    d = sub.add_parser("draft")
    d.add_argument("--conv", required=True)
    d.add_argument("--body-file"); d.add_argument("--body")
    d.add_argument("--inbound", default="")
    d.add_argument("--pickup", action="store_true",
                   help="insert the fixed map link + phone block before the sign-off")
    d.add_argument("--lang", choices=["da", "en"], default=None,
                   help="force the pickup block language (default: detect from the body)")
    d.add_argument("--title", default=""); d.add_argument("--buyer", default="")
    d.set_defaults(fn=cmd_draft)

    s = sub.add_parser("show"); s.add_argument("--id", type=int, required=True)
    s.set_defaults(fn=cmd_show)

    ap = sub.add_parser("approve")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--action", required=True, choices=["send", "ignore", "supersede"])
    ap.add_argument("--body-file"); ap.add_argument("--body")
    ap.add_argument("--by", default="Jacob")
    ap.add_argument("--via", default="dom", choices=["dom", "http"],
                    help="dom = proven path through the browser; http = direct API call")
    ap.set_defaults(fn=cmd_approve)

    a = p.parse_args()
    try:
        sys.exit(a.fn(a))
    except AuthError as e:
        print(f"AUTH: {e}", file=sys.stderr); sys.exit(2)
    except ProviderError as e:
        print(f"PROVIDER: {e}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    main()
