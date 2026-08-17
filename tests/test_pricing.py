"""Cohortes, médiane/MAD robustes, prix de référence, garde-fous prix."""

import unittest

from golddigger02.domains import Domain, ValueBand
from golddigger02.models import Listing
from golddigger02.pricing import (
    assign_cohort_keys,
    assign_reference_prices,
    gap_confidence,
    is_suspiciously_cheap,
    mad,
    median,
    price_gap,
    price_signal,
    robust_z,
)


class TestRobustStats(unittest.TestCase):
    def test_median_odd(self):
        self.assertEqual(median([1, 3, 2]), 2)

    def test_median_empty(self):
        self.assertEqual(median([]), 0.0)

    def test_mad_ignores_outlier_more_than_stdev_would(self):
        values = [100, 100, 100, 100, 10000]
        self.assertEqual(mad(values), 0.0)  # médiane = 100, tous à 0 sauf l'outlier

    def test_robust_z_zero_dispersion(self):
        self.assertEqual(robust_z(50, 100, 0), 0.0)

    def test_robust_z_below_center_is_negative(self):
        self.assertLess(robust_z(50, 100, 10), 0)


class TestAssignReferencePrices(unittest.TestCase):
    def _listing(self, id_, price, model="Badin"):
        return Listing(id=id_, category_id="40", title="Badin", price=price, model=model)

    def test_cohort_median_used_when_enough_members(self):
        listings = [self._listing(str(i), 200 + i) for i in range(6)]
        assign_reference_prices(listings, domain=None, min_cohort=5)
        for listing in listings:
            self.assertIsNotNone(listing.reference_price)
            self.assertEqual(listing.cohort_size, 6)

    def test_falls_back_to_value_band_below_min_cohort(self):
        domain = Domain(
            name="aviation",
            value_bands=[ValueBand(match="badin", low=100, high=300)],
        )
        listings = [self._listing("1", 50), self._listing("2", 60)]
        assign_reference_prices(listings, domain=domain, min_cohort=5)
        for listing in listings:
            self.assertEqual(listing.cohort_size, 2)
            self.assertAlmostEqual(listing.reference_price, 100 + 200 * 0.35)

    def test_no_domain_and_small_cohort_gives_no_reference(self):
        listings = [self._listing("1", 50), self._listing("2", 60)]
        assign_reference_prices(listings, domain=None, min_cohort=5)
        self.assertIsNone(listings[0].reference_price)

    def test_la_reference_exclut_l_annonce_elle_meme(self):
        # [10, 10, 10, 100, 100] : la médiane pleine cohorte vaut 10, mais un
        # "10" doit être comparé aux 4 *autres* (10,10,100,100) -> médiane 55.
        # La plupart des cohortes donnent la même valeur avec/sans self ; il
        # faut des prix qui divergent pour que le bug A2 se révèle.
        prices = [10, 10, 10, 100, 100]
        listings = [self._listing(str(i), p) for i, p in enumerate(prices)]
        assign_reference_prices(listings, domain=None, min_cohort=5)
        self.assertEqual(listings[0].reference_price, 55.0)
        self.assertEqual(listings[0].cohort_size, 5)  # self compris

    def test_cohorte_de_deux_retombe_sur_le_bareme(self):
        # Avec min_cohort=2, deux annonces valident historiquement la médiane
        # l'une de l'autre (docstring pricing.py). Le plancher max(2, min-1)
        # doit fermer ce cas et faire retomber sur le barème.
        domain = Domain(
            name="aviation",
            value_bands=[ValueBand(match="badin", low=100, high=300)],
        )
        listings = [self._listing("1", 50), self._listing("2", 4000)]
        assign_reference_prices(listings, domain=domain, min_cohort=2)
        for listing in listings:
            self.assertEqual(listing.reference_source, "band")
            self.assertAlmostEqual(listing.reference_price, 100 + 200 * 0.35)

    def test_dispersion_renseignee_sur_le_chemin_cohorte(self):
        prices = [100, 100, 100, 200, 200]
        listings = [self._listing(str(i), p) for i, p in enumerate(prices)]
        assign_reference_prices(listings, domain=None, min_cohort=5)
        self.assertIsNotNone(listings[0].reference_dispersion)

    def test_le_bareme_ne_renseigne_pas_de_dispersion(self):
        domain = Domain(
            name="aviation",
            value_bands=[ValueBand(match="badin", low=100, high=300)],
        )
        listings = [self._listing("1", 50), self._listing("2", 60)]
        assign_reference_prices(listings, domain=domain, min_cohort=5)
        self.assertIsNone(listings[0].reference_dispersion)

    def test_historique_utilise_sous_min_cohort(self):
        # `model="Badin"`, `category_id="40"`, `domain=None` -> cohort_key
        # déterministe (cf. extract.cohort_signature) : "40:badin".
        listings = [self._listing("1", 50), self._listing("2", 60)]
        assign_reference_prices(
            listings, domain=None, min_cohort=5, cohort_stats={"40:badin": 180.0}
        )
        self.assertEqual(listings[0].reference_price, 180.0)
        self.assertEqual(listings[0].reference_source, "history")

    def test_la_cohorte_vivante_prime_sur_l_historique(self):
        prices = [200 + i for i in range(6)]
        listings = [self._listing(str(i), p) for i, p in enumerate(prices)]
        assign_reference_prices(
            listings, domain=None, min_cohort=5, cohort_stats={"40:badin": 9999.0}
        )
        self.assertEqual(listings[0].reference_source, "cohort")
        self.assertNotEqual(listings[0].reference_price, 9999.0)

    def test_l_historique_prime_sur_le_bareme(self):
        domain = Domain(
            name="aviation",
            value_bands=[ValueBand(match="badin", low=100, high=300)],
        )
        listings = [self._listing("1", 50), self._listing("2", 60)]
        assign_reference_prices(
            listings, domain=domain, min_cohort=5, cohort_stats={"40:badin": 250.0}
        )
        self.assertEqual(listings[0].reference_source, "history")
        self.assertEqual(listings[0].reference_price, 250.0)

    def test_la_mediane_persistee_est_bornee_par_le_bareme(self):
        # Persisté à 20€, très en dessous du barème 60-400 : le corridor doit
        # empêcher une valeur apprise dérivante de sortir de ce qu'un humain a
        # jugé plausible. C'est le garde-fou le plus important du lot.
        domain = Domain(
            name="aviation",
            value_bands=[ValueBand(match="badin", low=60, high=400)],
        )
        listings = [self._listing("1", 30), self._listing("2", 40)]
        assign_reference_prices(
            listings, domain=domain, min_cohort=5, cohort_stats={"40:badin": 20.0}
        )
        self.assertEqual(listings[0].reference_source, "history")
        self.assertEqual(listings[0].reference_price, 60.0)


