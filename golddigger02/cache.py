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
import threading
import time
from typing import Any, Iterable

from . import config
from .extract import SIGNATURE_VERSION

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

# Échelle de migration pilotée par `PRAGMA user_version` : chaque entier ajoute
# ce que la version précédente n'avait pas. `user_version == 0` couvre à la fois
# une base neuve et une base créée avant ce versionnage — `_SCHEMA` est
# idempotent (`IF NOT EXISTS` partout), donc le rejouer ne perd aucune donnée.
_SCHEMA_VERSION = 2

_MIGRATIONS: dict[int, str] = {
    1: _SCHEMA,
    2: """
CREATE TABLE IF NOT EXISTS price_history (
    ad_id   TEXT    NOT NULL,
    price   INTEGER NOT NULL,
    seen_at REAL    NOT NULL,
    PRIMARY KEY (ad_id, price)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS cohort_stats (
    cohort_key TEXT PRIMARY KEY,
    domain     TEXT DEFAULT '',
    median     REAL NOT NULL,
    n          INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
""",
}


def cache_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class Store:
    """Accès SQLite. Utilisable en gestionnaire de contexte."""

    def __init__(self, path=None):
        config.ensure_dirs()
        self.path = str(path or config.CACHE_DB)
        # check_same_thread=False + verrou explicite : `research_many` interroge
        # ce même Store depuis plusieurs threads (ThreadPoolExecutor), et le
        # module sqlite3 refuse par défaut qu'une connexion traverse un thread.
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._migrate()

    def _migrate(self) -> None:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current == 0:
            self.conn.executescript(_MIGRATIONS[1])
            current = 1
        for version in range(current + 1, _SCHEMA_VERSION + 1):
            self.conn.executescript(_MIGRATIONS[version])
            # PRAGMA n'accepte pas de paramètre lié ; `version` est un entier
            # interne, jamais une entrée utilisateur.
            self.conn.execute(f"PRAGMA user_version = {int(version)}")
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
        with self._lock:
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
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO responses (key, payload, fetched_at) VALUES (?, ?, ?)",
                (key, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            self.conn.commit()

    def purge_expired(self, ttl: int | None = None) -> int:
        ttl = config.CACHE_TTL if ttl is None else ttl
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM responses WHERE fetched_at < ?", (time.time() - ttl,)
            )
            self.conn.commit()
            return cur.rowcount

    # -- Mémoire des annonces vues -------------------------------------------

    def is_seen(self, ad_id: str) -> bool:
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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

    # -- Historique de prix (D1) ----------------------------------------------

    def previous_prices(self, ad_ids: Iterable[str]) -> dict[str, float]:
        """Prix le plus haut observé antérieurement, par annonce.

        Le plus haut et non le dernier : `price_drop` doit détecter une baisse
        par rapport à ce que l'annonce a valu, pas rejouer une oscillation.
        """
        ids = [str(i) for i in ad_ids if i]
        if not ids:
            return {}
        out: dict[str, float] = {}
        with self._lock:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = self.conn.execute(
                    f"SELECT ad_id, MAX(price) AS top FROM price_history "
                    f"WHERE ad_id IN ({placeholders}) GROUP BY ad_id",
                    chunk,
                ).fetchall()
                for r in rows:
                    out[r["ad_id"]] = float(r["top"])
        return out

    def record_prices(self, listings: Iterable) -> int:
        """Historise le prix courant de chaque annonce (une ligne par prix
        distinct — `ON CONFLICT DO NOTHING` borne la table sans effacer le
        premier moment observé pour ce prix)."""
        now = time.time()
        rows = [
            (item.id, int(item.price), now)
            for item in listings
            if item.id and item.price
        ]
        if not rows:
            return 0
        with self._lock:
            self.conn.executemany(
                "INSERT INTO price_history (ad_id, price, seen_at) VALUES (?, ?, ?) "
                "ON CONFLICT(ad_id, price) DO NOTHING",
                rows,
            )
            self.conn.commit()
        return len(rows)

    # -- Référence de cohorte persistée (D2) -----------------------------------

    @staticmethod
    def _versioned(key: str) -> str:
        return f"{SIGNATURE_VERSION}:{key}"

    def cohort_medians(self, keys: Iterable[str], ttl: int | None = None) -> dict[str, float]:
        """Médianes persistées par `cohort_key` (clé brute), filtrées par
        péremption. Les lignes trop vieilles sont ignorées, pas supprimées."""
        ttl = config.COHORT_STAT_TTL if ttl is None else ttl
        raw_keys = [str(k) for k in keys if k]
        if not raw_keys:
            return {}
        by_versioned = {self._versioned(k): k for k in raw_keys}
        versioned = list(by_versioned)
        out: dict[str, float] = {}
        now = time.time()
        with self._lock:
            for start in range(0, len(versioned), 500):
                chunk = versioned[start : start + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = self.conn.execute(
                    f"SELECT cohort_key, median, updated_at FROM cohort_stats "
                    f"WHERE cohort_key IN ({placeholders})",
                    chunk,
                ).fetchall()
                for r in rows:
                    if ttl > 0 and (now - r["updated_at"]) > ttl:
                        continue
                    raw_key = by_versioned.get(r["cohort_key"])
                    if raw_key:
                        out[raw_key] = float(r["median"])
        return out

    def update_cohort_stats(
        self, listings: Iterable, domain: str = "", alpha: float | None = None
    ) -> int:
        """Mélange (EWMA) la médiane pleine cohorte de chaque annonce dans la
        persistance, une seule fois par cohorte présente dans `listings`.

        Ne lit que `listing.cohort_median` — jamais `reference_price` (LOO) :
        chaque référence LOO exclut délibérément un membre différent, l'écrire
        figerait un biais par-annonce dans une estimation de population.
        `cohort_median` n'est renseigné par `pricing.assign_reference_prices`
        que pour les cohortes ayant atteint `min_cohort` : une cohorte de deux
        pépites qui se valident l'une l'autre n'atteint jamais cette table.
        """
        alpha = config.COHORT_STAT_ALPHA if alpha is None else alpha
        now = time.time()
        observed: dict[str, float] = {}
        for item in listings:
            if item.cohort_key and item.cohort_median is not None:
                observed[item.cohort_key] = item.cohort_median
        if not observed:
            return 0

        versioned_keys = [self._versioned(k) for k in observed]
        with self._lock:
            placeholders = ",".join("?" * len(versioned_keys))
            rows = self.conn.execute(
                f"SELECT cohort_key, median FROM cohort_stats WHERE cohort_key IN ({placeholders})",
                versioned_keys,
            ).fetchall()
            existing = {r["cohort_key"]: r["median"] for r in rows}

            upserts = []
            for raw_key, new_value in observed.items():
                vkey = self._versioned(raw_key)
                old = existing.get(vkey)
                blended = new_value if old is None else (1 - alpha) * old + alpha * new_value
                upserts.append((vkey, domain, blended, now))

            self.conn.executemany(
                """
                INSERT INTO cohort_stats (cohort_key, domain, median, n, updated_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(cohort_key) DO UPDATE SET
                    median     = excluded.median,
                    n          = n + 1,
                    updated_at = excluded.updated_at
                """,
                upserts,
            )
            self.conn.commit()
        return len(upserts)

    def forget(self, ad_ids: Iterable[str] | None = None, domain: str | None = None) -> int:
        with self._lock:
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
        with self._lock:
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
