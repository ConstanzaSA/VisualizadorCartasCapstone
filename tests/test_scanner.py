import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "VisionPorComputador" / "ocr"))
from parser import MTGCardMatcher
from scryfall import Scryfall
import requests


class ScannerTests(unittest.TestCase):
    def test_number_and_edition(self):
        matcher = MTGCardMatcher({"fin", "tfin"})
        number, rarity, editions = matcher.extract_candidates("R 0100 FIN EN")
        self.assertEqual((number, rarity, editions), ("0100", "r", ["fin"]))

    def test_no_text(self):
        self.assertEqual(MTGCardMatcher({"fin"}).extract_candidates(""), (None, None, []))

    def test_token(self):
        self.assertEqual(MTGCardMatcher({"fin", "tfin"}).extract_candidates("T 0014 FIN EN")[2], ["tfin"])

    def test_query_and_json(self):
        client = Scryfall()
        response = Mock(status_code=200)
        response.json.return_value = {"data": [{"name": "Example"}]}
        client.session.get = Mock(return_value=response)
        query = client.query("fin", "100", False)
        self.assertEqual(query, "set:fin cn:100 lang:en is:nonfoil")
        self.assertEqual(client.search(query)["name"], "Example")
        self.assertEqual(client.session.get.call_args.kwargs["params"], {"q": query})
        self.assertTrue(client.query("fin", "100", True).endswith("is:foil"))

    def test_not_found(self):
        client = Scryfall()
        client.session.get = Mock(return_value=Mock(status_code=404))
        self.assertIsNone(client.search("set:fin cn:999999 lang:en is:nonfoil"))

    def test_network_error_is_not_not_found(self):
        client = Scryfall()
        client.session.get = Mock(side_effect=requests.Timeout("Sin conexión"))
        with self.assertRaises(RuntimeError):
            client.search("set:fin cn:100 lang:en is:nonfoil")


if __name__ == "__main__":
    unittest.main()
