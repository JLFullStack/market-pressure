"""Estratégia de venda (aba Configurações) e a indicação do ranking do Screener."""

import unittest

from marketpressure import api, screener
from marketpressure import strategy as st

from .support import DatabaseTestCase


class StrategyTest(DatabaseTestCase):
    def test_default_until_saved(self):
        self.assertEqual(st.load(), st.DEFAULT)
        saved, error = st.from_form({"strategy": "parcial", "target": "15,5", "fraction": "30"})
        self.assertIsNone(error)
        st.save(saved)
        self.assertEqual(st.load(), {"key": "parcial", "target": 0.155, "fraction": 0.3})
        self.assertEqual(st.describe(st.load())["text"],
                         "Vende 30% de cada compra ao atingir +15,5% sobre o preço dela; o restante na reversão de tendência.")

    def test_validation(self):
        for body in ({"strategy": "x", "target": 10, "fraction": 50},
                     {"strategy": "parcial", "target": 0, "fraction": 50},
                     {"strategy": "parcial", "target": 10, "fraction": 0},
                     {"strategy": "parcial", "target": "abc", "fraction": 50}):
            self.assertIsNotNone(st.from_form(body)[1], body)

    def test_routes(self):
        status, body = api.call("POST", "/api/configuracoes", {}, lambda: {"strategy": "reversao", "target": 10, "fraction": 50})
        self.assertEqual(status, 200)
        self.assertEqual(body["strategy"]["key"], "reversao")
        self.assertEqual(api.call("GET", "/api/configuracoes", {}, lambda: None)[1]["strategy"]["key"], "reversao")
        status, body = api.call("POST", "/api/configuracoes", {}, lambda: {"strategy": "parcial", "target": 900, "fraction": 50})
        self.assertEqual(status, 400)

    def test_portfolio_follows_the_saved_strategy(self):
        def act(body):
            status, data = api.call("POST", "/api/carteira/acao", {}, lambda: body)
            self.assertEqual(status, 200, data)
            return data
        pid = act({"action": "criar_carteira", "name": "Teste"})["id"]
        act({"action": "nova_operacao", "id": pid, "ticker": "PETR4", "market": "b3", "cls": "Ações", "type": "compra",
             "date": "2021-06-01", "quantity": 10, "price": 100, "fees": 0, "fx": 1})

        def signal():
            status, body = api.call("GET", f"/api/carteira/{pid}/analise", {}, lambda: None)
            self.assertEqual(status, 200, body)
            return body["open"][0]
        first = signal()
        self.assertIn(first["status"], ("COMPRAR", "VENDER", "VENDER 50%", "MANTER"))
        self.assertTrue(first["statusReason"])
        api.call("POST", "/api/configuracoes", {}, lambda: {"strategy": "reversao", "target": 10, "fraction": 50})
        self.assertNotEqual(signal()["statusKey"], "partial")
        self.assertIsNone(signal()["partial"])


class AdviceTest(unittest.TestCase):
    ROW = {"ticker": "PETR4", "price": 50.0, "score": 70.0, "pressure": 65.0,
           "setup": {"ma": {"name": "MM20", "dist": 0.012}},
           "bt": {"wins": 6, "trades": 8, "winRate": 66.0, "avgReturn": 4.2}}

    def test_buy_with_exit_plan(self):
        a = screener.advice(self.ROW, None, st.DEFAULT)
        self.assertEqual(a["action"], "comprar")
        self.assertIn("vender 50% em +10% (R$ 55,00)", a["reason"])
        a = screener.advice(self.ROW, None, {"key": "reversao", "target": 0.1, "fraction": 0.5})
        self.assertIn("vender tudo na reversão", a["reason"])

    def test_wait_when_already_bought_in_the_region(self):
        lots = [{"date": "2026-09-28", "price": 49.0}]
        a = screener.advice(self.ROW, lots, st.DEFAULT)
        self.assertEqual(a["action"], "aguardar")
        self.assertIn("28/09/2026", a["reason"])
        far = screener.advice(self.ROW, [{"date": "2026-09-28", "price": 60.0}], st.DEFAULT)
        self.assertEqual(far["action"], "comprar")
        self.assertIn("Já na carteira", far["reason"])

    def test_wait_when_the_history_loses_money(self):
        row = {**self.ROW, "bt": {"wins": 1, "trades": 5, "winRate": 33.0, "avgReturn": -2.5}}
        self.assertEqual(screener.advice(row, None, st.DEFAULT)["action"], "aguardar")
        few = {**self.ROW, "bt": {"wins": 0, "trades": 2, "winRate": 33.0, "avgReturn": -2.5}}
        self.assertEqual(screener.advice(few, None, st.DEFAULT)["action"], "comprar")


if __name__ == "__main__":
    unittest.main()
