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
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "providers"))

import approvals                                                   # noqa: E402
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
def cmd_draft(a):
    st = Store()
    body = Path(a.body_file).read_text(encoding="utf-8").rstrip("\n") if a.body_file else (a.body or "")
    if len(body.strip()) < 8:
        print(f"REFUSED: body is {len(body.strip())} chars", file=sys.stderr)
        return 3
    convs = {c["conv_id"]: c for c in st.conversations(a.provider)}
    c = convs.get(a.conv, {})
    did = st.create_draft(a.provider, a.conv, body,
                          inbound=a.inbound or "",
                          item_title=c.get("item_title") or a.title or "",
                          partner_name=c.get("partner_name") or a.buyer or "")
    print(json.dumps({"draft_id": did,
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
        return 0

    if a.action == "send":
        body = (Path(a.body_file).read_text(encoding="utf-8").rstrip("\n")
                if a.body_file else d["body"])
        try:
            res = prov(d["provider"]).send(d["conv_id"], body, via=a.via)
        except AuthError as e:
            st.decide(a.id, "failed", a.by, result=f"auth: {e}")
            print(f"AUTH: {e}", file=sys.stderr)
            return 2
        except Exception as e:
            st.decide(a.id, "failed", a.by, result=f"{type(e).__name__}: {e}")
            print(f"SEND FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            return 4
        st.decide(a.id, "sent", a.by, result=json.dumps(res)[:500])
        st.add_message(d["provider"], d["conv_id"], body, "out")
        # the thread is handled — clear its queue entry
        for e in st.open_events("needs_draft"):
            if e["conv_id"] == d["conv_id"] and e["provider"] == d["provider"]:
                st.close_event(e["id"])
        print(json.dumps({"sent": True, "draft_id": a.id, "result": res},
                         indent=1, ensure_ascii=False))
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


def cmd_notify(a):
    """Deliver the draft card to the human. This is the script the chat receives —
    not the agent writing prose, so the wording cannot drift and it costs nothing."""
    st = Store()
    d = st.draft(a.id)
    if not d:
        print(f"no draft #{a.id}", file=sys.stderr)
        return 3
    html = notify.to_html(d)
    if a.no_send:
        print(html)
        return 0
    res = notify.send_telegram(html, chat=a.chat, thread=a.thread)
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
    out = {"draft_id": a.id, "parsed": decision}

    if decision["action"] in (approvals.ACTION_FREEFORM, approvals.ACTION_NEED_TEXT):
        out["note"] = ("instruction — hand back to the agent; nothing was sent")
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0

    out["result"] = approvals.apply(st, prov(d["provider"]), a.id, decision, a.by)
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
    un = sub.add_parser("unanswered")
    un.add_argument("--days", type=int, default=14)
    un.add_argument("--json", action="store_true")
    un.set_defaults(fn=cmd_unanswered)

    nt = sub.add_parser("notify")
    nt.add_argument("--id", type=int, required=True)
    nt.add_argument("--no-send", action="store_true")
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
