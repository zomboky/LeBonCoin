"""`--sweep` (B3) et la garantie de non-régression du Lot 1 : sans drapeau,
`_collect` ne doit interroger que la première catégorie du pack, comme avant."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from golddigger02 import cache
from golddigger02.cli import _collect, _params_from_args, build_parser


def _parse(argv):
    return build_parser().parse_args(argv)


class TestSweep(unittest.TestCase):
    def test_sweep_produit_une_requete_vide_par_categorie(self):
        args = _parse(["deals", "--domain", "aviation", "--sweep"])
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.Store(path=Path(tmp) / "t.sqlite3")
            with patch("golddigger02.cli.fetch_matrix", return_value=[]) as fm:
                _collect(args, store)
            store.close()
        queries, params, categories = fm.call_args[0][:3]
        self.assertEqual(queries, [])
        self.assertEqual(params.sort, "date")
        self.assertTrue(categories)

    def test_sweep_sans_categorie_echoue_clairement(self):
        args = _parse(["deals", "--sweep"])  # ni --domain ni --category
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.Store(path=Path(tmp) / "t.sqlite3")
            with self.assertRaises(SystemExit):
                _collect(args, store)
            store.close()

    def test_sweep_pagine_plus_profond_sauf_si_pages_fourni(self):
        auto = SimpleNamespace(
            query="", category=None, min_price=None, max_price=None, where=None,
            radius=None, sort="date", seller=None, pages=None, sweep=True,
        )
        self.assertEqual(_params_from_args(auto).pages, 10)

        explicite = SimpleNamespace(
            query="", category=None, min_price=None, max_price=None, where=None,
            radius=None, sort="date", seller=None, pages=3, sweep=True,
        )
        self.assertEqual(_params_from_args(explicite).pages, 3)

    def test_sweep_all_categories_interroge_toutes_les_categories(self):
        args = _parse(["deals", "--domain", "aviation", "--sweep", "--all-categories"])
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.Store(path=Path(tmp) / "t.sqlite3")
            with patch("golddigger02.cli.fetch_matrix", return_value=[]) as fm:
                _collect(args, store)
            store.close()
        _, _, categories = fm.call_args[0][:3]
        self.assertGreater(len(categories), 1)


class TestSansDrapeauCompatibiliteAscendante(unittest.TestCase):
    def test_sans_drapeau_seule_la_premiere_categorie_est_interrogee(self):
        """Garde-fou de non-régression du Lot 1 tout entier : sans
        --all-categories, le comportement doit être identique à avant."""
        from golddigger02 import domains as domains_mod

        args = _parse(["deals", "--domain", "aviation"])
        domain = domains_mod.get("aviation")
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.Store(path=Path(tmp) / "t.sqlite3")
            with patch("golddigger02.cli.fetch_matrix", return_value=[]) as fm:
                _collect(args, store)
            store.close()
        queries, params, categories = fm.call_args[0][:3]
        self.assertIsNone(categories)
        self.assertEqual(params.category, domain.category_ids[0])
        self.assertEqual(queries, domain.queries)


if __name__ == "__main__":
    unittest.main()
