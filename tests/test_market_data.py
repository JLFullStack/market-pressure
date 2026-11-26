"""Histórico salvo no banco: só o que falta é pedido ao Yahoo; salvo em dia e fora do pregão,
nada é pedido; no pregão, só o candle do dia (5d); mudança no passado (desdobramento, provento
novo) baixa os 10 anos de novo; o salvo no formato antigo, cortado em 5 anos, é baixado de novo
com os 10 anos."""

import unittest
import json
import urllib.parse
from datetime import date, timedelta
from unittest import mock

from marketpressure import browser
from marketpressure.comum import core, database, market_data, runtime

from .support import DAY, DatabaseTestCase, candles
from .test_browser import BrowserTestCase

RANGE_DAYS = {"5d": 7, "1mo": 30, "3mo": 91, "6mo": 182, "1y": 365, "2y": 730, "5y": 1826, "10y": 3652}
HOUR = 3600


def series(n, factor=1.0):
    """Candles brutos do Yahoo (volume inteiro, como o Yahoo manda), um por dia às 09:00 de
    Brasília desde 2021-01-01."""
    return [{"date": c["date"], "open": c["o"] * factor, "high": c["h"] * factor, "low": c["l"] * factor,
             "close": c["c"] * factor, "volume": round(c["v"]), "adjustedClose": c["adj"] * factor}
            for c in candles(n)]


def at(candle, hour):
    """Instante às hour horas (Brasília) do dia do candle."""
    return candle["date"] + (hour - 9) * HOUR


class FakeYahoo:
    """Responde /v8/finance/chart com os candles do range pedido (até now)."""

    def __init__(self, data, now):
        self.data, self.now, self.ranges = data, now, []

    def __call__(self, url):
        range_ = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["range"][0]
        self.ranges.append(range_)
        rows = [c for c in self.data if self.now - RANGE_DAYS[range_] * DAY <= c["date"] <= self.now]
        quote = {k: [c[k] for c in rows] for k in ("open", "high", "low", "close", "volume")}
        return {"chart": {"result": [{
            "timestamp": [c["date"] for c in rows], "events": {},
            "indicators": {"quote": [quote], "adjclose": [{"adjclose": [c["adjustedClose"] for c in rows]}]},
        }]}}


