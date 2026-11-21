#!/usr/bin/env python3
"""
routing.py — which Notion database an item belongs in.

A second-hand operation rarely sells one kind of thing. Bikes and bike spares
live in a parts/inventory database; everything else lives in a general sales
database with different properties. Both are Notion databases; the pipeline
should not care which is which.

Routing is CONFIG, never code:

    # .env
    NOTION_DATABASE_DEFAULT=<data_source_id or database_id>
    NOTION_DATABASE_ROUTES=bikes=<id>,spares=<id>,general=<id>

or point at a JSON file for anything more elaborate:

    NOTION_ROUTING_FILE=/etc/secondhand-ops/routing.json

    {
      "default": "db-default-id",
      "routes": [
        {"match": {"category": ["bike", "bicycle", "wheel", "spare"]}, "database": "db-bikes-id"},
        {"match": {"category": ["*"]}, "database": "db-general-id"}
      ]
    }

Resolution order: explicit item override → first matching rule → default.
Nothing here hardcodes an id; a different install supplies its own file.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class RoutingError(Exception):
    """Raised when an item cannot be routed and there is no default."""


@dataclass
class Rule:
    match: dict[str, list[str]]
    database: str

    def matches(self, item: dict[str, Any]) -> bool:
        for key, wanted in self.match.items():
            value = str(item.get(key, "") or "").lower()
            if "*" in wanted:
                continue
            if not any(w.lower() in value for w in wanted):
                return False
        return True


@dataclass
class Router:
    default: str | None = None
    rules: list[Rule] = field(default_factory=list)

    # ---- construction -------------------------------------------------
    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Router":
        """Build from environment; falls back to the operator's .env file.

        A library caller (not started by a shell that sourced .env) would
        otherwise silently get an empty router and fail on every item, so the
        .env file is read when the keys are absent from the process environment.
        """
        env = env if env is not None else dict(os.environ)
        if not any(k in env for k in ("NOTION_ROUTING_FILE", "NOTION_DATABASE_DEFAULT", "NOTION_DATABASE_ROUTES")):
            file_env = {k: v for k, v in _load_env_file().items() if k.startswith("NOTION_")}
            env = {**file_env, **env}

        path = env.get("NOTION_ROUTING_FILE", "").strip()
        if path:
            return cls.from_file(path)

        default = env.get("NOTION_DATABASE_DEFAULT", "").strip() or None
        routes = env.get("NOTION_DATABASE_ROUTES", "").strip()

        rules: list[Rule] = []
        for chunk in filter(None, (c.strip() for c in routes.split(","))):
            if "=" not in chunk:
                raise RoutingError(
                    f"NOTION_DATABASE_ROUTES entry {chunk!r} is not key=database_id"
                )
            key, db = (p.strip() for p in chunk.split("=", 1))
            if not db:
                raise RoutingError(f"empty database id for route {key!r}")
            # a bare key is a category keyword match
            rules.append(Rule(match={"category": [key]}, database=db))

        return cls(default=default, rules=rules)

    @classmethod
    def from_file(cls, path: str | Path) -> "Router":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rules = [
            Rule(match=r.get("match", {}), database=r["database"])
            for r in data.get("routes", [])
        ]
        return cls(default=data.get("default"), rules=rules)

    # ---- resolution ---------------------------------------------------
    def database_for(self, item: dict[str, Any]) -> str:
        """Return the database id for one item dict. Raises if unroutable."""
        override = item.get("database") or item.get("database_id")
        if override:
            return str(override)

        for rule in self.rules:
            if rule.matches(item):
                return rule.database

        if self.default:
            return self.default

        raise RoutingError(
            "no route matched and NOTION_DATABASE_DEFAULT is unset; "
            f"item keys={sorted(item)} — add a route rather than guessing"
        )


def main(argv: list[str] | None = None) -> int:
    """CLI: routing.py '{"category": "wheel"}' -> prints the database id."""
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    item = json.loads(argv[0])
    router = Router.from_env(
        {k: v for k, v in os.environ.items() if k.startswith("NOTION_")}
        | {k: v for k, v in _load_env_file().items() if k.startswith("NOTION_")}
    )
    print(router.database_for(item))
    return 0


def _load_env_file() -> dict[str, str]:
    """Read the caller's .env so the CLI behaves like the pipeline."""
    for candidate in (
        os.environ.get("ENV_FILE", ""),
        str(Path.home() / "hermes" / "home" / ".env"),
        str(Path.home() / ".hermes" / ".env"),
        ".env",
    ):
        if not candidate:
            continue
        p = Path(candidate)
        if not p.is_file():
            continue
        out = {}
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
        return out
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
