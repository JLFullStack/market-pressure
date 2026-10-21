"""Rotas HTTP com o servidor de verdade numa porta livre e banco temporário."""

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from marketpressure import market_data, screener, server
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
        for path in ("/", "/carteira", "/backtest", "/configuracoes", "/assets/js/core.js", "/assets/js/backtest.js",
                     "/assets/js/configuracoes.js", "/assets/css/styles.css"):
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
        self.assertEqual(self.request("GET", "/api/scan/prompt?ticker=AAAA3&mode=outro")[0], 400)
        with mock.patch.object(screener.scan_job, "scan", None):
            self.assertEqual(self.request("GET", "/api/scan/prompt?ticker=AAAA3")[0], 404)
        with mock.patch.object(market_data, "universe", side_effect=market_data.UpstreamError("brapi fora")):
            self.assertEqual(self.request("GET", "/api/universe")[:2], (502, {"message": "brapi fora"}))
        with mock.patch.object(market_data, "universe", side_effect=KeyError("bug")), \
                self.assertLogs("marketpressure.api", "ERROR"):
            self.assertEqual(self.request("GET", "/api/universe")[0], 500)

    def test_nan_becomes_null(self):
        with mock.patch.object(market_data, "universe", return_value=[{"close": float("nan")}]):
            self.assertEqual(self.request("GET", "/api/universe")[1], [{"close": None}])

    def test_backtest_route(self):
        status, data, _ = self.request("GET", "/api/backtest?ticker=petr4&target=10&fraction=50")
        self.assertEqual(status, 200)
        self.assertEqual((data["ticker"], data["kind"], data["rule"]["fraction"]), ("PETR4", "acoes", 50))
        self.assertEqual(len(data["candles"]), len(data["equity"]))
        self.assertTrue(data["lots"])
        self.assertEqual(data["stats"]["lots"], len(data["lots"]))
        self.assertIsNotNone(data["onlyB"])
        s = data["stats"]
        self.assertEqual((s["amount"], s["invested"]), (1000, 1000 * s["lots"]))
        self.assertAlmostEqual(s["profit"], s["sumReturn"] * 10)
        self.assertAlmostEqual(s["realized"] + s["unrealized"], s["profit"])
        self.assertAlmostEqual(data["equity"][-1]["value"], s["profit"])
        self.assertAlmostEqual(sum(lot["profit"] for lot in data["lots"]), s["profit"])
        self.assertTrue(0 < s["maxCapital"] <= s["invested"])
        b = data["onlyB"]
        self.assertTrue(0 < b["maxCapital"] <= b["invested"])
        # "ANALISAR BACKTEST" do modo Um ativo: a mesma consulta → o texto com os lotes.
        status, prompt, _ = self.request("GET", "/api/backtest/prompt?ticker=petr4&target=10&fraction=50&saida=manual")
        self.assertEqual(status, 200)
        self.assertIn("PETR4", prompt["title"])
        self.assertIn("# Compras (lotes)", prompt["text"])
        self.assertIn("alvo e parte escolhidos à mão", prompt["text"])
        self.assertEqual(prompt["text"].count("\n| "), len(data["lots"]) + 1)   # cabeçalho + um lote por linha
        self.assertEqual(self.request("GET", "/api/backtest/prompt?ticker=")[0], 400)
        self.assertGreaterEqual(b["maxCapital"], s["maxCapital"])  # sem parcial, nada volta antes da regra B
        self.assertAlmostEqual(b["returnOnMaxCapital"], b["profit"] / b["maxCapital"] * 100)
        self.assertEqual(len(data["equityB"]), len(data["equity"]))
        self.assertAlmostEqual(data["equityB"][-1]["value"], b["profit"])
        self.assertIn("a mais que", data["compare"])
        only = self.request("GET", "/api/backtest?ticker=PETR4&fraction=0&saida=manual")[1]
        self.assertEqual((only["onlyB"], only["equityB"], only["compare"]), (None, None, None))
        doubled = self.request("GET", "/api/backtest?ticker=PETR4&amount=2000")[1]["stats"]
        self.assertAlmostEqual(doubled["profit"], 2 * s["profit"])
        self.assertAlmostEqual(doubled["returnOnMaxCapital"], s["returnOnMaxCapital"])
        status, period, _ = self.request("GET", "/api/backtest?ticker=PETR4&from=2021-10-01&to=2021-12-31")
        self.assertEqual(status, 200)
        self.assertEqual((period["bounds"]["from"], period["candles"][-1]["time"]), ("2021-10-01", "2021-12-31"))
        self.assertEqual(period["candles"][0]["time"], "2021-10-01")
        self.assertTrue(all("2021-10-01" < lot["entry"]["date"] <= "2021-12-31" for lot in period["lots"]))
        self.assertLess(len(period["lots"]), len(data["lots"]))
        self.assertIsNone(period["note"])
        early = self.request("GET", "/api/backtest?ticker=PETR4&from=2020-01-01")[1]
        self.assertEqual(early["bounds"]["from"], data["bounds"]["min"])  # ajustado: as médias precisam de 220 pregões
        self.assertIn("Início ajustado", early["note"])
        for query in ("ticker=PETR4&market=xx", "ticker=PETR4&target=abc&saida=manual", "ticker=&market=b3",
                      "ticker=PETR4&fraction=150&saida=manual", "ticker=PETR4&from=ontem", "ticker=PETR4&from=2021-12-01&to=2021-11-01",
                      "ticker=PETR4&to=2021-02-01", "ticker=PETR4&amount=0", "ticker=PETR4&amount=abc"):
            self.assertEqual(self.request("GET", f"/api/backtest?{query}")[0], 400, query)

    def test_portfolio_backtest_route(self):
        body = {"universe": "lista", "tickers": "PETR4, VALE3", "initial": "5000", "cdi": False}
        status, data, _ = self.request("POST", "/api/backtest/carteira", body)
        self.assertEqual(status, 200)
        self.assertEqual((data["universe"]["candidates"], data["universe"]["ranked"]), (2, 2))
        self.assertEqual({a["ticker"] for a in data["assets"]}, set(data["markers"]))
        self.assertEqual(data["strategies"][0]["metrics"]["contributed"], 5000)
        self.assertEqual(self.request("POST", "/api/backtest/carteira", {**body, "tickers": ""})[0], 400)
        self.assertEqual(self.request("POST", "/api/backtest/carteira", {**body, "position": "150"})[0], 400)
        # "ANALISAR BACKTEST": o mesmo corpo → o texto para a IA, com os números do resultado.
        status, prompt, _ = self.request("POST", "/api/backtest/carteira/prompt", body)
        self.assertEqual(status, 200)
        self.assertIn("# Seu papel", prompt["text"])
        self.assertIn("# O que o app já testou", prompt["text"])
        self.assertIn(data["period"], prompt["text"])
        self.assertIn("PETR4", prompt["text"])
        self.assertEqual(self.request("POST", "/api/backtest/carteira/prompt", {**body, "tickers": ""})[0], 400)
        # Robustez: com o histórico curto do teste não há 2 anos depois do início possível → 400 com o motivo.
        status, data, _ = self.request("POST", "/api/backtest/carteira/robustez", body)
        self.assertEqual(status, 400)
        self.assertIn("2 anos", data["message"])

    def test_goal_routes(self):
        pid = self.request("POST", "/api/carteira/acao", {"action": "criar_carteira", "name": "Metas"})[1]["id"]
        status, data, _ = self.request("GET", f"/api/carteira/{pid}/metas")
        self.assertEqual((status, data["goals"]), (200, []))
        fields = {"name": "Casa", "kind": "patrimonio", "target": "300000", "monthly": "2000", "rate": "10",
                  "inflation": "4", "usePortfolio": True, "adjust": True}
        status, data, _ = self.request("POST", f"/api/carteira/{pid}/metas/simular", fields)
        self.assertEqual(status, 200)
        self.assertEqual((data["portfolioValue"], data["goal"]["result"]["current"]), (0, 0))
        self.assertEqual(self.request("POST", f"/api/carteira/{pid}/metas/simular", {**fields, "target": "0"})[0], 400)
        self.request("POST", "/api/carteira/acao", {"action": "salvar_meta", "id": pid, **fields})
        status, data, _ = self.request("GET", f"/api/carteira/{pid}/metas")
        self.assertEqual([g["name"] for g in data["goals"]], ["Casa"])
        self.assertGreater(data["goals"][0]["result"]["reach"]["months"], 0)


    def test_portfolio_prompt(self):
        pid = self.request("POST", "/api/carteira/acao", {"action": "criar_carteira", "name": "IA"})[1]["id"]
        self.assertEqual(self.request("GET", f"/api/carteira/{pid}/prompt")[0], 400)
        for ticker, price in (("PETR4", 120), ("VALE3", 125)):
            self.request("POST", "/api/carteira/acao", {"action": "nova_operacao", "id": pid, "ticker": ticker, "market": "b3",
                                                        "type": "compra", "date": "2021-09-01", "quantity": "10",
                                                        "price": str(price), "fees": "0"})
        with mock.patch.object(market_data, "cdi", lambda start: {"rates": []}):
            status, data, _ = self.request("GET", f"/api/carteira/{pid}/prompt")
        self.assertEqual(status, 200)
        self.assertEqual(data["name"], "IA")
        for part in ("# Minha carteira: IA", "## PETR4", "## VALE3", "# Alocação por classe", "Situação do app",
                     "Compras abertas: 01/09/2021", "MANTER", "**Ações**",
                     "# Eventos e fundamentos", "Posição com evento de veto"):
            self.assertIn(part, data["text"])
        self.assertEqual(self.request("GET", "/api/carteira/nao-existe/prompt")[0], 404)

if __name__ == "__main__":
    unittest.main()
