import unittest
from unittest import mock

from marketpressure import screener
from tests.support import DatabaseTestCase, candles, raw_history


class AnalysisTest(unittest.TestCase):
    def test_backtest_and_pressure_on_synthetic_history(self):
        series = candles(400)
        result = screener.analyze_asset(series[:222])  # candle 221 é um sinal do setup
        self.assertTrue(result["setup"]["ok"])
        self.assertTrue(0 <= result["pressure"]["total"] <= 100)
        bt = screener.backtest(screener.compute_indicators(series), strict=False)
        self.assertEqual(set(bt), {"wins", "trades", "winRate", "avgReturn"})

    def test_backtest_buys_again_only_in_a_new_pullback(self):
        opens = {221: 100, 222: 102, 226: 110, 231: 105, 233: 90}
        ind = {"candles": [{"o": opens.get(i, 1)} for i in range(240)]}
        signals, exits = {220, 221, 225, 232}, {230}
        with mock.patch.object(screener, "evaluate_setup", lambda ind, i: {"ok": i in signals, "okStrict": False}), \
                mock.patch.object(screener, "exit_signal", lambda ind, i: i in exits):
            bt = screener.backtest(ind, strict=False)
        # 100 e 110 vendidas a 105 (102 é o mesmo pullback da compra a 100); 90 segue aberta
        self.assertEqual((bt["wins"], bt["trades"]), (1, 2))
        self.assertAlmostEqual(bt["avgReturn"], (5 + (105 / 110 - 1) * 100) / 2)
        self.assertAlmostEqual(bt["winRate"], (1 + 2) / (2 + 4) * 100)

    def test_classifications(self):
        self.assertEqual([screener.bottle_state(p) for p in (80, 65, 10)], ["cheia", "quase", "enchendo"])
        self.assertEqual([screener.regime(b) for b in (70, 50, 10)], ["alta", "misto", "defensivo"])


class UniverseTest(DatabaseTestCase):
    def test_candidates_use_a_loose_cut_on_the_day_volume(self):
        floor = screener.CONFIG["minAvgVolume"]
        listed = [{"ticker": t, "name": t, "sector": "S", "close": 10, "volume": v}
                  for t, v in (("BAIX3", floor * 0.2 / 10), ("ALTA3", floor * 3 / 10), ("MEIO3", floor * 0.5 / 10))]
        with mock.patch.object(screener.market_data, "universe", return_value=listed):
            self.assertEqual([a["ticker"] for a in screener.load_universe()], ["ALTA3", "MEIO3"])

    def test_floor_uses_the_average_of_closed_sessions(self):
        def raw(ticker, market="b3"):
            data = raw_history(ticker)
            if ticker == "POUC3":  # R$ ~1 mi por pregão, abaixo do piso: fora do universo, não é falha
                for c in data["candles"]:
                    c["volume"] = 1e6 / c["close"]
            if ticker == "FRAC3":  # dias fracos só no fim: a média de 20 pregões continua acima do piso
                for c in data["candles"][-3:]:
                    c["volume"] = 1
            return data
        universe = [{"ticker": t, "name": t, "sector": "Teste"} for t in ("AAAA3", "POUC3", "FRAC3")]
        with mock.patch.object(screener.market_data, "raw_history", raw), \
                mock.patch.object(screener, "load_universe", return_value=universe):
            scan = screener.run_scan()
        self.assertEqual(sorted(a["ticker"] for a in scan["analyses"]), ["AAAA3", "FRAC3"])
        self.assertEqual((scan["failures"], scan["universeSize"]), ([], 2))

    def test_live_candle_is_left_out_of_the_average(self):
        series = candles(300)
        for c in series[:-1]:
            c["v"] = screener.CONFIG["minAvgVolume"] * 0.9 / c["c"]
        series[-1]["v"] = 1e12  # volume do pregão em andamento não pode salvar o ativo
        with mock.patch.object(screener.market_data, "history", return_value={"candles": series}), \
                mock.patch.object(screener, "session_in_progress", return_value=True):
            self.assertIsNone(screener.analyze_ticker({"ticker": "AAAA3"}))


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
