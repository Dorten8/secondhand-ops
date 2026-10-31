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
