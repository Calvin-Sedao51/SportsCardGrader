"""SQLite storage for releases, cards and sync runs (stdlib sqlite3 only)."""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from app.sportsdb.models import Card, Release

# server/data/sportsdb.sqlite — gitignored; override with SPORTSDB_PATH.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sportsdb.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    id            INTEGER PRIMARY KEY,
    manufacturer  TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    name          TEXT NOT NULL,
    sport         TEXT NOT NULL,
    year          INTEGER,
    release_date  TEXT,
    product_type  TEXT,
    url           TEXT,
    image_url     TEXT,
    checklist_url TEXT,
    description   TEXT,
    raw           TEXT NOT NULL DEFAULT '{}',
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    checklist_synced_at TEXT,
    UNIQUE (manufacturer, source_id)
);
CREATE INDEX IF NOT EXISTS releases_sport_year ON releases (sport, year);

CREATE TABLE IF NOT EXISTS cards (
    id           INTEGER PRIMARY KEY,
    release_id   INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    card_number  TEXT NOT NULL,
    player       TEXT NOT NULL,
    player_norm  TEXT NOT NULL,
    team         TEXT,
    subset       TEXT NOT NULL DEFAULT '',
    parallel     TEXT NOT NULL DEFAULT '',
    print_run    INTEGER,
    is_rookie    INTEGER NOT NULL DEFAULT 0,
    is_auto      INTEGER NOT NULL DEFAULT 0,
    is_relic     INTEGER NOT NULL DEFAULT 0,
    raw          TEXT NOT NULL DEFAULT '{}',
    UNIQUE (release_id, card_number, subset, parallel)
);
CREATE INDEX IF NOT EXISTS cards_player_norm ON cards (player_norm);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    manufacturer    TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,          -- ok | blocked | error
    releases_seen   INTEGER NOT NULL DEFAULT 0,
    releases_new    INTEGER NOT NULL DEFAULT 0,
    cards_new       INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_player(value: str) -> str:
    """Lowercase alnum words only; mirrors app.catalog.match.norm_text."""
    from app.catalog.match import norm_text
    return norm_text(value)


def db_path() -> Path:
    return Path(os.environ.get("SPORTSDB_PATH") or DEFAULT_DB_PATH)


