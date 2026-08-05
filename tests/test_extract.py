"""Extraction lexicale : marque, modèle caché, fautes d'orthographe, cohortes."""

import unittest

from golddigger02.domains import Domain, ModelRule
from golddigger02.extract import (
    cohort_signature,
    content_tokens,
    detect_typo,
    extract_model,
    find_markers,
    is_model_hidden,
    listing_quality,
    normalize,
)
from golddigger02.models import Listing


def make_aviation_domain() -> Domain:
    return Domain(
        name="aviation",
        categories=["collection"],
        brands=["Badin", "Kollsman"],
        models=[ModelRule(pattern=r"badin\s*[a-z0-9-]{0,6}", name="Badin")],
        typos={"kolsman": "Kollsman"},
        origin_markers=["plaque constructeur", "numero de serie"],
        repro_markers=["reproduction", "reedition"],
    )


class TestNormalize(unittest.TestCase):
    def test_strips_accents_and_case(self):
        self.assertEqual(normalize("Altimètre RÉCENT"), "altimetre recent")


class TestContentTokens(unittest.TestCase):
    def test_removes_stopwords_and_noise(self):
        tokens = content_tokens("Vends très bel altimètre Badin en bon état, 25")
        self.assertIn("altimetre", tokens)
        self.assertIn("badin", tokens)
        self.assertNotIn("vends", tokens)
        self.assertNotIn("25", tokens)


class TestFindMarkers(unittest.TestCase):
    def test_accent_insensitive_match(self):
        found = find_markers("Numéro de série visible", ["numero de serie"])
        self.assertEqual(found, ["numero de serie"])

    def test_no_match(self):
        self.assertEqual(find_markers("rien à signaler", ["plaque constructeur"]), [])


class TestDetectTypo(unittest.TestCase):
    def test_detects_misspelling(self):
        result = detect_typo("Instrument Kolsman en bon état", {"kolsman": "Kollsman"})
        self.assertEqual(result, ("kolsman", "Kollsman"))

    def test_no_false_positive_when_correct_spelling_present(self):
        result = detect_typo("Kollsman authentique", {"kolsman": "Kollsman"})
        self.assertIsNone(result)

    def test_no_typo_present(self):
        self.assertIsNone(detect_typo("rien de particulier", {"kolsman": "Kollsman"}))


class TestExtractModel(unittest.TestCase):
    def test_hidden_model_in_body_not_title(self):
        domain = make_aviation_domain()
        listing = Listing(
            id="1",
            title="Vieil instrument trouvé au grenier",
            body="Il y a marqué Badin dessus, avec un numéro de série.",
        )
        brand, model, source = extract_model(listing, domain)
        self.assertEqual(model, "Badin")
        self.assertEqual(source, "body")

    def test_model_in_title_is_not_hidden(self):
        domain = make_aviation_domain()
        listing = Listing(id="1", title="Altimètre Badin d'avion", body="bon état")
        brand, model, source = extract_model(listing, domain)
        self.assertEqual(source, "title")

    def test_no_domain_returns_empty(self):
        listing = Listing(id="1", title="Quelque chose")
        self.assertEqual(extract_model(listing, None), ("", "", ""))

    def test_word_boundary_prevents_false_match(self):
        # "ge" ne doit pas matcher dans "garage".
        domain = Domain(name="x", brands=["GE"])
        listing = Listing(id="1", title="Vieux garage à vendre", body="rien")
        brand, model, source = extract_model(listing, domain)
        self.assertEqual(brand, "")


class TestIsModelHidden(unittest.TestCase):
    def test_hidden_when_source_is_body(self):
        listing = Listing(id="1", model="Badin", model_source="body")
        self.assertTrue(is_model_hidden(listing))

    def test_not_hidden_when_in_title(self):
        listing = Listing(id="1", model="Badin", model_source="title")
        self.assertFalse(is_model_hidden(listing))

    def test_not_hidden_when_no_model(self):
        listing = Listing(id="1", model="", model_source="")
        self.assertFalse(is_model_hidden(listing))


class TestListingQuality(unittest.TestCase):
    def test_bare_listing_scores_low(self):
        listing = Listing(id="1", title="x", body="", nb_images=0)
        self.assertLess(listing_quality(listing), 0.2)

    def test_full_listing_scores_high(self):
        listing = Listing(
            id="1",
            title="Un titre suffisamment long et descriptif",
            body="Une description détaillée de plus de cent cinquante caractères qui décrit l'objet avec beaucoup de soin, de détails et de précision pour l'acheteur potentiel qui hésite encore.",
            nb_images=5,
            attributes={"brand": "x"},
        )
        self.assertGreater(listing_quality(listing), 0.9)


class TestCohortSignature(unittest.TestCase):
    def test_same_model_same_signature_different_titles(self):
        a = Listing(id="1", category_id="40", title="Vieux truc", model="Badin")
        b = Listing(id="2", category_id="40", title="Autre chose", model="Badin")
        self.assertEqual(cohort_signature(a), cohort_signature(b))

    def test_falls_back_to_title_tokens_without_model(self):
        listing = Listing(id="1", category_id="40", title="Altimètre avion ancien Badin")
        sig = cohort_signature(listing)
        self.assertIn("40:", sig)

    def test_empty_title_does_not_crash(self):
        listing = Listing(id="1", category_id="40", title="")
        self.assertTrue(cohort_signature(listing).startswith("40:"))


if __name__ == "__main__":
    unittest.main()
