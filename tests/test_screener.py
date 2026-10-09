import unittest
from unittest import mock

from marketpressure import screener
from tests.support import DatabaseTestCase, candles


class AnalysisTest(unittest.TestCase):
    def test_backtest_and_pressure_on_synthetic_history(self):
        series = candles(400)
        result = screener.analyze_asset(series[:222])  # candle 221 é um sinal do setup
        self.assertTrue(result["setup"]["ok"])
        self.assertTrue(0 <= result["pressure"]["total"] <= 100)
        bt = screener.backtest(screener.compute_indicators(series), strict=False)
        self.assertEqual(set(bt), {"wins", "trades", "winRate", "avgReturn"})

    def test_classifications(self):
        self.assertEqual([screener.bottle_state(p) for p in (80, 65, 10)], ["cheia", "quase", "enchendo"])
        self.assertEqual([screener.regime(b) for b in (70, 50, 10)], ["alta", "misto", "defensivo"])


class ScanStorageTest(DatabaseTestCase):
    def test_saved_scan_gives_same_payload(self):
        universe = [{"ticker": t, "name": t, "sector": "Teste"} for t in ("AAAA3", "BBBB4")]
        with mock.patch.object(screener, "load_universe", return_value=universe):
            scan = screener.run_scan()
        self.assertEqual(scan["failures"], [])
        screener.write_scan(scan)
        self.assertEqual(screener.scan_payload(screener.read_scan()), screener.scan_payload(scan))


if __name__ == "__main__":
    unittest.main()
