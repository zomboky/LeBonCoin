"""Cohortes, médiane/MAD robustes, prix de référence, garde-fous prix."""

import unittest

from lbc.domains import Domain, ValueBand
from lbc.models import Listing
from lbc.pricing import (
    assign_reference_prices,
    is_suspiciously_cheap,
    mad,
    median,
    price_gap,
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


class TestSuspiciouslyCheap(unittest.TestCase):
    def test_below_floor_is_suspicious(self):
        listing = Listing(id="1", price=2, reference_price=200)
        self.assertTrue(is_suspiciously_cheap(listing))

    def test_reasonable_discount_is_not_suspicious(self):
        listing = Listing(id="1", price=80, reference_price=200)
        self.assertFalse(is_suspiciously_cheap(listing))


if __name__ == "__main__":
    unittest.main()
