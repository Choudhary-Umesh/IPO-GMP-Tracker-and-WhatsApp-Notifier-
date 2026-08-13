"""Lightweight SQLite layer.

The database is intentionally ephemeral: every run recreates it, stores the
Step-1 candidates, then Step 2 enriches those same rows in place. Keeping it in
SQLite (rather than passing dicts around) means each stage can be run and
debugged independently — `python -m src.investorgain_scraper` then
`python -m src.ipowatch_scraper`."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import DB_PATH

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ipos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL UNIQUE,
    normalized_name  TEXT    NOT NULL,
    issue_price      REAL,
    ig_gmp           REAL,
    ig_gmp_pct       REAL,
    close_date       TEXT,
    exchange         TEXT,
    iw_name          TEXT,
    iw_gmp           REAL,
    iw_gmp_pct       REAL,
    iw_match_score   REAL,
    created_at       TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def connect(db_path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str = DB_PATH, *, reset: bool = True) -> None:
    """Create a clean database for today's run."""
    path = Path(db_path)
    if reset and path.exists():
        path.unlink()
        log.info("Removed stale database %s", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
    log.info("Initialised database at %s", path)


def save_candidates(rows: list[dict[str, Any]], db_path: Path | str = DB_PATH) -> int:
    """Insert Step-1 (InvestorGain) matches. Upserts on company name."""
    if not rows:
        return 0

    sql = """
    INSERT INTO ipos (name, normalized_name, issue_price, ig_gmp, ig_gmp_pct,
                      close_date, exchange)
    VALUES (:name, :normalized_name, :issue_price, :ig_gmp, :ig_gmp_pct,
            :close_date, :exchange)
    ON CONFLICT(name) DO UPDATE SET
        issue_price = excluded.issue_price,
        ig_gmp      = excluded.ig_gmp,
        ig_gmp_pct  = excluded.ig_gmp_pct,
        close_date  = excluded.close_date,
        exchange    = excluded.exchange;
    """
    payload = [
        {
            "name": r["name"],
            "normalized_name": r["normalized_name"],
            "issue_price": r.get("issue_price"),
            "ig_gmp": r.get("ig_gmp"),
            "ig_gmp_pct": r.get("ig_gmp_pct"),
            "close_date": r["close_date"].isoformat() if r.get("close_date") else None,
            "exchange": r.get("exchange"),
        }
        for r in rows
    ]
    with connect(db_path) as conn:
        conn.executemany(sql, payload)
    log.info("Stored %s candidate IPO(s) in SQLite", len(payload))
    return len(payload)


def get_candidates(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ipos ORDER BY ig_gmp_pct DESC NULLS LAST"
        ).fetchall()
    return [dict(r) for r in rows]


def get_candidate_names(db_path: Path | str = DB_PATH) -> list[tuple[int, str, str]]:
    """(id, name, normalized_name) tuples for the fuzzy matcher in Step 2."""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id, name, normalized_name FROM ipos").fetchall()
    return [(r["id"], r["name"], r["normalized_name"]) for r in rows]


def update_ipowatch(
    ipo_id: int,
    *,
    iw_name: Optional[str],
    iw_gmp: Optional[float],
    iw_gmp_pct: Optional[float],
    score: Optional[float],
    db_path: Path | str = DB_PATH,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """UPDATE ipos
               SET iw_name = ?, iw_gmp = ?, iw_gmp_pct = ?, iw_match_score = ?
               WHERE id = ?""",
            (iw_name, iw_gmp, iw_gmp_pct, score, ipo_id),
        )
