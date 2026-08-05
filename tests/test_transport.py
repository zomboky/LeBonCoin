"""Le parsing de `__NEXT_DATA__` doit fonctionner sans réseau ni navigateur.

C'est le repli de dernier recours quand l'API est fermée ; il doit être testable
sur du HTML figé puisque aucun appel live n'est possible depuis ce conteneur
(DataDome bloque les IP de datacenter — voir README).
"""

import unittest

from golddigger02.transport import Blocked, extract_next_data


def wrap(payload_json: str) -> str:
    return (
        "<html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{payload_json}</script>'
        "</body></html>"
    )


class TestExtractNextData(unittest.TestCase):
    def test_finds_ads_nested_in_props(self):
        html = wrap(
            '{"props":{"pageProps":{"searchData":{"ads":[{"list_id":"1"}],"total":1}}}}'
        )
        result = extract_next_data(html)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["ads"][0]["list_id"], "1")

    def test_no_next_data_block_raises_blocked(self):
        with self.assertRaises(Blocked):
            extract_next_data("<html><body>mur datadome, rien ici</body></html>")

    def test_next_data_without_ads_raises_blocked(self):
        html = wrap('{"props":{"pageProps":{"other":"stuff"}}}')
        with self.assertRaises(Blocked):
            extract_next_data(html)

    def test_malformed_json_raises(self):
        html = wrap("{not valid json")
        with self.assertRaises(Exception):
            extract_next_data(html)


if __name__ == "__main__":
    unittest.main()
