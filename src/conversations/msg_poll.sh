#!/bin/bash
# msg_poll.sh — cron pre-run script for the marketplace messaging lane.
#
# Two jobs, in this order:
#
#   1. DELIVER, silently. Any draft that exists but was never put in front of the
#      human is posted here. Drafting is an LLM step; showing the card must not
#      depend on that same run finishing. Measured 2026-11-06: time from a buyer's
#      message to a draft was 0.1-1.2 minutes, but time to *show* it was unbounded —
#      one draft sat undelivered because the run that wrote it never posted it.
#      --quiet on purpose: this must not change the monitor's stdout, or every tick
#      would look "changed" and wake the agent.
#
#   2. POLL. Prints a digest ONLY when a buyer spoke, so a quiet tick wakes nobody
#      and costs zero tokens.
#
# Keep this dumb: all logic lives in messaging/.

set -uo pipefail
export HERMES_HOME=/home/hermes/hermes/home
M="$HERMES_HOME/messaging"
P=/home/hermes/hermes-agent/.venv/bin/python3

[ -d "$M" ] || { echo "msg_poll: missing $M" >&2; exit 2; }

# Drop anything older than Jacob's freshness rule before delivering: a weeks-old thread
# presented as today's work is noise, and it also holds the one-card gate shut.
"$P" "$M/msgctl.py" sweep --hours "${MSG_STALE_HOURS:-24}" >/dev/null 2>&1 || true

"$P" "$M/msgctl.py" deliver --quiet || true

# providers to poll. fb rides the same tick: its provider is deterministic
# (DOM over CDP, no model), so a second marketplace costs no tokens.
for prov in dba fb; do
  "$P" "$M/poll.py" "$prov" || echo "msg_poll: $prov failed (see lane log)" >&2
done
