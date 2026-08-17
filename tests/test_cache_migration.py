"""Migration du schéma SQLite : une base utilisateur vivante ne doit jamais
perdre de données quand une nouvelle version du schéma sort."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from golddigger02 import cache
from golddigger02.models import Listing


_OLD_SCHEMA = """
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
"""


class TestMigration(unittest.TestCase):
    def test_ancienne_base_conserve_ses_annonces_vues(self):
        """Le test le plus important : ouvrir une base pré-versionnage
        (user_version=0, ancien schéma) ne doit ni lever, ni effacer les
        lignes déjà présentes."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite3"
            conn = sqlite3.connect(str(path))
            conn.executescript(_OLD_SCHEMA)
            conn.execute(
                "INSERT INTO seen (ad_id, domain, title, price, deal_score, "
                "first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("42", "aviation", "vieux cadran", 50, 12.0, time.time(), time.time()),
            )
            conn.commit()
            conn.close()

            store = cache.Store(path=path)
            row = store.conn.execute(
                "SELECT * FROM seen WHERE ad_id = ?", ("42",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["title"], "vieux cadran")
            store.close()

    def test_base_neuve_est_a_la_version_courante(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.Store(path=Path(tmp) / "new.sqlite3")
            version = store.conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, cache._SCHEMA_VERSION)
            # Les nouvelles tables existent dès la première ouverture.
            store.conn.execute("SELECT * FROM price_history")
            store.conn.execute("SELECT * FROM cohort_stats")
            store.close()

    def test_migration_idempotente(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reopen.sqlite3"
            cache.Store(path=path).close()
            store = cache.Store(path=path)  # rouvrir ne doit ni lever ni régresser
            version = store.conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, cache._SCHEMA_VERSION)
            store.close()


class _FakeListing:
    def __init__(self, id_, price):
        self.id = id_
        self.price = price


class TestPriceHistory(unittest.TestCase):
    def _store(self, tmp):
        return cache.Store(path=Path(tmp) / "hist.sqlite3")

    def test_chaque_prix_distinct_est_historise(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_prices([_FakeListing("1", 200), _FakeListing("1", 150)])
            n = store.conn.execute(
                "SELECT COUNT(*) FROM price_history WHERE ad_id = '1'"
            ).fetchone()[0]
            self.assertEqual(n, 2)
            store.close()

    def test_un_prix_repete_ne_grossit_pas_la_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_prices([_FakeListing("1", 200)])
            store.record_prices([_FakeListing("1", 200)])
            n = store.conn.execute(
                "SELECT COUNT(*) FROM price_history WHERE ad_id = '1'"
            ).fetchone()[0]
            self.assertEqual(n, 1)
            store.close()

    def test_previous_prices_renvoie_le_plus_haut_prix_anterieur(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_prices([_FakeListing("1", 200), _FakeListing("1", 150)])
            self.assertEqual(store.previous_prices(["1"]), {"1": 200.0})
            store.close()

    def test_mark_seen_ecrase_le_prix_courant_mais_l_historique_survit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            listing = _FakeListing("1", 200)
            listing.title, listing.deal_score = "x", 10.0
            store.record_prices([listing])
            store.mark_seen([listing])
            listing.price = 90
            store.mark_seen([listing])  # `seen.price` écrasé (comportement existant)
            row = store.conn.execute("SELECT price FROM seen WHERE ad_id='1'").fetchone()
            self.assertEqual(row["price"], 90)
            # ... mais le prix de 200 reste dans l'historique.
            self.assertEqual(store.previous_prices(["1"]), {"1": 200.0})
            store.close()


def _cohort_listing(key: str, median_value: float | None) -> Listing:
    listing = Listing(id="x", category_id="40")
    listing.cohort_key = key
    listing.cohort_median = median_value
    return listing


class TestCohortStats(unittest.TestCase):
    def _store(self, tmp):
        return cache.Store(path=Path(tmp) / "cohort.sqlite3")

    def test_seules_les_cohortes_fiables_sont_persistees(self):
        # cohort_median=None -> cohorte non fiable ce run (cf. pricing.py) :
        # ne doit jamais atteindre la table.
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            n = store.update_cohort_stats([_cohort_listing("40:badin", None)])
            self.assertEqual(n, 0)
            self.assertEqual(store.cohort_medians(["40:badin"]), {})
            store.close()

    def test_la_mediane_persistee_se_melange_a_l_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.update_cohort_stats([_cohort_listing("40:badin", 100.0)], alpha=0.3)
            store.update_cohort_stats([_cohort_listing("40:badin", 400.0)], alpha=0.3)
            # 0.7*100 + 0.3*400 = 190, pas 400 : un run anormal ne doit pas
            # écraser la valeur, seulement la déplacer de alpha.
            self.assertAlmostEqual(store.cohort_medians(["40:badin"])["40:badin"], 190.0)
            store.close()

    def test_une_statistique_perimee_est_ignoree(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.update_cohort_stats([_cohort_listing("40:badin", 100.0)])
            old = time.time() - 100 * 24 * 3600
            store.conn.execute(
                "UPDATE cohort_stats SET updated_at = ? WHERE cohort_key = ?",
                (old, store._versioned("40:badin")),
            )
            store.conn.commit()
            self.assertEqual(store.cohort_medians(["40:badin"], ttl=90 * 24 * 3600), {})
            store.close()

    def test_on_persiste_la_mediane_pleine_pas_la_reference_loo(self):
        # Garde-fou de la collision A2 x D2 : deux annonces de la même
        # cohorte, avec des `cohort_median` différents par construction (LOO)
        # ne devrait jamais arriver -- `cohort_median` est le même pour tous
        # les membres d'une cohorte (calculé une fois, self inclus).
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.update_cohort_stats(
                [_cohort_listing("40:badin", 150.0), _cohort_listing("40:badin", 150.0)]
            )
            self.assertEqual(store.cohort_medians(["40:badin"])["40:badin"], 150.0)
            store.close()


if __name__ == "__main__":
    unittest.main()
