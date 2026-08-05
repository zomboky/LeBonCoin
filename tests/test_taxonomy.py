"""Résolution de catégories et de localisations, sans appel réseau."""

import unittest

from golddigger02.taxonomy import resolve_category, resolve_location, slug


class TestSlug(unittest.TestCase):
    def test_accents_removed(self):
        self.assertEqual(slug("Montres & Bijoux"), "montres_bijoux")

    def test_already_clean(self):
        self.assertEqual(slug("velos"), "velos")


class TestResolveCategory(unittest.TestCase):
    def test_numeric_passthrough(self):
        self.assertEqual(resolve_category("55"), "55")

    def test_canonical_name(self):
        self.assertEqual(resolve_category("montres_bijoux"), "55")

    def test_alias(self):
        self.assertEqual(resolve_category("montre"), "55")
        self.assertEqual(resolve_category("velo"), "44")

    def test_case_and_accent_insensitive(self):
        self.assertEqual(resolve_category("Vêtements"), "27")

    def test_none_input(self):
        self.assertIsNone(resolve_category(None))

    def test_empty_string(self):
        self.assertIsNone(resolve_category(""))


class TestResolveLocation(unittest.TestCase):
    def test_department_number(self):
        result = resolve_location("75")
        self.assertEqual(result, {"locationType": "department", "department_id": "75"})

    def test_single_digit_padded(self):
        result = resolve_location("6")
        self.assertEqual(result["department_id"], "06")

    def test_zipcode_resolves_department(self):
        result = resolve_location("69001")
        self.assertEqual(result["department_id"], "69")

    def test_corsica_zipcode_2a(self):
        result = resolve_location("20000")
        self.assertEqual(result["department_id"], "2A")

    def test_corsica_zipcode_2b(self):
        result = resolve_location("20200")
        self.assertEqual(result["department_id"], "2B")

    def test_city_name(self):
        result = resolve_location("Lyon")
        self.assertEqual(result["department_id"], "69")

    def test_city_accented(self):
        result = resolve_location("Clermont-Ferrand")
        self.assertEqual(result["department_id"], "63")

    def test_region_alias(self):
        result = resolve_location("PACA")
        self.assertEqual(result["locationType"], "region")

    def test_unknown_returns_none(self):
        self.assertIsNone(resolve_location("Nulle Part Ville Imaginaire"))

    def test_none_input(self):
        self.assertIsNone(resolve_location(None))


if __name__ == "__main__":
    unittest.main()
