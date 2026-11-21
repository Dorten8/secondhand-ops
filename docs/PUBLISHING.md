# Publishing one item, end to end

One command, no editor in between:

```bash
python3 bin/publish_item.py --db-number 0002 --category mobile_phone \
    --marketplaces dba,fb --endpoint http://127.0.0.1:9222 --confirm
```

It: builds the payload from the database row → downloads the row's photos → runs each
marketplace CLI → pulls the real listing URL out of the marketplace's own output →
writes `DBA URL` / `Facebook URL` and `STOCK=LISTED` back into the row → reads the row
back and prints what it stored.

`--payload-only` shows what would be published without touching a browser.
`--dry-run` fills the form and stops before the irreversible click.

## What a machine needs to run it

None of this is Hermes-specific — it needs a browser and a token, not an agent:

1. **This repo**, and `python3` with `websockets` (`bin/cdp.py` is the only dependency).
2. **`NOTION_API_KEY`** in the environment, plus `NOTION_DATABASE_DEFAULT`
   (the sales data source id). Without it, payload build and writeback both fail loudly.
3. **A browser with a debugging port**, signed into DBA and Facebook for the account
   that should own the listings. There is no cookie jar: the login is the credential.
   `--endpoint` points at it, so it can be that machine's own browser.
4. **A category key** in `config/marketplace-categories.json`. Marketplace naming is
   per-marketplace (DBA uses numeric path codes, Facebook uses labels), so it is
   configuration, not code. Add a key before publishing a new kind of item.

## Why each marketplace is independent

A Facebook failure must not undo a DBA success. The two run in sequence, each reports
its own result and URL, and only marketplaces that returned a real URL are written to
the row. Exit code is 1 if any marketplace failed, so a caller can tell.

## The thing that is NOT automated, on purpose

The final click. `--confirm` is required, because publishing is customer-facing and
announcement-shaped. `--dry-run` exists so the form can be inspected first.

## Known asymmetry

DBA's wizard is stable over dozens of navigations. Facebook's is not: the create form
moved "Description" behind a collapsed "More details" row, renders condition options
as a listbox rather than a dialog, and keeps hidden zero-size copies of nav labels in
the DOM. All of that is handled in `bin/fb_publish.py`, but Facebook changes its flow
without warning — treat a Facebook run as the one that needs eyes on it.
