#!/usr/bin/env python3
"""topics.py — the only place that knows which chat+topic a message belongs in.

WHY THIS EXISTS
The topic map was already documented, and messages still went to the wrong
thread. The map was not the bug: the bug was that *each sender chose a target*,
and any choice can be made wrong. So this module removes the choice.

Rules it enforces:
  * **No default.** `resolve()` raises on an unknown key. There is no fallback
    topic, because a silent fallback is exactly how buyer messages end up in the
    Development thread.
  * **Content decides the topic, not the chat the work happened in.** A job that
    discusses Hermes in Development but delivers buyer messages must resolve
    `inbox`, and `ROUTES` below states that mapping so it is reviewable.
  * **Personal is refused by default.** That topic is the local-model lane; an
    agent has no business posting there, so it needs an explicit override.
"""
from __future__ import annotations

CHAT_ID = "-1004362684061"          # Hermes group (forum)

# Canonical topic keys -> thread ids. Verified by reading what was actually said
# in each thread, not by inference. Thread ids change if a topic is deleted and
# re-created; re-verify with the content method before editing this table.
TOPICS: dict[str, int] = {
    "bikes": 7,          # Koga F3 project
    "gets": 10,          # item + housing hunts, shopping, price watches
    "inbox": 13,         # buyer messages, marketplace replies, anything unsorted
    "jobs": 16,          # academic job + PhD hunt
    "todoes": 19,        # task capture
    "personal": 44,      # LOCAL MODEL ONLY — refused unless explicitly overridden
    "development": 45,   # conversations about Hermes itself
}

# What kind of content belongs where. This is the reviewable part: if a new kind
# of job appears, add it here rather than picking a thread at send time.
ROUTES: dict[str, str] = {
    "buyer_message": "inbox",
    "marketplace_reply": "inbox",
    "draft_card": "inbox",
    "unsorted": "inbox",
    "shopping": "gets",
    "price_watch": "gets",
    "housing": "gets",
    "academic_jobs": "jobs",
    "task_capture": "todoes",
    "hermes_work": "development",
    "ops": "local",             # no human value: log only, post nowhere
    "backup": "local",
}

# human synonyms, so a caller can say what it means rather than memorise keys
ALIASES: dict[str, str] = {
    "buyers": "inbox",
    "messages": "inbox",
    "marketplace": "inbox",
    "shopping": "gets",
    "deals": "gets",
    "prices": "gets",
    "unijobs": "jobs",
    "jobs_hunt": "jobs",
    "todo": "todoes",
    "todos": "todoes",
    "dev": "development",
    "hermes": "development",
}


class UnknownTopic(ValueError):
    """Raised instead of guessing. A wrong topic is worse than a failed send."""


class PolicyRefusal(RuntimeError):
    """A destination that needs an explicit override (Personal / local model)."""


def route_for(content_kind: str) -> str:
    """Content kind -> topic key. Raises rather than defaulting."""
    kind = (content_kind or "").strip().lower()
    if kind in ROUTES:
        return ROUTES[kind]
    if kind in TOPICS:
        return kind
    raise UnknownTopic(
        f"no route for content kind {content_kind!r}. Add it to ROUTES in "
        f"src/shared/topics.py — do not pick a thread at send time.")


def resolve(key: str, allow_personal: bool = False) -> tuple[str, int | None]:
    """Topic key (or alias, or content kind) -> (chat_id, thread_id).

    Returns thread_id None for the 'local' pseudo-topic, meaning: do not post.
    """
    k = (key or "").strip().lower()
    k = ALIASES.get(k, k)
    if k == "local":
        return CHAT_ID, None                     # log only, never posted
    if k not in TOPICS:
        # maybe it was a content kind
        k = route_for(k)
        if k == "local":
            return CHAT_ID, None
    if k == "personal" and not allow_personal:
        raise PolicyRefusal(
            "Personal (thread 44) is the local-model lane — refusing to post there. "
            "Pass allow_personal=True only for privacy work Jakub asked for.")
    return CHAT_ID, TOPICS[k]


def describe() -> str:
    lines = [f"chat {CHAT_ID}", ""]
    for k, tid in TOPICS.items():
        note = "  (local model only)" if k == "personal" else ""
        lines.append(f"  {k:12s} -> {tid}{note}")
    lines.append("")
    lines.append("routes:")
    for kind, key in ROUTES.items():
        lines.append(f"  {kind:18s} -> {key}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
