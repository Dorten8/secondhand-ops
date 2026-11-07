# Setup & Quickstart Guide

This repository contains the complete intake, catalog generation, relational inventory management, and multi-platform publishing engine for secondhand marketplace automation.

It is designed to be **harness-agnostic** — runnable via Antigravity, Cursor, Claude Code, VS Code tasks, or standalone CLI.

---

## 1. Prerequisites

- **Python 3.10+**
- **Chromium / Google Chrome / Brave Browser** (for CDP-based marketplace automation and session persistence)
- **Node.js / Playwright** (optional, for headless browser automation scripts)

---

## 2. Environment Configuration

Copy the example environment file and set your private credentials:

```bash
cp .env.example .env
```

Configuration variables in `.env`:

| Key | Description | Required? |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API key for photo and voice note intake | Recommended |
| `OPENAI_API_KEY` | Alternative LLM provider key | Optional |
| `MARKETPLACE_REGISTRY` | Path to shared `items.json` atomic WAL file (default: `/opt/data/marketplace/items.json`) | Optional |
| `NOTION_TOKEN` | Notion API Integration token (if syncing with private Notion SSoT) | Optional |
| `NOTION_DATABASE_ID` | Notion Database ID | Optional |
| `CDP_URL` | Chrome DevTools Protocol URL (default: `http://127.0.0.1:9222`) | Optional |
| `SELLER_LOCATION_POSTAL` | Default pickup location (e.g., `2300 København S`) | Optional |
| `PICKUP_MAP` | Google Maps link sent when a pickup is agreed | **Required to send confirmations** |
| `PICKUP_PHONE` | Phone number in the same block | **Required to send confirmations** |
| `PICKUP_PHONE_NOTE_DA` / `_EN` | Wording of the phone line per language | Optional (sensible defaults) |
| `HERMES_ENV_FILE` | Where these values are read from when cron runs lack a shell | Optional |
| `SAY_LOG` | Audit log of every delivered message and its topic | Optional |
| `TG_TOKEN_FILE` | File holding the bot token, instead of `TELEGRAM_BOT_TOKEN` | Optional |

Everything the code reads is listed in `.env.example`, and a test fails if the two
drift apart — a clone that follows `cp .env.example .env` works without reading the
source.

> **Note on Notion**: The engine runs completely standalone using local atomic WAL `items.json`. If `NOTION_TOKEN` is omitted, Notion syncing degrades gracefully without crashing.

---

## 3. Python Dependencies

Install required Python packages:

```bash
pip install -r requirements.txt
```

Or install minimal dependencies directly:

```bash
pip install requests websockets playwright
playwright install chromium
```

---

## 4. Running with AI Coding Harnesses

### A. Antigravity / Gemini CLI
To run commands directly within Antigravity:
```bash
python3 listctl.py ls
```

### B. Cursor / VS Code / Claude Code
Open the project directory in your editor. The CLI interface is standardized:
```bash
python3 listctl.py --help
```

---

## 5. Standard CLI Operations

### List Current Registered Inventory
```bash
python3 listctl.py ls
```

### Register a New Item Draft
```bash
python3 listctl.py add --provider dba --title "Front Wheel / Forhjul - 28\" Dynamonav - Shimano" --price 250
```

### Transition Draft to Published
```bash
python3 listctl.py publish --provider dba --draft-id <DRAFT_ID> --marketplace-id 25983667 --url "https://www.dba.dk/recommerce/forsale/item/25983667"
```

### Mark Item as Sold (with Automatic Relational Cascades)
When an item is sold, `listctl` automatically marks the item sold AND cascades invalidation to related bundles or constituent wheels:
```bash
python3 listctl.py sold WRS06
```

### Extract Live Unread Conversations (Zero-Token Compact JSON)
```bash
python3 listing/extractors/dba_threads.py
```
