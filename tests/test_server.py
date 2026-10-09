"""Rotas HTTP com o servidor de verdade numa porta livre e banco temporário."""

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from marketpressure import market_data, server
from tests.support import DatabaseTestCase


class ServerTest(DatabaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        is_json = response.getheader("Content-Type", "").startswith("application/json")
        return response.status, json.loads(raw) if is_json else raw, response

    def test_pages_and_assets_are_served(self):
        for path in ("/", "/carteira", "/assets/js/core.js", "/assets/css/styles.css"):
            self.assertEqual(self.request("GET", path)[0], 200, path)

    def test_code_and_database_are_private(self):
        for path in ("/server.py", "/marketpressure/server.py", "/../data/portfolios.parquet",
                     "/%2e%2e/marketpressure/config.py", "/.env", "/data/portfolios.parquet"):
            self.assertEqual(self.request("GET", path)[0], 404, path)

    def test_actions(self):
        status, data, _ = self.request("POST", "/api/carteira/acao", {"action": "criar_carteira", "name": "Teste"})
        self.assertEqual(status, 200)
        pid = data["id"]
        status, data, _ = self.request("GET", f"/api/carteira/{pid}/estrutura")
        self.assertEqual((status, data["hasEntries"]), (200, False))
        self.assertEqual(self.request("GET", f"/api/carteira/{pid}/analise")[0], 400)
        self.assertEqual(self.request("GET", "/api/carteira/nao-existe/estrutura")[0], 404)
        status, data, _ = self.request("POST", "/api/carteira/acao", {"action": "xyz"})
        self.assertEqual((status, data["message"]), (400, "Ação desconhecida."))

    def test_external_sites_cannot_call_the_api(self):
        status, _, _ = self.request("POST", "/api/carteira/acao", {"action": "criar_carteira", "name": "X"},
                                    {"Origin": "https://site-qualquer.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.request("GET", "/api/portfolios")[1]["portfolios"], [])
        status, _, response = self.request("POST", "/api/carteira/acao", {"action": "criar_carteira", "name": "X"},
                                           {"Origin": "http://localhost:5500"})
        self.assertEqual(status, 200)
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "http://localhost:5500")

    def test_error_statuses(self):
        self.assertEqual(self.request("GET", "/api/cdi?from=ontem")[0], 400)
        self.assertEqual(self.request("GET", "/api/history/PETR-4")[0], 400)
        self.assertEqual(self.request("PUT", "/api/carteira/acao", {})[0], 405)
        self.assertEqual(self.request("GET", "/api/nada")[0], 404)
        with mock.patch.object(market_data, "universe", side_effect=market_data.UpstreamError("brapi fora")):
            self.assertEqual(self.request("GET", "/api/universe")[:2], (502, {"message": "brapi fora"}))
        with mock.patch.object(market_data, "universe", side_effect=KeyError("bug")), \
                self.assertLogs("marketpressure.server", "ERROR"):
            self.assertEqual(self.request("GET", "/api/universe")[0], 500)

    def test_nan_becomes_null(self):
        with mock.patch.object(market_data, "universe", return_value=[{"close": float("nan")}]):
            self.assertEqual(self.request("GET", "/api/universe")[1], [{"close": None}])


if __name__ == "__main__":
    unittest.main()
