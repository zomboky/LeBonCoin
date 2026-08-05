"""Chargement des packs YAML : tous les packs livrés doivent être valides."""

import unittest

from lbc import domains


class TestLoadAll(unittest.TestCase):
    def setUp(self):
        self.domains = domains.load_all()

    def test_eight_packs_loaded(self):
        # Verrouille le nombre attendu : une régression de chargement se voit
        # immédiatement, sans avoir à lister les fichiers à la main.
        self.assertGreaterEqual(len(self.domains), 8)

    def test_every_pack_resolves_at_least_one_category(self):
        for name, domain in self.domains.items():
            with self.subTest(pack=name):
                self.assertTrue(domain.category_ids, f"{name}: categories={domain.categories}")

    def test_every_pack_has_queries(self):
        for name, domain in self.domains.items():
            with self.subTest(pack=name):
                self.assertGreater(len(domain.queries), 0)

    def test_every_pack_has_value_bands(self):
        for name, domain in self.domains.items():
            with self.subTest(pack=name):
                self.assertGreater(len(domain.value_bands), 0)

    def test_aviation_present_with_expected_shape(self):
        aviation = domains.get("aviation")
        self.assertIsNotNone(aviation)
        self.assertIn("Badin", aviation.brands)
        self.assertTrue(any("badin" in m.pattern.lower() for m in aviation.models))

    def test_hyphenated_lookup_matches_underscored_name(self):
        # Les fichiers utilisent des tirets (flight-sim.yml) ; le nom interne
        # est slugifié en underscore. get() doit accepter les deux écritures.
        self.assertIsNotNone(domains.get("flight-sim"))
        self.assertIsNotNone(domains.get("flight_sim"))

    def test_unknown_domain_returns_none(self):
        self.assertIsNone(domains.get("ne-existe-pas"))


class TestValueBand(unittest.TestCase):
    def test_reference_is_low_biased(self):
        band = domains.ValueBand(match="x", low=100, high=200)
        # 0.35 vers le haut de la fourchette : proche du bas, pas la moyenne.
        self.assertAlmostEqual(band.reference, 135.0)

    def test_matches_case_insensitive(self):
        band = domains.ValueBand(match="altim[eè]tre", low=1, high=2)
        self.assertTrue(band.matches("Un bel ALTIMÈTRE d'avion"))
        self.assertFalse(band.matches("un vélo"))


class TestMerged(unittest.TestCase):
    def test_merges_two_packs(self):
        combo = domains.merged(["aviation", "space"])
        self.assertIn("aviation", combo.name)
        self.assertIn("space", combo.name)
        self.assertGreater(len(combo.queries), len(domains.get("aviation").queries))


if __name__ == "__main__":
    unittest.main()
