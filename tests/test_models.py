"""Normalisation des payloads bruts en `Listing`.

L'API leboncoin sert le même champ sous des formes différentes selon les
endpoints (prix en liste vs scalaire, dates avec ou sans fuseau...) ; ces tests
verrouillent le comportement défensif attendu de `Listing.from_raw`.
"""

import datetime as dt
import unittest

from lbc.models import Listing, _parse_date, _parse_price


class TestParsePrice(unittest.TestCase):
    def test_scalar_int(self):
        self.assertEqual(_parse_price(120), 120)

    def test_list_form(self):
        self.assertEqual(_parse_price([120]), 120)

    def test_empty_list(self):
        self.assertIsNone(_parse_price([]))

    def test_string_with_currency(self):
        self.assertEqual(_parse_price("120 €"), 120)

    def test_zero_is_none(self):
        self.assertIsNone(_parse_price(0))

    def test_negative_is_none(self):
        self.assertIsNone(_parse_price(-5))

    def test_none(self):
        self.assertIsNone(_parse_price(None))

    def test_bool_is_not_price(self):
        # bool est une sous-classe d'int en Python ; un piège classique.
        self.assertIsNone(_parse_price(True))


class TestParseDate(unittest.TestCase):
    def test_space_separated(self):
        result = _parse_date("2026-08-04 09:00:00")
        self.assertEqual(result.year, 2026)
        self.assertIsNotNone(result.tzinfo)

    def test_iso_with_z(self):
        result = _parse_date("2026-08-04T09:00:00Z")
        self.assertEqual(result.hour, 9)

    def test_garbage_is_none(self):
        self.assertIsNone(_parse_date("pas une date"))

    def test_none_input(self):
        self.assertIsNone(_parse_date(None))


class TestListingFromRaw(unittest.TestCase):
    def test_minimal_payload(self):
        listing = Listing.from_raw({"list_id": "42", "subject": "Titre"})
        self.assertEqual(listing.id, "42")
        self.assertEqual(listing.title, "Titre")
        self.assertIsNone(listing.price)
        self.assertEqual(listing.url, "https://www.leboncoin.fr/ad/42")

    def test_missing_id_falls_back_to_ad_id(self):
        listing = Listing.from_raw({"ad_id": 99})
        self.assertEqual(listing.id, "99")

    def test_completely_empty_payload_does_not_raise(self):
        listing = Listing.from_raw({})
        self.assertEqual(listing.id, "")
        self.assertEqual(listing.title, "")

    def test_attributes_extracted(self):
        raw = {
            "list_id": "1",
            "attributes": [
                {"key": "brand", "value_label": "Omega"},
                {"key_label": "condition", "value": "bon"},
                {"garbage": "ignored"},
            ],
        }
        listing = Listing.from_raw(raw)
        self.assertEqual(listing.attributes["brand"], "Omega")
        self.assertEqual(listing.attributes["condition"], "bon")

    def test_images_prefer_large(self):
        raw = {
            "list_id": "1",
            "images": {
                "urls_large": ["big.jpg"],
                "urls_thumb": ["small.jpg"],
                "nb_images": 1,
            },
        }
        listing = Listing.from_raw(raw)
        self.assertEqual(listing.images, ["big.jpg"])

    def test_url_already_absolute_is_untouched(self):
        raw = {"list_id": "1", "url": "https://www.leboncoin.fr/ad/1"}
        listing = Listing.from_raw(raw)
        self.assertEqual(listing.url, "https://www.leboncoin.fr/ad/1")

    def test_text_property_joins_fields(self):
        listing = Listing(id="1", title="A", body="B", attributes={"k": "C"})
        self.assertIn("A", listing.text)
        self.assertIn("B", listing.text)
        self.assertIn("C", listing.text)

    def test_age_hours_none_without_date(self):
        listing = Listing(id="1")
        self.assertIsNone(listing.age_hours)

    def test_age_hours_computed(self):
        published = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)
        listing = Listing(id="1", published=published)
        self.assertAlmostEqual(listing.age_hours, 5.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
