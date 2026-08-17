"""Chaque signal isolément, puis l'agrégation et ses garde-fous."""

import datetime as dt
import unittest

from golddigger02 import config
from golddigger02.domains import Domain, ModelRule, ValueBand
from golddigger02.models import Listing
from golddigger02.score import score_listing


def aviation_domain() -> Domain:
    return Domain(
        name="aviation",
        categories=["collection"],
        brands=["Badin"],
        models=[ModelRule(pattern=r"badin", name="Badin")],
        typos={"kolsman": "Kollsman"},
        origin_markers=["plaque constructeur", "numero de serie"],
        repro_markers=["reproduction", "reedition"],
        premium=["siege ejectable"],
        value_bands=[ValueBand(match="badin|altimetre", low=100, high=300)],
    )


class TestScoreListing(unittest.TestCase):
    def test_hidden_model_and_cheap_price_scores_high(self):
        domain = aviation_domain()
        listing = Listing(
            id="1",
            title="Vieil instrument trouvé au grenier",
            body="Il y a marqué Badin dessus avec un numéro de série, succession, à débarrasser.",
            price=25,
            category_id="40",
            seller_type="private",
        )
        listing.model, listing.model_source = "Badin", "body"
        listing.reference_price = 100 + 200 * 0.35  # même calcul que pricing.py
        score_listing(listing, domain)
        self.assertGreater(listing.deal_score, 50)
        self.assertTrue(any("titre" in r for r in listing.reasons))

    def test_repro_marker_crushes_score_regardless_of_price(self):
        domain = aviation_domain()
        listing = Listing(
            id="2",
            title="Réplique altimètre style ancien",
            body="Belle reproduction pour déco, neuve.",
            price=10,
            category_id="40",
        )
        listing.reference_price = 165.0
        score_listing(listing, domain)
        self.assertLess(listing.deal_score, 20)
        self.assertTrue(any("REPRO" in r for r in listing.reasons))

    def test_suspiciously_cheap_is_penalized(self):
        domain = aviation_domain()
        listing = Listing(id="3", title="Altimètre Badin", body="", price=1, category_id="40")
        listing.reference_price = 200.0

        comparable = Listing(id="4", title="Altimètre Badin", body="", price=150, category_id="40")
        comparable.reference_price = 200.0
        score_no_penalty = score_listing(comparable, domain)

        score_listing(listing, domain)
        # Le prix quasi nul doit être suspect, pas juste "très rentable".
        self.assertLess(listing.deal_score, score_no_penalty)

    def test_no_price_is_penalized_but_not_excluded(self):
        domain = aviation_domain()
        listing = Listing(id="5", title="Altimètre Badin", body="contactez moi", category_id="40")
        listing.reference_price = 200.0
        score = score_listing(listing, domain)
        self.assertGreaterEqual(score, 0)
        self.assertIn("prix non affiché", listing.reasons)

    def test_typo_brand_detected(self):
        domain = aviation_domain()
        listing = Listing(
            id="6", title="Instrument Kolsman ancien", body="bon etat", price=150, category_id="40"
        )
        score_listing(listing, domain)
        self.assertEqual(listing.signals["typo_brand"], 1.0)

    def test_freshness_decays_with_age(self):
        domain = aviation_domain()
        fresh = Listing(
            id="7", title="x", price=100, category_id="40",
            published=dt.datetime.now(dt.timezone.utc),
        )
        old = Listing(
            id="8", title="x", price=100, category_id="40",
            published=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10),
        )
        score_listing(fresh, domain)
        score_listing(old, domain)
        self.assertGreater(fresh.signals["freshness"], old.signals["freshness"])

    def test_no_domain_does_not_crash(self):
        listing = Listing(id="9", title="Quelque chose", price=50, category_id="1")
        score = score_listing(listing, None)
        self.assertGreaterEqual(score, 0)

    def test_tout_signal_produit_a_un_poids(self):
        """Un signal sans poids dans config.WEIGHTS est silencieusement ignoré
        (c'était le bug A1 sur `authenticity`) — verrou anti-régression."""
        domain = aviation_domain()
        listing = Listing(
            id="10",
            title="Instrument Kolsman",
            body="plaque constructeur numero de serie succession je ne sais pas",
            price=1,
            category_id="40",
            seller_type="private",
        )
        listing.model, listing.model_source = "Badin", "body"
        listing.reference_price = 200.0
        score_listing(listing, domain)
        self.assertLessEqual(set(listing.signals), set(config.WEIGHTS))

    def test_indices_faibles_seuls_ne_saturent_pas(self):
        domain = aviation_domain()
        listing = Listing(
            id="11", title="x",
            body="vieux, en l'état, non testé, sans garantie",
            price=100, category_id="40",
        )
        score_listing(listing, domain)
        self.assertLessEqual(listing.signals["urgency"], 0.45)

    def test_deux_indices_forts_saturent(self):
        domain = aviation_domain()
        listing = Listing(
            id="12", title="x",
            body="succession, je ne sais pas ce que c'est",
            price=100, category_id="40",
        )
        score_listing(listing, domain)
        self.assertEqual(listing.signals["urgency"], 1.0)

    def test_un_indice_fort_vaut_plus_que_quatre_faibles(self):
        domain = aviation_domain()
        fort = Listing(id="13", title="x", body="succession", price=100, category_id="40")
        faible = Listing(
            id="14", title="x",
            body="vieux, en l'état, non testé, sans garantie",
            price=100, category_id="40",
        )
        score_listing(fort, domain)
        score_listing(faible, domain)
        self.assertGreater(fort.signals["urgency"], faible.signals["urgency"])

    def test_baisse_de_prix_detectee(self):
        domain = aviation_domain()
        listing = Listing(
            id="15", title="x", price=100, category_id="40", previous_price=200.0,
        )
        score_listing(listing, domain)
        self.assertEqual(listing.signals["price_drop"], 1.0)

    def test_absence_d_historique_est_neutre(self):
        domain = aviation_domain()
        listing = Listing(id="16", title="x", price=100, category_id="40")
        score_listing(listing, domain)
        self.assertEqual(listing.signals["price_drop"], 0.0)

    def test_une_hausse_n_est_pas_une_baisse(self):
        domain = aviation_domain()
        listing = Listing(
            id="17", title="x", price=250, category_id="40", previous_price=200.0,
        )
        score_listing(listing, domain)
        self.assertEqual(listing.signals["price_drop"], 0.0)

    def test_score_ne_depend_pas_du_cache(self):
        """Le scoring doit rester pur et testable hors ligne : aucun objet de
        cache.py ne doit fuir dans score.py. Verrou pour A2/A3, D1 et D2."""
        import golddigger02.cache as cache_mod
        import golddigger02.score as score_mod

        leaked = [
            name for name, value in vars(score_mod).items()
            if getattr(value, "__module__", "") == cache_mod.__name__
        ]
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
