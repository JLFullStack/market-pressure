"""Versão publicada (Python no navegador): banco no D1 pela rota /db, fontes pelo /proxy e a
varredura em etapas de browser.py. O Worker do Cloudflare é simulado por FakeWorker."""

import base64
import json
import urllib.parse
from unittest import mock

from marketpressure import browser, database, market_data, runtime, screener
from marketpressure import portfolio_store as store
from tests.support import DatabaseTestCase


class FakeWorker:
    """Rotas /db (como worker/index.js: guarda o texto de cada tabela como veio) e /proxy."""

    def __init__(self):
        self.tables, self.backup, self.proxied = {}, {}, []

    def __call__(self, method, url, body=None):
        parts = urllib.parse.urlsplit(url)
        if parts.path == "/db" and method == "GET":
            names = urllib.parse.parse_qs(parts.query)["names"][0].split(",")
            return 200, "{" + ",".join(f"{json.dumps(n)}:{self.tables.get(n, 'null')}" for n in names) + "}"
        if parts.path == "/db" and method == "PUT":
            data = json.loads(body)
            for name, rows in data["tables"].items():
                if data["backup"] and name in self.tables:
                    self.backup[name] = self.tables[name]
                self.tables[name] = rows
            return 200, '{"ok": true}'
        if parts.path == "/proxy":
            self.proxied.append(urllib.parse.parse_qs(parts.query)["url"][0])
            return 503, '{"message": "fonte fora do ar"}'
        return 404, '{"message": "não encontrado"}'


class BrowserTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.worker = FakeWorker()
        for patcher in (mock.patch.object(runtime, "BROWSER", True), mock.patch.object(runtime, "REMOTE", True),
                        mock.patch.object(runtime, "http_request", self.worker)):
            patcher.start()
            self.addCleanup(patcher.stop)


class RemoteDatabaseTest(BrowserTestCase):
    def test_actions_are_saved_in_d1_with_backup(self):
        created = store.apply_action({"action": "criar_carteira", "name": "Principal"})
        store.apply_action({"action": "nova_operacao", "id": created["id"], "ticker": "petr4", "market": "b3",
                            "cls": "Ações", "type": "compra", "date": "2021-03-01", "quantity": "100",
                            "price": "10,50", "fees": "0", "fx": "1"})
        doc = store.read_doc()
        self.assertEqual(doc["portfolios"][0]["transactions"][0]["price"], 10.5)
        self.assertEqual(doc["portfolios"][0]["transactions"][0]["quantity"], 100)
        self.assertEqual(json.loads(self.worker.backup["transactions"]), [])
        self.assertFalse(database.table_path("transactions").exists())  # nada no Parquet

    def test_value_outside_column_type_writes_nothing(self):
        with self.assertRaises(TypeError):
            database.write_tables({"universe": [{"ticker": "PETR4", "close": "caro"}]})
        self.assertEqual(self.worker.tables, {})
        self.assertFalse(database.exists("universe"))

    def test_d1_unavailable_is_os_error(self):
        with mock.patch.object(runtime, "http_request", return_value=(500, '{"message": "D1 fora"}')), \
                self.assertRaisesRegex(OSError, "D1 fora"):
            database.read_table("portfolios")


class ProxyTest(BrowserTestCase):
    URL = "https://query1.finance.yahoo.com/v8/finance/chart/PETR4.SA?range=10y"

    def test_primed_response_skips_the_network(self):
        market_data.prime(self.URL, 200, '{"chart": {"result": []}}')
        self.assertEqual(market_data.http_json(self.URL), {"chart": {"result": []}})
        self.assertEqual(self.worker.proxied, [])

    def test_errors_become_upstream_errors(self):
        with self.assertRaisesRegex(market_data.UpstreamError, "fonte fora do ar"):
            market_data.http_json(self.URL)
        self.assertEqual(self.worker.proxied, [self.URL])
        market_data.prime(self.URL, 0, "timeout")
        with self.assertRaisesRegex(market_data.UpstreamError, "timeout"):
            market_data.http_json(self.URL)

    def test_b3_json_inside_a_string(self):
        params = {"issuingCompany": "PETR", "language": "pt-br"}
        url = f"{market_data.B3_COMPANY_API}/GetDetail/" + \
            base64.b64encode(json.dumps(params).encode()).decode()
        market_data.prime(url, 200, json.dumps(json.dumps([{"codeCVM": "9512"}])))
        self.assertEqual(market_data.b3_call("GetDetail", params), [{"codeCVM": "9512"}])


class BrowserBridgeTest(BrowserTestCase):
    def test_handle_uses_the_same_routes(self):
        out = json.loads(browser.handle("GET", "/api/portfolios"))
        self.assertEqual(out, {"status": 200, "body": {"version": 2, "portfolios": []}})
        self.assertEqual(json.loads(browser.handle("GET", "/api/nada"))["status"], 404)
        self.assertEqual(json.loads(browser.handle("POST", "/api/carteira/acao"))["status"], 400)
        with mock.patch.object(market_data, "universe", return_value=[{"close": float("nan")}]):
            self.assertEqual(json.loads(browser.handle("GET", "/api/universe"))["body"], [{"close": None}])

    def test_scan_runs_in_steps_and_is_saved(self):
        universe = [{"ticker": t, "name": t, "sector": "Teste"} for t in ("AAAA3", "BBBB4")]
        job = screener.ScanJob()
        job.start = browser._request_scan
        with mock.patch.object(screener, "scan_job", job), \
                mock.patch.object(screener, "load_universe", return_value=universe):
            self.assertEqual(json.loads(browser.handle("GET", "/api/scan"))["body"]["state"], "running")
            self.assertTrue(browser.take_scan_request())
            self.assertFalse(browser.take_scan_request())
            self.assertEqual(json.loads(browser.scan_begin()), 2)
            urls = json.loads(browser.scan_prepare(0, 2))
            self.assertEqual(urls, [market_data.history_url(t) for t in ("AAAA3", "BBBB4")])
            browser.scan_step(0)
            self.assertEqual(json.loads(browser.handle("GET", "/api/scan"))["body"],
                             {"state": "running", "done": 1, "total": 2})
            browser.scan_step(1)
            browser.scan_end()
            status = json.loads(browser.handle("GET", "/api/scan"))["body"]
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["scan"]["universeSize"], 2)
        self.assertEqual(screener.scan_payload(screener.read_scan()), status["scan"])

    def test_scan_failure_is_reported(self):
        job = screener.ScanJob()
        job.start = browser._request_scan
        with mock.patch.object(screener, "scan_job", job), \
                mock.patch.object(screener, "load_universe", side_effect=market_data.UpstreamError("brapi fora")), \
                self.assertLogs("marketpressure.screener", "WARNING"):
            browser.handle("GET", "/api/scan")
            browser.take_scan_request()
            self.assertEqual(browser.scan_begin(), "null")
            status = json.loads(browser.handle("GET", "/api/scan"))["body"]
        self.assertEqual(status, {"state": "error", "message": "brapi fora", "scan": None})
