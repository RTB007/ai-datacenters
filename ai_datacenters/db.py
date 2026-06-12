"""SQLite schema and helpers for ai-datacenters.

Two tables hold curated state, one holds per-call history:

  dc_projects   one row per tracked datacenter (the table the UI renders)
  dc_checks     full history of per-project "what's new" Grok calls
  discoveries   full history of "find the biggest" Grok calls
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS dc_projects (
    id                       INTEGER PRIMARY KEY,
    slug                     TEXT NOT NULL UNIQUE,
    name                     TEXT NOT NULL,
    owner                    TEXT,
    location                 TEXT,
    claimed_mw               REAL,
    last_known_status        TEXT,
    latest_summary           TEXT,
    last_checked_at          TEXT,
    discovered_at            TEXT NOT NULL DEFAULT (datetime('now')),
    first_announcement_date  TEXT,
    live_date                TEXT,
    notes                    TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_claimed_mw ON dc_projects(claimed_mw DESC);

CREATE TABLE IF NOT EXISTS dc_checks (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES dc_projects(id) ON DELETE CASCADE,
    checked_at      TEXT NOT NULL DEFAULT (datetime('now')),
    prompt          TEXT NOT NULL,
    response_text   TEXT NOT NULL,
    citations_json  TEXT,
    parsed_json     TEXT,
    model           TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cost_usd        REAL
);
CREATE INDEX IF NOT EXISTS idx_checks_project_date ON dc_checks(project_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS discoveries (
    id              INTEGER PRIMARY KEY,
    discovered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    prompt          TEXT NOT NULL,
    response_text   TEXT NOT NULL,
    citations_json  TEXT,
    projects_added  INTEGER NOT NULL DEFAULT 0,
    model           TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cost_usd        REAL
);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        # Idempotent migration: add new columns to pre-existing DBs.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(dc_projects)")}
        for col in ("first_announcement_date", "live_date"):
            if col not in existing:
                conn.execute(f"ALTER TABLE dc_projects ADD COLUMN {col} TEXT")


def list_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM dc_projects ORDER BY claimed_mw DESC NULLS LAST, name"
    )
    return [dict(row) for row in cur]


def get_project(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM dc_projects WHERE slug = ?", (slug,))
    row = cur.fetchone()
    return dict(row) if row else None


def upsert_project(conn: sqlite3.Connection, p: dict[str, Any]) -> tuple[int, bool]:
    """Upsert by slug. Returns (id, was_inserted)."""
    cur = conn.execute("SELECT id FROM dc_projects WHERE slug = ?", (p["slug"],))
    existing = cur.fetchone()
    if existing:
        conn.execute(
            "UPDATE dc_projects SET name=?, owner=?, location=?, claimed_mw=?, "
            "last_known_status=COALESCE(?, last_known_status), "
            "first_announcement_date=COALESCE(?, first_announcement_date), "
            "live_date=COALESCE(?, live_date), "
            "notes=COALESCE(?, notes) "
            "WHERE id=?",
            (
                p["name"],
                p.get("owner"),
                p.get("location"),
                p.get("claimed_mw"),
                p.get("status") or p.get("last_known_status"),
                p.get("first_announcement_date"),
                p.get("live_date"),
                p.get("notes"),
                existing["id"],
            ),
        )
        return existing["id"], False
    cur = conn.execute(
        "INSERT INTO dc_projects(slug, name, owner, location, claimed_mw, "
        "last_known_status, first_announcement_date, live_date, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            p["slug"],
            p["name"],
            p.get("owner"),
            p.get("location"),
            p.get("claimed_mw"),
            p.get("status") or p.get("last_known_status"),
            p.get("first_announcement_date"),
            p.get("live_date"),
            p.get("notes"),
        ),
    )
    return cur.lastrowid, True


def record_check(
    conn: sqlite3.Connection,
    project_id: int,
    prompt: str,
    response_text: str,
    citations: list[dict[str, Any]] | None,
    parsed: dict[str, Any] | None,
    model: str,
    tokens_in: int | None,
    tokens_out: int | None,
    cost_usd: float | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO dc_checks(project_id, prompt, response_text, citations_json, "
        "parsed_json, model, tokens_in, tokens_out, cost_usd) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            prompt,
            response_text,
            json.dumps(citations) if citations else None,
            json.dumps(parsed) if parsed else None,
            model,
            tokens_in,
            tokens_out,
            cost_usd,
        ),
    )
    return cur.lastrowid


def apply_check_to_project(
    conn: sqlite3.Connection,
    project_id: int,
    new_status: str | None,
    summary: str | None,
    first_announcement_date: str | None = None,
    live_date: str | None = None,
) -> None:
    conn.execute(
        "UPDATE dc_projects SET "
        "last_known_status         = COALESCE(?, last_known_status), "
        "latest_summary            = COALESCE(?, latest_summary), "
        "first_announcement_date   = COALESCE(?, first_announcement_date), "
        "live_date                 = COALESCE(?, live_date), "
        "last_checked_at           = datetime('now') "
        "WHERE id = ?",
        (new_status, summary, first_announcement_date, live_date, project_id),
    )


def record_discovery(
    conn: sqlite3.Connection,
    prompt: str,
    response_text: str,
    citations: list[dict[str, Any]] | None,
    projects_added: int,
    model: str,
    tokens_in: int | None,
    tokens_out: int | None,
    cost_usd: float | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO discoveries(prompt, response_text, citations_json, "
        "projects_added, model, tokens_in, tokens_out, cost_usd) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            prompt,
            response_text,
            json.dumps(citations) if citations else None,
            projects_added,
            model,
            tokens_in,
            tokens_out,
            cost_usd,
        ),
    )
    return cur.lastrowid


def recent_checks(
    conn: sqlite3.Connection, project_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT id, checked_at, response_text, citations_json, parsed_json, cost_usd "
        "FROM dc_checks WHERE project_id = ? ORDER BY checked_at DESC LIMIT ?",
        (project_id, limit),
    )
    out = []
    for row in cur:
        d = dict(row)
        d["citations"] = json.loads(d.pop("citations_json")) if d["citations_json"] else []
        d["parsed"] = json.loads(d.pop("parsed_json")) if d["parsed_json"] else None
        out.append(d)
    return out