class SportsDb:
    def __init__(self, path: Optional[os.PathLike | str] = None):
        self.path = Path(path) if path else db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- releases ---

    def upsert_release(self, release: Release) -> tuple[int, bool]:
        """Insert or refresh by (manufacturer, source_id). Returns (id, is_new)."""
        ts = now_iso()
        row = release.to_row()
        row["raw"] = json.dumps(row["raw"], sort_keys=True, default=str)
        existing = self._conn.execute(
            "SELECT id, name, sport, raw FROM releases WHERE manufacturer = ? AND source_id = ?",
            (release.manufacturer, release.source_id)).fetchone()
        if existing:
            # Never overwrite known values with None from a thinner page, and
            # don't let a slug-derived name (sitemap-only pass) replace a
            # richer one read from the product page earlier.
            if len(row["name"]) < len(existing["name"]):
                row["name"] = existing["name"]
            if row["sport"] == "other":
                row["sport"] = existing["sport"]
            if row["raw"] == "{}":
                row["raw"] = existing["raw"]
            self._conn.execute(
                """UPDATE releases SET name = ?, sport = ?,
                   year = COALESCE(?, year), release_date = COALESCE(?, release_date),
                   product_type = COALESCE(?, product_type), url = COALESCE(?, url),
                   image_url = COALESCE(?, image_url),
                   checklist_url = COALESCE(?, checklist_url),
                   description = COALESCE(?, description), raw = ?, last_seen = ?
                   WHERE id = ?""",
                (row["name"], row["sport"], row["year"], row["release_date"],
                 row["product_type"], row["url"], row["image_url"], row["checklist_url"],
                 row["description"], row["raw"], ts, existing["id"]))
            self._conn.commit()
            return existing["id"], False
        cur = self._conn.execute(
            """INSERT INTO releases (manufacturer, source_id, name, sport, year,
               release_date, product_type, url, image_url, checklist_url, description,
               raw, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["manufacturer"], row["source_id"], row["name"], row["sport"], row["year"],
             row["release_date"], row["product_type"], row["url"], row["image_url"],
             row["checklist_url"], row["description"], row["raw"], ts, ts))
        self._conn.commit()
        return cur.lastrowid, True

    def releases(self, manufacturer: Optional[str] = None, since_days: Optional[int] = None,
                 limit: Optional[int] = None) -> list[sqlite3.Row]:
        sql, params = "SELECT * FROM releases", []
        clauses = []
        if manufacturer:
            clauses.append("manufacturer = ?")
            params.append(manufacturer)
        if since_days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()
            # Undated releases count as recent only if we first saw them recently.
            clauses.append("(release_date >= ? OR (release_date IS NULL AND first_seen >= ?))")
            params += [cutoff, cutoff]
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(release_date, first_seen) DESC, id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self._conn.execute(sql, params).fetchall()

    def mark_checklist_synced(self, release_id: int) -> None:
        self._conn.execute("UPDATE releases SET checklist_synced_at = ? WHERE id = ?",
                           (now_iso(), release_id))
        self._conn.commit()

    # --- cards ---

    def upsert_cards(self, release_id: int, cards: Iterable[Card]) -> int:
        """Insert cards that aren't there yet. Returns the number inserted."""
        new = 0
        for card in cards:
            if not card.card_number or not card.player:
                continue
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO cards (release_id, card_number, player, player_norm,
                   team, subset, parallel, print_run, is_rookie, is_auto, is_relic, raw)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (release_id, card.card_number.strip(), card.player.strip(),
                 norm_player(card.player), card.team, card.subset or "", card.parallel or "",
                 card.print_run, int(card.is_rookie), int(card.is_auto), int(card.is_relic),
                 json.dumps(card.raw, sort_keys=True, default=str)))
            new += cur.rowcount
        self._conn.commit()
        return new

    def search_cards(self, player: str, year: Optional[int] = None,
                     sport: Optional[str] = None, limit: int = 50) -> list[sqlite3.Row]:
        """Cards whose normalized player name contains every word of the query."""
        words = norm_player(player).split()
        if not words:
            return []
        sql = """SELECT c.*, r.name AS release_name, r.manufacturer, r.sport, r.year,
                        r.url AS release_url, r.image_url
                 FROM cards c JOIN releases r ON r.id = c.release_id WHERE 1=1"""
        params: list = []
        for w in words:
            sql += " AND c.player_norm LIKE ?"
            params.append(f"%{w}%")
        if year is not None:
            sql += " AND r.year BETWEEN ? AND ?"
            params += [year - 1, year]   # a 2024-25 hockey set is stored as 2024
        if sport:
            sql += " AND r.sport = ?"
            params.append(sport)
        sql += f" ORDER BY r.year DESC LIMIT {int(limit)}"
        return self._conn.execute(sql, params).fetchall()

    # --- runs ---

    def start_run(self, manufacturer: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (manufacturer, started_at, status) VALUES (?, ?, 'running')",
            (manufacturer, now_iso()))
        self._conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, *, releases_seen: int = 0,
                   releases_new: int = 0, cards_new: int = 0, error: Optional[str] = None) -> None:
        self._conn.execute(
            """UPDATE runs SET finished_at = ?, status = ?, releases_seen = ?,
               releases_new = ?, cards_new = ?, error = ? WHERE id = ?""",
            (now_iso(), status, releases_seen, releases_new, cards_new, error, run_id))
        self._conn.commit()

    def last_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def stats(self) -> dict:
        by_mfr = {r["manufacturer"]: r["n"] for r in self._conn.execute(
            "SELECT manufacturer, COUNT(*) AS n FROM releases GROUP BY manufacturer")}
        by_sport = {r["sport"]: r["n"] for r in self._conn.execute(
            "SELECT sport, COUNT(*) AS n FROM releases GROUP BY sport")}
        cards = self._conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
        return {"db": str(self.path), "releases": sum(by_mfr.values()),
                "releases_by_manufacturer": by_mfr, "releases_by_sport": by_sport,
                "cards": cards}
