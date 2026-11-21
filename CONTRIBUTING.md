# Working agreement — how changes reach this repo

This repo is shared by more than one agent. The rule that keeps it from rotting is
short: **work in commits, land them, and tell the others to pull.**

## 1. Every change is a commit, pushed immediately

- One concern per commit. The message says *why*, not just what.
- Push as soon as it is coherent. An unpushed local change is invisible to every other
  agent and will be lost the moment that machine is replaced.
- No long-lived branches for this work. `main` is the trunk; work lands on it.
- Never force-push `main`. If you must rewrite, say so in the channel first.

## 2. Pull before you start, and after anyone tells you to

```bash
git pull --ff-only
```

`--ff-only` is deliberate: it refuses rather than creating a merge commit when
histories diverge, so a divergence is *visible* instead of silently papered over.
Any scheduled job that touches this repo pulls first and continues on failure — a
stale working copy must never stop a job, but it also must never be trusted blindly.

## 3. Announce the pull, don't assume it

After pushing something another agent depends on, say so in the channel:

> `secondhand-ops` @ `<sha>` — pulled and pushed. Others: `git pull --ff-only`.

That single line is the handover for code. Silence after a push is how two agents end
up building against different versions of the same seam.

## 4. Ownership boundaries survive a push

Pushing is not permission. Two lanes touch this repo and they must not edit each
other's half:

- the **publishing/listing** lane writes the registry and publishes ads;
- the **conversations** lane reads the registry and never writes it.

Both may commit docs, routing config and tests. Neither may quietly change the other's
guarantees — that is a decision for the operator, recorded in `docs/`.

## 5. What a commit must leave behind

- the code change itself
- a doc line where a *reader* would look for it (`docs/`, or the section it affects)
- proof the thing still runs — a command whose output you actually saw

If a change cannot show that third item, it is a draft, not a commit.

## 6. Verification beats intent

Never report "done" from a plausible-looking edit. Read the file back, run the script,
query the API. A claim without an artifact is hearsay, and hearsay is how a repo
acquires a doc that describes a system that does not exist.
