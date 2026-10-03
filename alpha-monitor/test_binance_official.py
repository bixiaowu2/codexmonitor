import unittest
from binance_official import ticker_quote, token_list


class OfficialAlphaTests(unittest.TestCase):
    def test_nested_ticker_is_fresh(self):
        q, d = ticker_quote({"code": "000000", "data": [{"symbol": "ALPHA_1USDT", "lastPrice": "2.5", "closeTime": 1_000_000}]}, "ALPHA_1", 1000)
        self.assertEqual(q["source"], "binance_alpha_official")
        self.assertEqual(d["reason"], "ok")

    def test_stale_ticker_is_never_a_quote(self):
        q, d = ticker_quote({"data": {"symbol": "ALPHA_1USDT", "lastPrice": "2.5", "closeTime": 1}}, "ALPHA_1", 1000)
        self.assertIsNone(q)
        self.assertEqual(d["reason"], "stale_or_future")

    def test_token_list_normalizes_nested_list(self):
        rows, d = token_list({"data": {"list": [{"alphaId": "ALPHA_1", "contractAddress": "0x1"}, {"symbol": "bad"}]}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(d["count"], 1)


if __name__ == "__main__":
    unittest.main()