class TestPriceGap(unittest.TestCase):
    def test_no_gap_at_reference_price(self):
        listing = Listing(id="1", price=100, reference_price=100)
        self.assertEqual(price_gap(listing), 0.0)

    def test_above_reference_is_zero(self):
        listing = Listing(id="1", price=150, reference_price=100)
        self.assertEqual(price_gap(listing), 0.0)

    def test_half_price_is_meaningful_gap(self):
        listing = Listing(id="1", price=50, reference_price=100)
        self.assertAlmostEqual(price_gap(listing), 0.5 / 0.9)

    def test_saturates_at_one(self):
        listing = Listing(id="1", price=1, reference_price=100)
        self.assertEqual(price_gap(listing), 1.0)

    def test_no_price_no_gap(self):
        listing = Listing(id="1", price=None, reference_price=100)
        self.assertEqual(price_gap(listing), 0.0)

    def test_no_reference_no_gap(self):
        listing = Listing(id="1", price=50, reference_price=None)
        self.assertEqual(price_gap(listing), 0.0)


class TestAssignCohortKeys(unittest.TestCase):
    def test_correspond_aux_cles_reellement_utilisees(self):
        listings = [
            Listing(id="1", category_id="40", title="Badin", model="Badin"),
            Listing(id="2", category_id="40", title="Autre", model="Kollsman"),
        ]
        keys = assign_cohort_keys(listings, domain=None)
        self.assertEqual(set(keys), {l.cohort_key for l in listings})


class TestPriceSignal(unittest.TestCase):
    def test_la_dispersion_attenue_l_ecart(self):
        tight = Listing(id="1", price=50, reference_price=100, reference_dispersion=2.0)
        scattered = Listing(id="2", price=50, reference_price=100, reference_dispersion=60.0)
        self.assertGreater(price_signal(tight), price_signal(scattered))

    def test_le_bareme_n_est_pas_attenue(self):
        # reference_dispersion=None (jamais renseignée sur le chemin barème).
        listing = Listing(id="1", price=50, reference_price=100, reference_source="band")
        self.assertEqual(price_signal(listing), price_gap(listing))

    def test_dispersion_nulle_n_attenue_pas(self):
        # mad([100,100,100,100,10000]) == 0.0 (cf. TestRobustStats) : une
        # dispersion nulle ne doit jamais être interprétée comme une preuve
        # d'anomalie, sinon un vrai rabais dans une cohorte parfaitement
        # uniforme serait amorti à tort.
        listing = Listing(id="1", price=50, reference_price=100, reference_dispersion=0.0)
        self.assertEqual(gap_confidence(listing), 1.0)
        self.assertEqual(price_signal(listing), price_gap(listing))


class TestSuspiciouslyCheap(unittest.TestCase):
    def test_below_floor_is_suspicious(self):
        listing = Listing(id="1", price=2, reference_price=200)
        self.assertTrue(is_suspiciously_cheap(listing))

    def test_reasonable_discount_is_not_suspicious(self):
        listing = Listing(id="1", price=80, reference_price=200)
        self.assertFalse(is_suspiciously_cheap(listing))


if __name__ == "__main__":
    unittest.main()
