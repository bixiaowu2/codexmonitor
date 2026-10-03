import json
import tempfile
import time
import unittest
from pathlib import Path
from binance_web3 import read, apply, audit_flags


class BinanceWeb3Tests(unittest.TestCase):
    def test_only_fresh_exact_chain_address_matches(self):
        now = 1_000_000
        pair = {"chain": "bsc", "address": "0x" + "a" * 40}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "evidence.jsonl"
            p.write_text(json.dumps({"chain": "bsc", "address": pair["address"], "checked_at": now, "provider": "binance_web3_query-token-audit", "audit": {"is_honeypot": "0"}}) + "\n")
            evidence, health = read(p, [pair], now=now)
        self.assertEqual(health["matched"], 1)
        apply([pair], evidence)
        self.assertEqual(pair["binance_web3"]["audit"]["is_honeypot"], "0")

    def test_stale_or_non_binance_evidence_is_ignored(self):
        now = time.time()
        pair = {"chain": "bsc", "address": "0x" + "b" * 40}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "evidence.jsonl"
            p.write_text(json.dumps({"chain": "bsc", "address": pair["address"], "checked_at": now - 5000, "provider": "goplus"}) + "\n")
            evidence, health = read(p, [pair], now=now)
        self.assertFalse(evidence)
        self.assertEqual(health["status"], "partial")

    def test_high_risk_audit_becomes_local_block(self):
        pair = {"binance_web3": {"audit": {"hasResult": True, "isSupported": True,
            "riskLevelEnum": "HIGH", "riskItems": [{"details": [{"isHit": True,
            "riskType": "RISK", "title": "Honeypot"}]}]}}}
        blocks, warnings = audit_flags(pair)
        self.assertIn("Binance Web3审计高风险", blocks)
        self.assertIn("Honeypot", blocks)
        self.assertFalse(warnings)


if __name__ == "__main__":
    unittest.main()
