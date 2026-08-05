"""Persistance SQLite : cache des réponses et mémoire des annonces déjà vues.

Deux choses distinctes vivent ici, et il ne faut pas les confondre :

- le **cache de réponses** évite de refetch la même page pendant `CACHE_TTL` ;
- la **mémoire des vues** retient les annonces déjà affichées, pour que `watch`
  ne renvoie que les nouveautés. C'est elle qui fait l'économie de tokens sur la
  durée : au bout de quelques runs, un `watch` renvoie souvent zéro ligne.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
import time
from typing import Any, Iterable

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    ad_id      TEXT PRIMARY KEY,
    domain     TEXT DEFAULT '',
    title      TEXT DEFAULT '',
    price      INTEGER,
    deal_score REAL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_domain ON seen(domain);
CREATE INDEX IF NOT EXISTS idx_responses_fetched ON responses(fetched_at);
"""


def cache_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class Store:
    """Accès SQLite. Utilisable en gestionnaire de contexte."""

    def __init__(self, path=None):
        config.ensure_dirs()
        self.path = str(path or config.CACHE_DB)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # -- Cache de réponses ----------------------------------------------------

    def get_response(self, key: str, ttl: int | None = None) -> dict | None:
        ttl = config.CACHE_TTL if ttl is None else ttl
        if ttl <= 0:
            return None
        row = self.conn.execute(
            "SELECT payload, fetched_at FROM responses WHERE key = ?", (key,)
        ).fetchone()
        if row is None or (time.time() - row["fetched_at"]) > ttl:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    def put_response(self, key: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO responses (key, payload, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(payload, ensure_ascii=False), time.time()),
        )
        self.conn.commit()

    def purge_expired(self, ttl: int | None = None) -> int:
        ttl = config.CACHE_TTL if ttl is None else ttl
        cur = self.conn.execute(
            "DELETE FROM responses WHERE fetched_at < ?", (time.time() - ttl,)
        )
        self.conn.commit()
        return cur.rowcount

    # -- Mémoire des annonces vues -------------------------------------------

    def is_seen(self, ad_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen WHERE ad_id = ?", (str(ad_id),)
        ).fetchone()
        return row is not None

    def filter_unseen(self, listings: Iterable) -> list:
        """Ne garde que les annonces jamais affichées."""
        items = list(listings)
        if not items:
            return []
        known = self.seen_ids([item.id for item in items])
        return [item for item in items if item.id not in known]

    def seen_ids(self, ad_ids: Iterable[str]) -> set[str]:
        ids = [str(i) for i in ad_ids if i]
        if not ids:
            return set()
        found: set[str] = set()
        # SQLite plafonne le nombre de paramètres ; on découpe.
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT ad_id FROM seen WHERE ad_id IN ({placeholders})", chunk
            ).fetchall()
            found.update(r["ad_id"] for r in rows)
        return found

    def mark_seen(self, listings: Iterable, domain: str = "") -> int:
        now = time.time()
        rows = [
            (
                item.id,
                domain,
                item.title[:200],
                item.price,
                round(item.deal_score, 2),
                now,
                now,
            )
            for item in listings
            if item.id
        ]
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO seen (ad_id, domain, title, price, deal_score, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ad_id) DO UPDATE SET
                last_seen  = excluded.last_seen,
                deal_score = excluded.deal_score,
                price      = excluded.price
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def forget(self, ad_ids: Iterable[str] | None = None, domain: str | None = None) -> int:
        if ad_ids:
            ids = [str(i) for i in ad_ids]
            placeholders = ",".join("?" * len(ids))
            cur = self.conn.execute(
                f"DELETE FROM seen WHERE ad_id IN ({placeholders})", ids
            )
        elif domain:
            cur = self.conn.execute("DELETE FROM seen WHERE domain = ?", (domain,))
        else:
            cur = self.conn.execute("DELETE FROM seen")
        self.conn.commit()
        return cur.rowcount

    def seen_stats(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT domain, COUNT(*) AS n, MAX(last_seen) AS last
            FROM seen GROUP BY domain ORDER BY n DESC
            """
        ).fetchall()
        out = []
        for r in rows:
            last = (
                _dt.datetime.fromtimestamp(r["last"], _dt.timezone.utc).isoformat(
                    timespec="minutes"
                )
                if r["last"]
                else ""
            )
            out.append({"domain": r["domain"] or "(aucun)", "count": r["n"], "last_seen": last})
        return out
