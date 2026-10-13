import unittest
from datetime import datetime
from unittest import mock

from marketpressure import screener, update
from marketpressure.core import SAO_PAULO
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
    def test_candidates_use_a_loose_cut_on_the_3_month_average_per_kind(self):
        listed = [{"ticker": t, "name": t, "sector": "S", "kind": kind, "close": 10, "volume": 1, "avg_volume": v / 10}
                  for t, kind, v in (("BAIX3", "acoes", 2e6), ("ALTA3", "acoes", 15e6), ("MEIO3", "acoes", 3.5e6),
                                     ("FUND11", "fundos", 2e6), ("ZZZZ11", "outro", 9e6))]
        with mock.patch.object(screener.market_data, "universe", return_value=listed):
            candidates = screener.load_universe()
        # ações: 60% de R$ 5 mi = 3 mi; fundos: 60% de R$ 3 mi = 1,8 mi; tipo fora de KINDS: fora
        self.assertEqual([a["ticker"] for a in candidates], ["ALTA3", "MEIO3", "FUND11"])
        self.assertEqual(candidates[2]["kind"], "fundos")

    def test_old_saved_list_without_kind_counts_as_stocks(self):
        listed = [{"ticker": "ALTA3", "name": "A", "sector": "S", "close": 10, "volume": 1e6}]
        with mock.patch.object(screener.market_data, "universe", return_value=listed):
            self.assertEqual(screener.load_universe()[0]["kind"], "acoes")

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


class KindsTest(DatabaseTestCase):
    def scan(self, universe, raw=None):
        patches = [mock.patch.object(screener, "load_universe", return_value=universe)]
        if raw:
            patches.append(mock.patch.object(screener.market_data, "raw_history", raw))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return screener.run_scan()

    def test_one_ranking_per_kind_and_etf_clones_are_dropped(self):
        universe = [{"ticker": t, "name": t, "sector": None, "kind": k}
                    for t, k in (("AAAA3", "acoes"), ("FUND11", "fundos"), ("BOVA11", "etfs"), ("BOVV11", "etfs"),
                                 ("AAPL34", "bdrs"))]
        scan = self.scan(universe)
        etfs = {a["ticker"]: a.get("duplicateOf") for a in scan["analyses"] if a["kind"] == "etfs"}
        # mesmas cotações sintéticas = mesmo índice: um dos dois fica, o outro aponta para ele
        self.assertIn(etfs, ({"BOVA11": None, "BOVV11": "BOVA11"}, {"BOVA11": "BOVV11", "BOVV11": None}))
        view = screener.scan_payload(scan)["views"]["fechamento"]
        self.assertEqual([r["kind"] for r in view["rankings"]], list(screener.KINDS))
        by_kind = {r["kind"]: r for r in view["rankings"]}
        self.assertEqual(by_kind["etfs"]["duplicates"], 1)
        self.assertEqual(by_kind["fundos"]["minAvgVolume"], 3_000_000)
        self.assertIsNone(by_kind["acoes"]["regime"])  # 1 ativo: amostra pequena
        screener.write_scan(scan)
        saved = screener.read_scan()
        self.assertEqual(screener.scan_payload(saved), screener.scan_payload(scan))
        # setor nulo (fundos, ETFs, BDRs) continua no ativo lido do banco: o ranking o usa
        self.assertTrue(all("sector" in a and "kind" in a for a in saved["analyses"]))

    def test_fixed_income_etf_is_left_out(self):
        def flat(ticker, market="b3"):
            data = raw_history(ticker, wave=0.0, drift=0.0003)
            for c in data["candles"]:
                c["high"], c["low"] = c["close"] * 1.0005, c["close"] * 0.9995
            return data
        scan = self.scan([{"ticker": "LFTS11", "name": "L", "sector": None, "kind": "etfs"},
                          {"ticker": "AAAA3", "name": "A", "sector": None, "kind": "acoes"}], flat)
        self.assertEqual([a["ticker"] for a in scan["analyses"]], ["AAAA3"])

    def test_bdr_pressure_has_no_volume_weight(self):
        series = candles(400)[:222]
        stock = screener.analyze_asset(series)["pressure"]
        bdr = screener.analyze_asset(series, kind="bdrs")["pressure"]
        self.assertAlmostEqual(bdr["total"], 0.40 * stock["momentum"] + 0.33 * stock["trend"] + 0.27 * stock["structure"])

    def test_funds_use_dividend_adjusted_prices(self):
        def with_dividends(ticker, market="b3"):
            data = raw_history(ticker)
            n = len(data["candles"])
            for k, c in enumerate(data["candles"]):
                c["adjustedClose"] = c["close"] * (1 - 0.0004 * (n - 1 - k))  # passado mais barato
            return data
        scan = self.scan([{"ticker": "FUND11", "name": "F", "sector": None, "kind": "fundos"}], with_dividends)
        a = scan["analyses"][0]
        raw = raw_history("FUND11")["candles"]
        self.assertAlmostEqual(a["fechamento"]["price"], raw[-1]["close"])  # o preço atual não muda
        self.assertEqual(a["kind"], "fundos")


