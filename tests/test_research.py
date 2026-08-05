"""Module `research` (ex-Mach2) : extraction, filtrage, orchestration.

Aucun de ces tests n'appelle le réseau — vérifié manuellement une fois en
conditions réelles (Wikipédia, example.com), mais la suite committée reste
déterministe, comme le reste du projet.
"""

import tempfile
import threading
import unittest
from pathlib import Path

from golddigger02 import cache, research


class TestNormalizeUrl(unittest.TestCase):
    def test_strips_fragment_and_trailing_slash(self):
        self.assertEqual(
            research.normalize_url("https://Example.com/page/#section"),
            "https://example.com/page",
        )

    def test_root_slash_kept(self):
        self.assertEqual(research.normalize_url("https://example.com/"), "https://example.com/")


class TestFilterMarkdown(unittest.TestCase):
    def test_keeps_relevant_block_drops_irrelevant(self):
        md = (
            "# Introduction\nCeci ne parle de rien d'intéressant.\n\n"
            "# Badin\nL'altimètre Badin est un instrument de bord rare et recherché.\n\n"
            "# Contact\nEnvoyez un message pour plus d'informations."
        )
        result = research.filter_markdown(md, "altimètre Badin recherché")
        self.assertIn("Badin", result)
        self.assertNotIn("Contact", result)

    def test_empty_query_returns_unchanged(self):
        md = "# A\ntexte\n\n# B\nautre texte"
        self.assertEqual(research.filter_markdown(md, ""), md)

    def test_no_match_does_not_erase_everything(self):
        md = "# A\ntexte sans rapport\n\n# B\nautre texte sans rapport"
        result = research.filter_markdown(md, "mots qui n'apparaissent nulle part")
        self.assertEqual(result, md)

    def test_empty_markdown(self):
        self.assertEqual(research.filter_markdown("", "requête"), "")


class TestTruncate(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(research.truncate("court", 100), "court")

    def test_no_limit_untouched(self):
        self.assertEqual(research.truncate("texte", None), "texte")

    def test_truncates_and_marks(self):
        long_text = "mot " * 500
        result = research.truncate(long_text, 200)
        self.assertLessEqual(len(result), 260)
        self.assertIn("tronqué", result)

    def test_cuts_on_paragraph_boundary_when_close_enough(self):
        text = "a" * 90 + "\n\n" + "b" * 90
        result = research.truncate(text, 100)
        self.assertTrue(result.startswith("a" * 90))
        self.assertNotIn("b", result)


class TestToMarkdown(unittest.TestCase):
    def test_empty_html(self):
        self.assertEqual(research.to_markdown("", "https://x.test"), "")

    def test_extracts_article_content(self):
        html = (
            "<html><head><title>Test</title></head><body>"
            "<nav>menu à ignorer</nav>"
            "<article><h1>Titre principal</h1>"
            "<p>Un altimètre Badin authentique avec plaque constructeur d'origine, "
            "provenant d'un avion militaire réformé dans les années soixante.</p></article>"
            "<footer>pied de page à ignorer</footer>"
            "</body></html>"
        )
        markdown = research.to_markdown(html, "https://x.test")
        self.assertIn("Badin", markdown)
        self.assertIn("plaque constructeur", markdown)


class TestExtractMetadata(unittest.TestCase):
    def test_title_from_html_tag(self):
        html = "<html><head><title>Ma page</title></head><body>contenu</body></html>"
        meta = research.extract_metadata(html, "https://x.test")
        self.assertEqual(meta["title"], "Ma page")

    def test_includes_source_url(self):
        meta = research.extract_metadata("<html></html>", "https://x.test/page")
        self.assertEqual(meta["sourceURL"], "https://x.test/page")


class TestExtractLinks(unittest.TestCase):
    def test_splits_internal_external(self):
        html = (
            '<html><body>'
            '<a href="/local">local</a>'
            '<a href="https://x.test/other">autre page interne</a>'
            '<a href="https://external.test/page">externe</a>'
            '<a href="#frag">fragment ignoré</a>'
            '<a href="mailto:a@b.com">mail ignoré</a>'
            "</body></html>"
        )
        links = research.extract_links(html, "https://x.test")
        self.assertEqual(len(links["internal"]), 2)
        self.assertEqual(len(links["external"]), 1)
        self.assertIn("https://external.test/page", links["external"])


class TestWriteResult(unittest.TestCase):
    def test_writes_file_with_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            result = research.ResearchResult(
                url="https://x.test/page", title="Titre", markdown="contenu utile"
            )
            path = research.write_result(out_dir, result)
            self.assertIsNotNone(path)
            content = path.read_text(encoding="utf-8")
            self.assertIn("sourceURL: https://x.test/page", content)
            self.assertIn("contenu utile", content)
            self.assertEqual(result.path, str(path))

    def test_error_result_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = research.ResearchResult(url="https://x.test", error="404")
            self.assertIsNone(research.write_result(Path(tmp), result))

    def test_manifest_lists_all_entries_including_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            results = [
                research.ResearchResult(url="https://ok.test", title="Ok", markdown="x"),
                research.ResearchResult(url="https://fail.test", error="timeout"),
            ]
            path = research.write_manifest(out_dir, results)
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["count"], 2)
            errors = [e for e in data["entries"] if e["error"]]
            self.assertEqual(len(errors), 1)


class TestResearchManyConcurrency(unittest.TestCase):
    """Régression : le Store SQLite doit survivre à un accès concurrent depuis
    le ThreadPoolExecutor de `research_many` (bug réel rencontré : sqlite3
    refuse par défaut qu'une connexion traverse un thread)."""

    def test_concurrent_cache_access_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.Store(path=Path(tmp) / "test.sqlite3")
            errors = []

            def hammer(i: int) -> None:
                try:
                    key = f"k{i % 3}"  # collisions volontaires entre threads
                    store.put_response(key, {"n": i})
                    store.get_response(key, ttl=3600)
                except Exception as exc:  # la régression levait ProgrammingError ici
                    errors.append(exc)

            threads = [threading.Thread(target=hammer, args=(i,)) for i in range(30)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            store.close()
            self.assertEqual(errors, [])

    def test_research_many_handles_mixed_success_and_failure(self):
        def fake_fetch(url, store=None, render=False, timeout=30, wait_for=None,
                       headers=None, use_cache=True, ttl=None):
            if "bad" in url:
                return {"url": url, "final_url": url, "status": None, "html": "",
                        "error": "connexion refusée", "from_cache": False}
            html = f"<html><head><title>{url}</title></head><body><p>contenu de {url}</p></body></html>"
            return {"url": url, "final_url": url, "status": 200, "html": html,
                    "error": None, "from_cache": False}

        original = research.fetch
        research.fetch = fake_fetch
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = cache.Store(path=Path(tmp) / "test.sqlite3")
                results = research.research_many(
                    ["https://a.test", "https://bad.test", "https://b.test"],
                    store=store, concurrency=3,
                )
                store.close()
        finally:
            research.fetch = original

        self.assertEqual(len(results), 3)
        by_url = {r.url: r for r in results}
        self.assertTrue(by_url["https://bad.test"].error)
        self.assertFalse(by_url["https://a.test"].error)
        self.assertIn("a.test", by_url["https://a.test"].markdown)
        # L'ordre de sortie doit respecter l'ordre d'entrée malgré le parallélisme.
        self.assertEqual([r.url for r in results],
                         ["https://a.test", "https://bad.test", "https://b.test"])


if __name__ == "__main__":
    unittest.main()
