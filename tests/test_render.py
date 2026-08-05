"""Formats de sortie : une ligne par annonce, budget de tokens tenu."""

import unittest

from golddigger02.models import Listing
from golddigger02.render import as_ids, as_jsonl, as_tsv, as_vision, render


def make_listings(n: int) -> list:
    out = []
    for i in range(n):
        listing = Listing(
            id=str(i),
            title=f"Annonce numéro {i} avec un titre plausible de taille moyenne",
            price=100 + i,
            city="Paris",
            deal_score=90 - i,
        )
        listing.reasons = ["-40% vs cohorte (200€)", "modèle caché"]
        out.append(listing)
    return out


class TestTsv(unittest.TestCase):
    def test_one_line_per_listing_plus_header(self):
        listings = make_listings(5)
        output = as_tsv(listings)
        lines = output.splitlines()
        # 2 lignes d'en-tête (colonnes + rappel du format d'URL) + 5 annonces.
        self.assertEqual(len(lines), 7)

    def test_long_title_is_truncated(self):
        listing = Listing(id="1", title="x" * 200, price=10)
        output = as_tsv([listing])
        # Aucune ligne ne doit dépasser une longueur raisonnable.
        for line in output.splitlines():
            self.assertLess(len(line), 250)

    def test_no_full_url_in_output(self):
        listings = make_listings(3)
        output = as_tsv(listings)
        self.assertNotIn("https://www.leboncoin.fr/ad/", output)


class TestBudget(unittest.TestCase):
    def test_500_listings_stay_under_rough_token_budget(self):
        listings = make_listings(500)
        top20 = listings[:20]
        output = as_tsv(top20)
        # ~4 caractères par token en moyenne pour du français/anglais mêlé de
        # chiffres ; on vise très large pour ne pas coupler le test à un
        # tokenizer précis, seulement à l'ordre de grandeur annoncé au plan.
        approx_tokens = len(output) / 3.0
        self.assertLess(approx_tokens, 800)

    def test_full_jsonl_of_500_is_much_larger_than_tsv_top20(self):
        listings = make_listings(500)
        full_json = as_jsonl(listings)
        compact_tsv = as_tsv(listings[:20])
        self.assertGreater(len(full_json), len(compact_tsv) * 20)


class TestIds(unittest.TestCase):
    def test_one_id_per_line(self):
        listings = make_listings(3)
        self.assertEqual(as_ids(listings).splitlines(), ["0", "1", "2"])


class TestVision(unittest.TestCase):
    def test_images_capped(self):
        listing = Listing(id="1", title="x", price=10, images=[f"img{i}.jpg" for i in range(10)])
        output = as_vision([listing], max_images=3)
        self.assertEqual(output.count("img:"), 3)

    def test_full_url_present_in_vision_mode(self):
        # Contrairement au TSV, le mode vision peut se permettre l'URL complète
        # puisqu'il ne sort que pour une poignée de finalistes.
        listing = Listing(id="42", title="x", price=10)
        output = as_vision([listing])
        self.assertIn("leboncoin.fr/ad/42", output)


class TestRenderDispatch(unittest.TestCase):
    def test_render_respects_top(self):
        listings = make_listings(10)
        output = render(listings, "ids", top=3)
        self.assertEqual(len(output.splitlines()), 3)

    def test_unknown_format_defaults_to_tsv(self):
        listings = make_listings(2)
        output = render(listings, "unknown-format")
        self.assertTrue(output.startswith("#"))


if __name__ == "__main__":
    unittest.main()
