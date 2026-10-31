# secondhand-ops

An autonomous, human-in-the-loop secondhand sales platform for online marketplaces (DBA.dk & Facebook Marketplace).

It manages the entire lifecycle: **intake, bilingual catalog creation, relational bundle cascades, multi-platform listing, inbox polling, reply drafting, human approval gate, and verified delivery.**

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) for the end-to-end design philosophy, stage boundaries, and the access strategy.

---

## 🏛️ Pipeline Architecture

```
 capture  →  register  →  list    →  poll   →  draft  →  approve  →  send   →  verify
 ad text     items      publish     inbox      reply     human       deliver   prove
             .json
```

The pipeline is split into two complementary lanes that meet through a single atomic WAL seam (`/opt/data/marketplace/items.json`):

1. **Listing Lane (`listing/` + `listctl.py`)**:
   - Audio memo + photo intake and LLM extraction (`listing/catalog.py`).
   - Title standardization: `WHAT - WHATTYPE - MAKE`.
   - Unified bilingual copy (Danish + English + specs + pickup terms).
   - Relational inventory graph & mutual invalidation (`listing/inventory.py`): e.g., Wheelset `WS01` ⟷ Front `WF03` + Rear `WRC06`.
   - Multi-platform publishing with quota guards (`batch_publisher.py`).
   - Atomic WAL status emitter (`listctl.py sold <ITEM_ID>`).
   - Optional decoupled Notion SSoT bridge (`listing/integrations/notion.py`).

2. **Messaging Lane (`messaging/` + `msgctl.py`)**:
   - Zero-token HTTP inbox poll (`messaging/poll.py`).
   - LLM reply drafting (`messaging/msgctl.py`).
   - Strict Human-in-the-Loop approval gate (enforced in code, not prompt).
   - Authenticated REST send with post-send API delivery verification.
   - Stateless read of `items.json` to close buyer drafts for sold/withdrawn items.

---

## 🚀 Quickstart

See [SETUP.md](SETUP.md) for complete prerequisite checks and environment configuration.

```bash
# 1. Clone repository
git clone git@github.com:Dorten8/secondhand-ops.git
cd secondhand-ops

# 2. Configure credentials
cp .env.example .env

# 3. Listing Ops: Inspect current inventory
python3 listctl.py ls

# 4. Listing Ops: Mark an item sold (automatically cascades to constituent parts)
python3 listctl.py sold WRS06

# 5. Messaging Ops: Poll inbox and check pending threads
python3 messaging/msgctl.py poll
python3 messaging/msgctl.py pending
```

---

## 🛠️ Modularity & Harness Agnosticism

This codebase is deliberately modular, stateless, and harness-agnostic:
- **Zero Coupling Across Internal State**: Stages interact exclusively via files and rows (`items.json`, SQLite store, or stdout JSON).
- **Runs Everywhere**: Supported by Antigravity, Cursor, VS Code, Claude Code, or scheduled cron daemons.
- **Access Strategy**:
  - *Primary*: Authenticated HTTP + exported cookies (fast, headless, zero tokens, deterministic).
  - *Fallback / Bootstrap*: Native Chrome DevTools Protocol (CDP) for initial login capture and dynamic DOM operations.
- **Safety First**: Outbound messages NEVER leave without explicit human approval (`msgctl.py approve`).
