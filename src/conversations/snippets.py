#!/usr/bin/env python3
"""snippets.py — fixed text that must appear verbatim in buyer messages.

A model retyping a phone number or a Google Maps link is one transcription away
from sending a stranger to the wrong address. These are constants, emitted by the
scripts, so the bytes are identical every time.

They are NOT literals in this file. The repository is shareable; the person's
address and number are not. Values live in the Hermes env file
(`~/hermes/home/.env`):

    PICKUP_MAP=https://maps.app.goo.gl/...
    PICKUP_PHONE=12345678
    PICKUP_PHONE_NOTE=call me if I'm not replying     # optional, this is the default

Rule of use: the pickup block goes in ONLY when a pickup time/place has actually
been agreed in that thread. Sending it early reads as pushing the buyer to collect.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_ENV_FILE = "/home/hermes/hermes/home/.env"


def env_file() -> Path:
    """Resolved per call, not at import: a caller (or a test) may point it elsewhere."""
    return Path(os.environ.get("HERMES_ENV_FILE", DEFAULT_ENV_FILE))

SIGNOFFS = ("mvh", "best", "kind regards", "venlig hilsen", "hilsen", "med venlig hilsen")


class MissingPickupConfig(RuntimeError):
    """Raised instead of sending a half-empty address block."""


def _env(name: str, default: str = "") -> str:
    """Process env first, then the env file — cron and gateway runs do not always
    inherit the shell environment that a human would have."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    try:
        txt = env_file().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default
    m = re.search(rf"^{re.escape(name)}=(.+)$", txt, re.M)
    return m.group(1).strip().strip('"').strip("'") if m else default


def pickup_block() -> str:
    """The block in Jacob's format, with the personal data coming from config."""
    url = _env("PICKUP_MAP")
    phone = _env("PICKUP_PHONE")
    note = _env("PICKUP_PHONE_NOTE", "call me if I'm not replying")
    if not url or not phone:
        raise MissingPickupConfig(
            "PICKUP_MAP and PICKUP_PHONE must be set in the Hermes env file before a "
            "pickup confirmation can be sent — refusing to send a partial address.")
    return f"📍{url}\n📞 {phone} ({note})"


def with_pickup(body: str) -> str:
    """Put the pickup block immediately before the sign-off.

    Jacob's instruction: "send the info about where and my phone contact in this
    way ... and then whatever else needs to be in the message". The address belongs
    where the buyer stops reading for logistics - after the agreement, before the
    name - not trailing under the signature where it reads like a footnote.
    """
    body = (body or "").rstrip()
    block = pickup_block()
    if block.split("\n")[0] in body:          # already present: never twice
        return body

    lines = body.split("\n")
    cut = len(lines)
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(SIGNOFFS):
            cut = i
            break
    head = "\n".join(lines[:cut]).rstrip()
    tail = "\n".join(lines[cut:]).strip()
    return f"{head}\n\n{block}" + (f"\n\n{tail}" if tail else "")


if __name__ == "__main__":
    print(pickup_block())