class AssetKindTest(unittest.TestCase):
    def test_kind_by_ticker_and_yahoo_name(self):
        kind = screener.market_data.asset_kind
        self.assertEqual(kind("PETR4", "PETROBRAS   PN"), "acoes")
        self.assertEqual(kind("TAEE11", "TAESA       UNT     N2"), "acoes")
        self.assertEqual(kind("HGLG11", "FII HGLG PAXCI  ER", "Cshg Logistica - Fundo De Investimento Imobiliario"), "fundos")
        self.assertEqual(kind("KDIF11", "FI INFRA KDIF", "Kinea Infra Fundo Incentivado"), "fundos")
        self.assertEqual(kind("BOVA11", "ISHARES BOVACI", "ISHARES IBOVESPA CLASSE DE ÍNDICE"), "etfs")
        self.assertIsNone(kind("LFTS11", "IT NOW LFTS", "IT NOW TESOURO SELIC FUNDO DE ÍNDICE"))
        self.assertIsNone(kind("HASH11", "HASHDEX NCI CI", "HASHDEX NASDAQ CRYPTO INDEX FUNDO DE ÍNDICE"))
        self.assertEqual(kind("AAPL34", "APPLE       DRN"), "bdrs")
        self.assertIsNone(kind("PETR4F", "PETROBRAS   PN"))
        self.assertIsNone(kind("BBDC10", "BRADESCO    DIR"))

    def test_isolated_bad_candles_are_dropped(self):
        series = candles(60)
        for c in series[30:33]:  # 3 pregões em ~1% do preço (como o XPML11 no Yahoo)
            c["c"] = c["c"] / 100
        with self.assertLogs("marketpressure.market_data", "WARNING"):
            kept = screener.market_data.drop_bad_candles(series, "XPML11")
        self.assertEqual(len(kept), 57)
        jump = candles(60)
        for c in jump[30:]:  # mudança de patamar (só de um lado) fica
            c["c"] *= 3
        self.assertEqual(len(screener.market_data.drop_bad_candles(jump)), 60)


def sp(day, hour):
    """Instante (unix) num dia e hora de Brasília."""
    return datetime.fromisoformat(f"{day}T{hour:02d}:00").replace(tzinfo=SAO_PAULO).timestamp()


class ScanFreshnessTest(unittest.TestCase):
    def fresh(self, made, now):
        job = screener.ScanJob.__new__(screener.ScanJob)
        job.scan = {"timestamp": made * 1000}
        return job._fresh(now)

    def test_scan_after_the_close_lasts_until_the_next_session(self):
        thursday_close = sp("2023-09-28", 18) + 1800
        self.assertTrue(self.fresh(thursday_close, sp("2023-09-29", 9)))   # sexta antes da abertura
        self.assertFalse(self.fresh(thursday_close, sp("2023-09-29", 11)))  # sexta, pregão aberto
        self.assertTrue(self.fresh(sp("2023-09-29", 19), sp("2023-10-01", 15)))  # fim de semana
        self.assertFalse(self.fresh(sp("2023-09-28", 17), sp("2023-09-28", 19)))  # feita no pregão

    def test_scan_during_the_session_lasts_the_ttl(self):
        made = sp("2023-09-28", 12)
        self.assertTrue(self.fresh(made, made + 59 * 60))
        self.assertFalse(self.fresh(made, made + 61 * 60))


class DailyUpdateTest(DatabaseTestCase):
    def test_update_saves_the_scan(self):
        universe = [{"ticker": t, "name": t, "sector": "Teste"} for t in ("AAAA3", "BBBB4")]
        with mock.patch.object(screener, "load_universe", return_value=universe), \
                self.assertLogs("marketpressure.update", "INFO"):
            scan = update.run()
        self.assertEqual(screener.scan_payload(screener.read_scan()), screener.scan_payload(scan))


if __name__ == "__main__":
    unittest.main()
