# Database routing — many databases, one pipeline

A second-hand operation does not sell one kind of thing. Bikes and bike spares have
their own databases with their own properties; everything else lives in a general
sales database. The pipeline must not care which database an item belongs to.

**Routing is configuration.** No database id may be hardcoded in a module.

## 1. The configuration

Two environment keys cover the common case:

```bash
# fallback for anything no rule matches
NOTION_DATABASE_DEFAULT=<data_source_id>

# keyword=database_id, comma separated; matched in order against item["category"]
NOTION_DATABASE_ROUTES=bike=<bikes_db>,wheel=<bikes_db>,spare=<spares_db>
```

Anything more elaborate goes in a JSON file and `NOTION_ROUTING_FILE` points at it:

```json
{
  "default": "<general_db>",
  "routes": [
    { "match": { "category": ["bike", "bicycle", "wheel"] }, "database": "<bikes_db>" },
    { "match": { "category": ["spare", "part"] },           "database": "<spares_db>" },
    { "match": { "category": ["*"] },                       "database": "<general_db>" }
  ]
}
```

`*` matches everything, so it belongs last. A `match` entry may use any item key, not
just `category` — `provider`, `kind`, `seller` all work.

## 2. Resolution order

1. **Explicit override** — `item["database"]` / `item["database_id"]` wins outright.
2. **First matching rule**, in configuration order.
3. **The default.**
4. If none of those exist → **raise**. Never pick a database to keep going.

That last rule is the important one: silently filing a kitchen appliance into the
bike-spares database is worse than failing the publish.

## 3. Reference implementation

`src/registry/routing.py`

```python
from registry.routing import Router

router = Router.from_env()            # or Router.from_file("/etc/.../routing.json")
database = router.database_for({"category": "Bike Wheel"})
```

```bash
python3 src/registry/routing.py '{"category": "monitor"}'
```

## 4. This deployment

Reference only — a different install supplies its own ids.

| Items | Database |
|---|---|
| bikes, wheels, bike frames | `Bikes Database` `d5186cc7-de2f-494a-b972-64e739da3ad4` |
| bike spare parts | `Spare parts` `34a1a17d-fe88-800d-8195-000bc19d4772` |
| **everything else** | `secondhand-ops_DB_SSoT` `8c31a17d-fe88-838d-8c29-078f29d0b23c` |

## 5. The general database's contract

`secondhand-ops_DB_SSoT` — properties that matter to publishing:

| Property | Type | Written by |
|---|---|---|
| `Name` | title | the operator |
| `STOCK` | select: `UNLISTED` · `LISTED` · `SOLD` | operator sets `UNLISTED`; publishing sets `LISTED`; a sale sets `SOLD` |
| `Price (DKK)` | number | the operator (asking price) |
| `Listing` | rich_text | **the ad body text** — final, publishable |
| `Photo` | files | the operator attaches images to the row; the publisher downloads them |
| `DB_number` | rich_text | **written after publish** — the marketplace's own item id |
| `DBA URL` / `Facebook URL` / `URL` | url | written after publish |
| `Sold for (DKK)`, `Sold date` | number, date | written on sale |
| `🚴🏼‍♀️ Bike` | relation | empty for non-bike items — this database holds everything except bikes and bike spares |

Only three states exist: `UNLISTED` (drafted), `LISTED` (live), `SOLD` (gone). No
`TO BE SOLD`, no `IN STOCK`, no `ORDERED` — those were collapsed deliberately. A
publisher never acts on `SOLD`.
