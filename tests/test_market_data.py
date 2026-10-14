"""Histórico salvo no banco: só o que falta é pedido ao Yahoo; salvo em dia e fora do pregão,
nada é pedido; no pregão, só o candle do dia (5d); mudança no passado (desdobramento, provento
novo) baixa os 10 anos de novo; o salvo no formato antigo, cortado em 5 anos, é baixado de novo
com os 10 anos."""

import json
import urllib.parse
from unittest import mock

from marketpressure import browser, core, database, market_data, runtime

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
        with self.assertLogs("marketpressure.market_data", "INFO"):
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
