"""`fetch_matrix` : produit cartésien requêtes × catégories, et compatibilité
de `fetch_many` (mono-catégorie, comportement par défaut inchangé)."""

from __future__ import annotations

import unittest

from golddigger02.api import SearchParams, fetch_many, fetch_matrix
from golddigger02.transport import Blocked


class FakeTransport:
    """Transport factice : enregistre les charges utiles, répond via un callback."""

    def __init__(self, responder):
        self.calls: list[dict] = []
        self._responder = responder

    def search(self, payload: dict, web_url: str | None = None) -> dict:
        self.calls.append(payload)
        return self._responder(payload)


def _pair(payload: dict) -> tuple[str, str | None]:
    text = payload["filters"].get("keywords", {}).get("text", "")
    cat = payload["filters"].get("category", {}).get("id")
    return text, cat


def _ad(ad_id: str, title: str = "x") -> dict:
    return {"list_id": ad_id, "subject": title, "category_id": "40"}


class TestFetchMatrix(unittest.TestCase):
    def test_toutes_les_paires_requete_categorie_sont_interrogees(self):
        transport = FakeTransport(lambda payload: {"ads": [], "total": 0})
        base = SearchParams(pages=1)
        fetch_matrix(["q1", "q2"], base, ["10", "20"], transport, None, True, None)

        got = {_pair(p) for p in transport.calls}
        expected = {("q1", "10"), ("q1", "20"), ("q2", "10"), ("q2", "20")}
        self.assertEqual(got, expected)

    def test_deduplication_sur_toute_la_matrice(self):
        transport = FakeTransport(lambda payload: {"ads": [_ad("99")], "total": 1})
        base = SearchParams(pages=1)
        listings = fetch_matrix(["q1", "q2"], base, ["10", "20"], transport, None, True, None)
        self.assertEqual(len(listings), 1)

    def test_la_premiere_occurrence_gagne(self):
        calls = {"n": 0}

        def responder(payload):
            calls["n"] += 1
            title = "premier" if calls["n"] == 1 else "second"
            return {"ads": [_ad("1", title)], "total": 1}

        transport = FakeTransport(responder)
        base = SearchParams(pages=1)
        listings = fetch_matrix(["q1", "q2"], base, None, transport, None, True, None)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].title, "premier")

    def test_fetch_many_inchange_en_mono_categorie(self):
        transport = FakeTransport(lambda payload: {"ads": [], "total": 0})
        base = SearchParams(category="40", pages=1)
        fetch_many(["q1", "q2"], base, transport, None, True, None)
        self.assertEqual(len(transport.calls), 2)
        for payload in transport.calls:
            self.assertEqual(payload["filters"]["category"]["id"], "40")

    def test_un_blocage_sur_une_paire_n_arrete_pas_la_matrice(self):
        def responder(payload):
            text, cat = _pair(payload)
            if cat == "10":
                raise Blocked("bloqué")
            return {"ads": [_ad(f"{text}-{cat}")], "total": 1}

        transport = FakeTransport(responder)
        base = SearchParams(pages=1)
        listings = fetch_matrix(["q1"], base, ["10", "20"], transport, None, True, None)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].id, "q1-20")

    def test_queries_vide_produit_une_seule_requete_sans_mot_cle(self):
        transport = FakeTransport(lambda payload: {"ads": [], "total": 0})
        base = SearchParams(pages=1)
        fetch_matrix([], base, ["10"], transport, None, True, None)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("keywords", transport.calls[0]["filters"])


class TestTypoQueries(unittest.TestCase):
    def test_les_variantes_d_accent_ne_produisent_pas_de_requete(self):
        from golddigger02.cli import _typo_queries
        from golddigger02.domains import Domain

        domain = Domain(
            name="aviation",
            typos={"kolsman": "Kollsman", "altimetre": "altimètre"},
        )
        self.assertEqual(_typo_queries(domain), ["kolsman"])


if __name__ == "__main__":
    unittest.main()
