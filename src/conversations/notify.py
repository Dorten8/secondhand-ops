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


if __name__ == "__main__":
    import json
    import sys
    demo = {"id": 7, "item_title": 'Rear Wheel / Baghjul - 28"', "partner_name": "Gunnar N",
            "inbound": "Passer dette hjul til min 7-speed racer?",
            "body": "Hej Gunnar,\n\nNej, det passer desværre ikke. 126 mm mod 135 mm.\n\nMvh Jakub"}
    print(draft_message(demo) if len(sys.argv) < 2 else json.dumps(demo, indent=1))
