#!/bin/bash
# msg_poll.sh — cron pre-run script for the marketplace messaging lane.
#
# Contract: prints NOTHING when no buyer spoke, so the cron job wakes nobody and
# costs zero tokens. Prints a digest only when there is something to draft.
#
# Keep this dumb: all logic lives in messaging/poll.py.

set -uo pipefail
export HERMES_HOME=/home/hermes/hermes/home
M="$HERMES_HOME/messaging"
P=/home/hermes/hermes-agent/.venv/bin/python3

[ -d "$M" ] || { echo "msg_poll: missing $M" >&2; exit 2; }

# providers to poll; add "fb" here once providers/fb.py is implemented
for prov in dba; do
  "$P" "$M/poll.py" "$prov"
done
