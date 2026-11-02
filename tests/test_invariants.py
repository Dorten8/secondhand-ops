#!/usr/bin/env python3
"""Invariant tests — runnable with no marketplace, no network, no dependencies.

    python3 tests/test_invariants.py

These are the rules that must never regress. They are deliberately plain asserts
rather than a test framework, so an agent (or a friend) can run them anywhere.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "conversations"))

from mstore import Store            # noqa: E402


def test_draft_decided_once():
    """A draft leaves `pending` exactly once — this is what stops a double send."""
    with tempfile.TemporaryDirectory() as td:
        st = Store(Path(td) / "state.db")
        did = st.create_draft("dba", "conv-1", "Hej med dig, den er stadig til salg.")

        st.decide(did, "sent", "jacob")
        assert st.draft(did)["status"] == "sent"

        try:
            st.decide(did, "sent", "jacob")
        except ValueError:
            pass
        else:
            raise AssertionError("deciding twice must be refused")


def test_messages_never_duplicate():
    """Stable content hashes: re-polling the same message must not queue it twice."""
    with tempfile.TemporaryDirectory() as td:
        st = Store(Path(td) / "state.db")
        assert st.add_message("dba", "c1", "Hej", "in") is True
        assert st.add_message("dba", "c1", "Hej", "in") is False   # already known


def test_event_dedupe():
    """The same (kind, provider, conv_id, payload) fires once."""
    with tempfile.TemporaryDirectory() as td:
        st = Store(Path(td) / "state.db")
        assert st.push_event("needs_draft", "dba", "c1", "hello") is True
        assert st.push_event("needs_draft", "dba", "c1", "hello") is False


def test_registry_example_parses():
    """The published example must stay loadable, and sold/draft rows must drop out."""
    ex = Path(__file__).resolve().parent.parent / "examples" / "registry.example.json"
    rows = json.loads(ex.read_text())
    assert isinstance(rows, list) and rows, "example must be a non-empty list"
    for r in rows:
        assert "provider" in r and "item_id" in r, "join key must be present"
        assert r.get("status") in ("draft", "listed", "reserved", "sold", "withdrawn")

    from conversations.registry import actionable
    keep = actionable(path=str(ex))
    assert all(r.status not in ("sold", "draft") for r in keep), \
        "sold and draft rows must never be actionable"


def test_router_shortcuts():
    """The human's reply is routed by a parser, not by a model reading prose."""
    from conversations import approvals as A
    assert A.parse_reply("1")["action"] == A.ACTION_SEND_AS_IS
    assert A.parse_reply("yes")["action"] == A.ACTION_SEND_AS_IS
    assert A.parse_reply("ja")["action"] == A.ACTION_SEND_AS_IS
    assert A.parse_reply("3")["action"] == A.ACTION_IGNORE
    assert A.parse_reply("no")["action"] == A.ACTION_IGNORE
    assert A.parse_reply("nej")["action"] == A.ACTION_IGNORE

    d = A.parse_reply("2: Hej, den er desværre solgt")
    assert d["action"] == A.ACTION_SEND_TEXT
    assert d["body"] == "Hej, den er desværre solgt"


def test_router_understands_button_labels():
    """A tap sends the label as text; it must take the same path as typing."""
    from conversations import approvals as A
    assert A.parse_reply("1\ufe0f\u20e3 Send as is")["action"] == A.ACTION_SEND_AS_IS
    assert A.parse_reply("3\ufe0f\u20e3 Ignore")["action"] == A.ACTION_IGNORE
    assert A.parse_reply("2\ufe0f\u20e3 Send what I write")["action"] == A.ACTION_NEED_TEXT
    assert A.parse_reply("2")["action"] == A.ACTION_NEED_TEXT
    other = A.parse_reply("4\ufe0f\u20e3 Other")
    assert other["action"] == A.ACTION_FREEFORM and other["body"] == ""


def test_one_live_draft_per_conversation():
    """The poller re-reports the open queue; drafting must not duplicate the card."""
    import tempfile
    from pathlib import Path as _P
    from conversations.mstore import Store
    with tempfile.TemporaryDirectory() as td:
        st = Store(_P(td) / "t.db")
        a = st.create_draft("dba", "conv-1", "first body")
        b = st.create_draft("dba", "conv-1", "second body")
        assert a == b, "a second draft was created for a conversation that already had one"
        st.decide(a, "ignored", "test")
        c = st.create_draft("dba", "conv-1", "third body")
        assert c != a, "a decided draft must not block a new one"


def test_router_freeform_is_not_invalid():
    """Anything outside the shortcuts is an instruction for the agent, not a rejection."""
    from conversations import approvals as A
    d = A.parse_reply("kan du spørge om han henter i weekenden i stedet")
    assert d["action"] == A.ACTION_FREEFORM
    assert d["body"] == "kan du spørge om han henter i weekenden i stedet"


def test_no_default_topic():
    """A missing destination must fail, not fall back to some thread."""
    from shared import topics as T
    try:
        T.resolve("")
    except T.UnknownTopic:
        pass
    else:
        raise AssertionError("empty topic resolved instead of raising")

    try:
        T.resolve("definitely_not_a_topic")
    except T.UnknownTopic:
        pass
    else:
        raise AssertionError("unknown topic resolved instead of raising")


def test_routes_by_content():
    from shared import topics as T
    assert T.resolve("buyer_message")[1] == T.TOPICS["inbox"]
    assert T.resolve("draft_card")[1] == T.TOPICS["inbox"]
    assert T.resolve("shopping")[1] == T.TOPICS["gets"]
    assert T.resolve("academic_jobs")[1] == T.TOPICS["jobs"]
    assert T.resolve("task_capture")[1] == T.TOPICS["todoes"]
    assert T.resolve("ops")[1] is None          # logged, never posted


def test_personal_is_refused():
    from shared import topics as T
    try:
        T.resolve("personal")
    except T.PolicyRefusal:
        pass
    else:
        raise AssertionError("Personal was allowed without an explicit override")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
