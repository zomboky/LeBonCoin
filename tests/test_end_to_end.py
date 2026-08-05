"""Bout-en-bout sur une fixture où la pépite attendue est connue à l'avance.

La fixture `aviation_sample.json` contient quatre annonces : une pépite plantée
(id 1001 : modèle Badin caché dans la description, prix cassé, vendeur qui ne
sait pas ce qu'il a), deux annonces de comparaison au prix du marché (1003,
1004 — qui forment la cohorte), et un piège de reproduction à écarter (1002,
qui coûte pourtant plus cher que la pépite : seul le garde-fou repro l'exclut).
"""

import json
import unittest
from pathlib import Path

from lbc.domains import get as get_domain
from lbc.models import Listing
from lbc.score import rank

FIXTURE = Path(__file__).parent / "fixtures" / "aviation_sample.json"


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.listings = [Listing.from_raw(raw) for raw in data["ads"]]
        self.domain = get_domain("aviation")

    def test_planted_deal_ranks_first(self):
        ranked = rank(self.listings, domain=self.domain, min_cohort=2)
        self.assertEqual(ranked[0].id, "1001")

    def test_repro_is_suppressed_despite_looking_expensive(self):
        ranked = rank(self.listings, domain=self.domain, min_cohort=2)
        repro = next(l for l in ranked if l.id == "1002")
        top = ranked[0]
        self.assertLess(repro.deal_score, top.deal_score)
        # La repro doit finir dans la moitié basse du classement.
        self.assertGreater(ranked.index(repro), len(ranked) // 2)

    def test_hidden_model_flagged_in_reasons(self):
        ranked = rank(self.listings, domain=self.domain, min_cohort=2)
        top = ranked[0]
        self.assertTrue(any("absent du titre" in r for r in top.reasons))

    def test_cohort_built_from_comparable_listings(self):
        rank(self.listings, domain=self.domain, min_cohort=2)
        # 1001, 1003, 1004 partagent le modèle Badin -> cohorte de 3 (au prix).
        badin_priced = [l for l in self.listings if l.model == "Badin" and l.price]
        self.assertEqual(len(badin_priced), 3)

    def test_min_score_filters_low_quality_matches(self):
        ranked = rank(self.listings, domain=self.domain, min_cohort=2, min_score=50)
        self.assertTrue(all(l.deal_score >= 50 for l in ranked))
        self.assertIn("1001", [l.id for l in ranked])

    def test_top_limits_output(self):
        ranked = rank(self.listings, domain=self.domain, min_cohort=2, top=1)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].id, "1001")


if __name__ == "__main__":
    unittest.main()