class StoredHistoryTest(DatabaseTestCase):
    def load(self, data, now):
        yahoo = FakeYahoo(data, now)
        with mock.patch.object(market_data, "http_json", yahoo):
            hist = market_data.load_history("PETR4", now=now)
        return hist, yahoo.ranges

    def saved(self):
        return len(database.read_table("history_b3_petr4"))

    def test_series_ends_on_a_thursday(self):
        self.assertEqual(core.local_time(series(1001)[-1]["date"]).strftime("%A"), "Thursday")

    def test_first_load_downloads_10_years_and_saves_closed_sessions(self):
        data = series(1001)
        hist, ranges = self.load(data, at(data[-1], 12))
        self.assertEqual(ranges, ["10y"])
        self.assertEqual(hist["candles"], data)
        self.assertEqual(self.saved(), 1000)  # o candle do pregão em andamento não é salvo
        self.assertTrue(database.table_path("history_b3_petr4").exists())

    def test_during_the_session_only_the_live_candle_is_fetched(self):
        data = series(1001)
        self.load(data, at(data[-1], 11))
        with mock.patch.object(database, "write_tables") as write:
            hist, ranges = self.load(data, at(data[-1], 15))
        self.assertEqual(ranges, ["5d"])
        self.assertEqual(hist["candles"], data)
        write.assert_not_called()

    def test_after_the_close_the_day_is_saved_and_nothing_else_is_fetched(self):
        data = series(1001)
        self.load(data, at(data[-1], 12))
        hist, ranges = self.load(data, at(data[-1], 19))
        self.assertEqual(ranges, ["1mo"])
        self.assertEqual(self.saved(), 1001)
        with mock.patch.object(database, "write_tables") as write:
            hist, ranges = self.load(data, at(data[-1], 23))
        self.assertEqual(ranges, [])  # salvo em dia e fora do pregão: nenhuma requisição
        self.assertEqual(hist["candles"], data)
        write.assert_not_called()

    def test_incomplete_candle_after_the_close_is_not_saved(self):
        # Logo depois do fechamento o Yahoo manda o candle do dia só com o fechamento provisório
        # (abertura, máxima, mínima e volume 0): ele fica de fora e é buscado de novo depois.
        data = series(1001)
        self.load(data, at(data[-1], 12))
        partial = data[:-1] + [{**data[-1], "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0}]
        hist, ranges = self.load(partial, at(data[-1], 19))
        self.assertEqual(hist["candles"], data[:-1])
        self.assertEqual(self.saved(), 1000)
        hist, ranges = self.load(data, at(data[-1], 23))
        self.assertEqual(ranges, ["1mo"])
        self.assertEqual(hist["candles"], data)
        self.assertEqual(self.saved(), 1001)

    def test_incomplete_candle_saved_before_is_fetched_again(self):
        data = series(1001)
        bad = data[:-1] + [{**data[-1], "open": 0.0, "high": 0.0, "low": 0.0, "volume": 0}]
        database.write_tables({"history_b3_petr4": market_data._history_to_rows({"candles": bad, "dividends": []})})
        hist, ranges = self.load(data, at(data[-1], 23))
        self.assertEqual(ranges, ["1mo"])
        self.assertEqual(hist["candles"], data)
        self.assertEqual(database.read_table("history_b3_petr4")[-1]["open"], data[-1]["open"])

    def test_gap_picks_a_larger_range(self):
        data = series(1100)
        self.load(data[:1001], at(data[1000], 12))
        hist, ranges = self.load(data, at(data[-1], 12))
        self.assertEqual(ranges, ["6mo"])
        self.assertEqual(hist["candles"], data)
        self.assertEqual(self.saved(), 1099)

    def test_split_downloads_everything_again(self):
        data = series(1001)
        self.load(data, at(data[-1], 12))
        split = series(1001, factor=0.5)  # desdobramento 2:1: o Yahoo ajusta todo o passado
        with self.assertLogs("marketpressure.comum.market_data", "INFO"):
            hist, ranges = self.load(split, at(split[-1], 19))
        self.assertEqual(ranges, ["1mo", "10y"])
        self.assertEqual(hist["candles"], split)
        self.assertEqual(database.read_table("history_b3_petr4")[0]["close"], split[0]["close"])

    def test_old_candles_are_dropped(self):
        data = series(3800)
        hist, _ = self.load(data, at(data[-1], 12))
        self.assertLessEqual(len(hist["candles"]), market_data.HISTORY_DAYS + 1)
        self.assertEqual(hist["candles"][-1], data[-1])

    def test_history_saved_with_5_years_is_downloaded_again_with_10(self):
        data = series(3800)
        now = at(data[-1], 19)
        old = [c for c in data if c["date"] >= now - market_data.LEGACY_HISTORY_DAYS * DAY]
        database.write_tables({"history_b3_petr4": market_data._history_to_rows({"candles": old, "dividends": []})})
        hist, ranges = self.load(data, now)
        self.assertEqual(ranges, ["10y"])
        self.assertGreater(len(hist["candles"]), 3600)
        self.assertGreater(self.saved(), 3600)
        hist, ranges = self.load(data, now + HOUR)
        self.assertEqual(ranges, [])  # já migrado: nada a pedir

    def test_last_closed_day_per_market(self):
        thursday = series(1001)[-1]
        self.assertEqual(market_data.last_closed_day("b3", at(thursday, 12)), "2023-09-27")
        self.assertEqual(market_data.last_closed_day("b3", at(thursday, 19)), "2023-09-28")
        self.assertEqual(market_data.last_closed_day("crypto", at(thursday, 12)), "2023-09-27")
        self.assertEqual(market_data.last_closed_day("fx", at(thursday, 12)), "2023-09-27")
        monday = thursday["date"] + 4 * DAY
        self.assertEqual(market_data.last_closed_day("b3", monday), "2023-09-29")  # sexta
        self.assertFalse(market_data.live_session("b3", monday - DAY))  # domingo
        self.assertTrue(market_data.live_session("crypto", monday - DAY))


class StoredHistoryD1Test(BrowserTestCase):
    def test_history_is_saved_in_d1_and_read_by_batch(self):
        data = series(1001)
        now = at(data[-1], 12)
        with mock.patch.object(market_data, "http_json", FakeYahoo(data, now)):
            market_data.load_history("PETR4", now=now)
        self.assertEqual(len(json.loads(self.worker.tables["history_b3_petr4"])), 1000)
        self.assertFalse(database.table_path("history_b3_petr4").exists())  # nada no Parquet
        market_data.prefetch_stored([("PETR4", "b3"), ("VALE3", "b3")])
        self.assertEqual(market_data.update_url("PETR4", now=now), market_data.history_url("PETR4", "b3", "5d"))
        self.assertIsNone(market_data.update_url("PETR4", now=at(data[-1], 8)))  # antes da abertura
        self.assertEqual(market_data.update_url("VALE3", now=now), market_data.history_url("VALE3"))

    def test_scan_prepare_returns_the_urls_of_the_batch(self):
        browser._scan["universe"] = [{"ticker": t} for t in ("AAAA3", "BBBB4", "CCCC3")]
        self.addCleanup(browser._scan.update, universe=[])
        with mock.patch.object(runtime, "http_request", wraps=self.worker) as calls:
            urls = json.loads(browser.scan_prepare(1, 3))
        self.assertEqual(urls, [market_data.history_url(t) for t in ("BBBB4", "CCCC3")])
        self.assertEqual(calls.call_count, 1)  # uma só leitura do D1 para o lote


class UniverseFallbackTest(DatabaseTestCase):
    YAHOO = [{"symbol": "PETR4.SA", "shortName": "PETROBRAS PN", "regularMarketPrice": 30.0,
              "regularMarketVolume": 1000, "averageDailyVolume3Month": 1000},
             {"symbol": "HGLG11.SA", "shortName": "FII CSHG LOG", "longName": "CSHG Logística Fundo de Investimento Imobiliário",
              "regularMarketPrice": 160.0, "regularMarketVolume": 100, "averageDailyVolume3Month": 100}]
    BRAPI = [{"ticker": "PETR4", "name": "Petrobras", "sector": "Energia", "kind": "acoes",
              "close": 30.0, "volume": 1000, "avg_volume": None}]

    def load(self, yahoo, brapi=BRAPI):
        def fail():
            raise market_data.UpstreamError("Yahoo recusou a sessão (HTTP 429)")
        with mock.patch.object(market_data, "load_yahoo_list", yahoo or fail), \
                mock.patch.object(market_data, "load_brapi_stocks", lambda: brapi):
            return market_data.load_universe()

    def test_without_yahoo_the_saved_yahoo_list_is_used_and_kept(self):
        saved = self.load(lambda: self.YAHOO)
        self.assertEqual({a["kind"] for a in saved}, {"acoes", "fundos"})
        with self.assertLogs("marketpressure.comum.market_data", "WARNING"):
            assets = self.load(None)
        self.assertEqual(assets, saved)
        self.assertEqual(database.read_table("universe"), saved)  # não sobrescreve com a reserva

    def test_without_yahoo_and_without_a_saved_yahoo_list_brapi_is_used(self):
        with self.assertLogs("marketpressure.comum.market_data", "WARNING"):
            assets = self.load(None)
        self.assertEqual([a["ticker"] for a in assets], ["PETR4"])
        self.assertFalse(database.exists("universe"))


class SplitAdjustTest(unittest.TestCase):
    @staticmethod
    def series(closes):
        return [{"date": 1_700_000_000 + k * 86400, "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": 100, "adj": c}
                for k, c in enumerate(closes)]

    def test_unadjusted_split_is_adjusted(self):
        candles = self.series([400.0, 402.0, 400.5] + [50.0, 50.2, 49.9, 50.1, 50.3, 50.0])
        divs = [{"date": candles[1]["date"], "amount": 8.0}, {"date": candles[5]["date"], "amount": 1.0}]
        fixed, fixed_divs = market_data.adjust_splits(candles, divs, "TEST11")
        self.assertAlmostEqual(fixed[0]["c"], 50.0)
        self.assertAlmostEqual(fixed[2]["c"], 400.5 / 8)
        self.assertEqual(fixed[0]["v"], 800)
        self.assertEqual([d["amount"] for d in fixed_divs], [1.0, 1.0])
        self.assertEqual(fixed[3:], candles[3:])

    def test_real_crash_and_isolated_jump_are_kept(self):
        crash = self.series([20.0, 20.1] + [4.6, 4.5, 4.7, 4.4, 4.6, 4.5])   # −77%: não é proporção inteira
        self.assertEqual(market_data.adjust_splits(crash, [], "AMER3")[0], crash)
        back = self.series([40.0, 40.2, 20.1, 40.3, 40.1, 40.0, 40.2, 40.4])   # metade num dia e volta
        self.assertEqual(market_data.adjust_splits(back, [], "XXXX3")[0], back)


class ClosedSessionsTest(DatabaseTestCase):
    """market_data.closed_sessions(): quem só usa pregões fechados (os backtests) não pede o candle
    do pregão em andamento quando o salvo já está em dia."""

    def setUp(self):
        super().setUp()
        market_data.raw_history = self._raw_history   # o real, com o Yahoo falso abaixo
        market_data._cache.clear()
        self.addCleanup(market_data._cache.clear)

    def test_saved_history_is_enough_during_the_session(self):
        data = series(1001)
        now = at(data[-1], 15)   # pregão aberto, salvo até o pregão anterior
        yahoo = FakeYahoo(data, at(data[-1], 11))
        with mock.patch.object(market_data, "http_json", yahoo):
            market_data.load_history("PETR4", now=at(data[-1], 11))
        yahoo.ranges.clear()
        with mock.patch.object(market_data, "http_json", yahoo), mock.patch("time.time", return_value=now):
            self.assertEqual(market_data.update_range(market_data.stored_history("PETR4", "b3"), "b3", now, live=False), None)
            with market_data.closed_sessions():
                closed = market_data.raw_history("PETR4")
            self.assertEqual(yahoo.ranges, [])            # nenhum pedido
            self.assertEqual(len(closed["candles"]), 1000)  # sem o candle ao vivo
            live = market_data.raw_history("PETR4")       # a Carteira continua com o candle ao vivo
            self.assertEqual(yahoo.ranges, ["5d"])
            self.assertEqual(len(live["candles"]), 1001)
            with market_data.closed_sessions():           # com o completo no cache, ele serve
                self.assertIs(market_data.raw_history("PETR4"), live)
            self.assertEqual(yahoo.ranges, ["5d"])

    def test_context_reaches_the_threads(self):
        seen = []
        with market_data.closed_sessions():
            for _item, result, _err in runtime.run_all(lambda x: market_data._closed_only.get(), range(4), 2):
                seen.append(result)
        self.assertEqual(seen, [True] * 4)
        self.assertFalse(market_data._closed_only.get())


class FakeBcb:
    """Responde a série 12 do Banco Central com uma taxa por dia útil entre as datas pedidas."""

    def __init__(self, rate=0.04, fail=False):
        self.calls, self.rate, self.fail = [], rate, fail

    def __call__(self, url):
        if self.fail:
            raise market_data.UpstreamError("BCB fora do ar")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        d1 = date(*reversed([int(x) for x in q["dataInicial"][0].split("/")]))
        d2 = date(*reversed([int(x) for x in q["dataFinal"][0].split("/")]))
        self.calls.append((d1.isoformat(), d2.isoformat()))
        out, d = [], d1
        while d <= d2:
            if d.weekday() < 5:
                out.append({"data": d.strftime("%d/%m/%Y"), "valor": str(self.rate)})
            d += timedelta(days=1)
        return out


class CdiTest(DatabaseTestCase):
    TODAY = date(2026, 11, 26)   # quinta-feira

    def load(self, start, bcb, today=None):
        with mock.patch.object(market_data, "http_json", bcb):
            return market_data.load_cdi(start, today or self.TODAY)["rates"]

    def test_first_load_fetches_and_saves(self):
        bcb = FakeBcb()
        rates = self.load(date(2026, 1, 1), bcb)
        self.assertEqual(bcb.calls, [("2026-01-01", "2026-11-26")])
        self.assertEqual(rates[0]["date"], "2026-01-01")
        self.assertEqual(len(database.read_table("cdi")), len(rates))

    def test_up_to_date_fetches_nothing(self):
        self.load(date(2026, 1, 5), FakeBcb())             # segunda-feira
        bcb = FakeBcb()
        rates = self.load(date(2026, 3, 1), bcb)          # início dentro do salvo
        self.assertEqual(bcb.calls, [])
        self.assertEqual(rates[0]["date"], "2026-03-02")   # 01/03 é domingo
        self.assertEqual(self.load(date(2026, 1, 3), bcb)[0]["date"], "2026-01-05")  # só o fim de semana antes
        self.assertEqual(bcb.calls, [])

    def test_only_the_missing_parts_are_fetched(self):
        self.load(date(2026, 1, 1), FakeBcb(), today=date(2026, 11, 10))
        bcb = FakeBcb(rate=0.05)
        rates = self.load(date(2025, 6, 1), bcb)
        self.assertEqual(bcb.calls, [("2025-06-01", "2025-12-31"), ("2026-11-03", "2026-11-26")])
        by_day = {r["date"]: r["rate"] for r in rates}
        self.assertEqual(by_day["2026-11-02"], 0.04)   # antes da sobreposição: o salvo
        self.assertEqual(by_day["2026-11-05"], 0.05)   # sobreposição: o BCB corrige os últimos dias
        self.assertEqual(rates[-1]["date"], "2026-11-26")
        self.assertEqual(len(database.read_table("cdi")), len(rates))

    def test_bcb_down_uses_the_saved_rates(self):
        self.load(date(2026, 1, 1), FakeBcb(), today=date(2026, 11, 10))
        rates = self.load(date(2026, 1, 1), FakeBcb(fail=True))
        self.assertEqual(rates[-1]["date"], "2026-11-10")
        with self.assertRaises(market_data.UpstreamError):   # sem nada salvo, a falha aparece
            database.write_tables({"cdi": []})
            self.load(date(2026, 1, 1), FakeBcb(fail=True))

    def test_regular_user_does_not_save(self):
        with mock.patch.object(runtime, "SHARED_WRITES", False):
            self.load(date(2026, 1, 1), FakeBcb())
        self.assertFalse(database.exists("cdi"))

