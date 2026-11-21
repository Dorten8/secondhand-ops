# Running the browser Hermes drives

Publishing attaches to a real browser over CDP (`127.0.0.1:9222`). When that browser
is not running, every publish path is dead — and the failure is silent, because a
missing port looks exactly like a broken script.

## The launcher

Installed system-wide, so it is in the app grid as **Brave — Hermes Debug**:

```
/usr/local/bin/brave-hermes-debug            # start
/usr/local/bin/brave-hermes-debug --stop     # stop the debug instance
/usr/local/bin/brave-hermes-debug --check    # diagnose, changes nothing
```

No `sudo` is needed. That was only ever required to *install* the launcher, because
the installing account was not the desktop user.

## Why it copies the profile

It runs from `~/.cache/brave-hermes-debug/Brave-Browser`, a **copy** of the real
profile (made on the first run). Consequences, all deliberate:

- your normal Brave keeps running, tabs untouched — the launcher never kills it;
- the debug instance carries the same logged-in sessions (DBA, Facebook);
- a crash in an automated flow cannot corrupt the profile you actually use;
- deleting `~/.cache/brave-hermes-debug` resets everything, at the cost of one recopy.

## Escape hatches, in the order they are worth trying

| Symptom | Meaning | Fix |
|---|---|---|
| `Missing X server or $DISPLAY` | no desktop session in that shell | run it from your desktop; the script auto-detects `/tmp/.X11-unix/X*` |
| `Authorization required, but no authorization protocol specified` | display found, but not your X authority | run as the desktop user, not root and not another account |
| port never answers | Brave started but the flag was swallowed | an instance is already running; quit it first or use `--stop` |
| 9222 answers, but no dba/fb tabs | working browser, empty/wrong profile | check `--check` output for which profile is in use |

## One line, no script

If the launcher is ever missing:

```bash
/opt/brave.com/brave/brave --remote-debugging-port=9222 \
  --remote-allow-origins='*' --no-first-run &
curl -s http://127.0.0.1:9222/json/version   # must answer
```

The `--remote-allow-origins='*'` flag matters: newer Chromium refuses websocket
clients without it, and the refusal shows up as an empty `/json/list` rather than an
error.
