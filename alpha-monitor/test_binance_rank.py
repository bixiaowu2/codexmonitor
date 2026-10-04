import unittest
import binance_rank


class RankTests(unittest.TestCase):
    def payload(self, **row):
        base = {"chainId": "56", "contractAddress": "0x1234567890", "symbol": "AAA",
                "price": "1.2", "liquidity": "1000", "volume1h": "2000",
                "holders": "42", "holdersTop10Percent": "80.5", "auditInfo": {"riskLevel": 1}}
        base.update(row)
        return {"code": "000000", "data": {"tokens": [base]}}

    def test_normalizes_exact_market_fields(self):
        result = binance_rank.normalize(self.payload(), checked_at=100)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["rank"], 1)
        self.assertEqual(result["entries"][0]["liquidity"], 1000.0)

    def test_business_failure_and_missing_rows_are_rejected(self):
        with self.assertRaises(ValueError):
            binance_rank.normalize({"code": "-1", "data": {"tokens": []}})
        with self.assertRaises(ValueError):
            binance_rank.normalize({"code": "000000", "data": {}})

    def test_attach_requires_chain_and_address(self):
        snap = binance_rank.normalize(self.payload(), checked_at=100)
        rows = [{"chain_id": "56", "address": "0x1234567890"},
                {"chain_id": "56", "address": "0x9999999999"}]
        self.assertEqual(binance_rank.attach(rows, snap, now=101, max_age=10), 1)
        self.assertEqual(rows[0]["crypto_market_rank"]["status"], "ok")
        self.assertEqual(rows[1]["crypto_market_rank"]["status"], "not_ranked")

    def test_stale_is_explicit(self):
        snap = binance_rank.normalize(self.payload(), checked_at=100)
        rows = [{"chain_id": "56", "address": "0x1234567890"}]
        binance_rank.attach(rows, snap, now=111, max_age=10)
        self.assertEqual(rows[0]["crypto_market_rank"]["status"], "stale")

    def test_fetch_turns_transport_failure_into_separate_health(self):
        class API:
            def get(self, *_args):
                raise RuntimeError("network")
        snapshot, health = binance_rank.fetch(API())
        self.assertEqual(snapshot["entries"], [])
        self.assertEqual(health["status"], "error")


if __name__ == "__main__":
    unittest.main()
