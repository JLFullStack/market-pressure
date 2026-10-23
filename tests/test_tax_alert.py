"""Aviso de imposto: vendas de ações do mês perto ou acima da isenção de R$ 20 mil (tax_alert.py)."""

import unittest
from unittest import mock

from marketpressure import portfolio as pf, portfolio_store as store, tax_alert
from tests.support import DatabaseTestCase


def tx(ticker, kind, date, qty, price, fx=1):
    return {"id": f"{ticker}{date}{kind}", "ticker": ticker, "type": kind, "date": date, "quantity": qty,
            "price": price, "fees": 0, "fx": fx}


class StockSalesTest(unittest.TestCase):
    def test_only_stock_sales_of_the_month_count(self):
        txs = [tx("PETR4", "compra", "2026-09-01", 1000, 10), tx("BOVA11", "compra", "2026-09-01", 100, 100),
               tx("PETR4", "venda", "2026-10-05", 500, 12),        # R$ 6.000, lucro R$ 1.000
               tx("PETR4", "venda", "2026-09-20", 100, 11),        # outro mês
               tx("BOVA11", "venda", "2026-10-06", 50, 120)]       # ETF: não entra na isenção
        m = pf.stock_sales(txs, {"PETR4"}, "2026-10")
        self.assertEqual((m["sold"], m["count"]), (6000, 1))
        self.assertAlmostEqual(m["gain"], 1000)

    def test_gain_uses_the_average_price_and_counts_losses(self):
        txs = [tx("VALE3", "compra", "2026-01-02", 100, 50), tx("VALE3", "compra", "2026-02-02", 100, 70),
               tx("VALE3", "venda", "2026-10-02", 100, 55)]        # preço médio 60 → prejuízo de R$ 500
        m = pf.stock_sales(txs, {"VALE3"}, "2026-10")
        self.assertAlmostEqual(m["gain"], -500)

    def test_indicated_sales(self):
        positions = [
            {"ticker": "PETR4", "hasQuote": True, "statusKey": "sell", "status": "VENDER", "quantity": 100, "price": 30},
            {"ticker": "VALE3", "hasQuote": True, "statusKey": "partial", "status": "VENDER 50%", "quantity": 100,
             "price": 60, "partial": {"quantity": 50}},
            {"ticker": "ITUB4", "hasQuote": True, "statusKey": "hold", "status": "MANTER", "quantity": 100, "price": 30},
            {"ticker": "BOVA11", "hasQuote": True, "statusKey": "sell", "status": "VENDER", "quantity": 10, "price": 120},
        ]
        items = pf.indicated_stock_sales(positions, {"PETR4", "VALE3", "ITUB4"})
        self.assertEqual([(i["ticker"], i["value"]) for i in items], [("PETR4", 3000), ("VALE3", 3000)])


class TaxAlertTest(DatabaseTestCase):
    def portfolio(self, *txs):
        assets = {t["ticker"]: {"class": "Ações", "market": "b3"} for t in txs}
        return {"id": "p", "name": "X", "classes": [], "assets": assets, "transactions": list(txs), "fixedIncome": []}

    def test_levels(self):
        buy = tx("PETR4", "compra", "2026-09-01", 2000, 10)
        with mock.patch.object(tax_alert, "today_iso", lambda: "2026-10-20"):
            ok = tax_alert.status(self.portfolio(buy, tx("PETR4", "venda", "2026-10-05", 500, 12)))
            near = tax_alert.status(self.portfolio(buy, tx("PETR4", "venda", "2026-10-05", 1400, 12)))
            over = tax_alert.status(self.portfolio(buy, tx("PETR4", "venda", "2026-10-05", 1800, 12)))
        self.assertEqual((ok["level"], ok["show"]), ("ok", False))
        self.assertEqual((near["level"], near["show"], near["sold"]), ("near", True, 16800))
        self.assertIn("Faltam R$ 3.200,00", near["lines"][0])
        self.assertEqual(over["level"], "over")
        self.assertIn("R$ 540,00", over["lines"][0])                       # 15% de R$ 3.600 de lucro
        self.assertEqual(over["title"], "Vendas de ações em outubro de 2026: R$ 21.600,00 de R$ 20.000,00")

    def test_indicated_sales_that_would_pass_the_limit(self):
        p = self.portfolio(tx("PETR4", "compra", "2026-09-01", 2000, 10), tx("PETR4", "venda", "2026-10-05", 1000, 12))
        positions = [{"ticker": "PETR4", "hasQuote": True, "statusKey": "partial", "status": "VENDER 50%", "quantity": 1000,
                      "price": 12, "partial": {"quantity": 900}}]
        with mock.patch.object(tax_alert, "today_iso", lambda: "2026-10-20"):
            st = tax_alert.status(p, positions)
        self.assertEqual(st["level"], "ok")                                   # R$ 12 mil vendidos
        self.assertTrue(st["show"] and st["indicated"]["over"])               # + R$ 10.800 indicados
        self.assertIn("não deve esperar", st["lines"][-1])

    def test_disabled_and_settings(self):
        tax_alert.save({"enabled": False, "warnAt": 18000})
        self.assertIsNone(tax_alert.status(self.portfolio(tx("PETR4", "compra", "2026-09-01", 10, 10))))
        self.assertEqual(tax_alert.load(), {"enabled": False, "warnAt": 18000})
        self.assertIsNone(tax_alert.from_form({"enabled": True, "warnAt": "25000"})[0])
        self.assertEqual(tax_alert.from_form({"enabled": True, "warnAt": "12000"})[0], {"enabled": True, "warnAt": 12000})

    def test_preview_of_a_typed_sale(self):
        p = self.portfolio(tx("PETR4", "compra", "2026-09-01", 2000, 10), tx("PETR4", "venda", "2026-10-05", 1000, 12))
        typed = {"ticker": "PETR4", "market": "b3", "type": "venda", "date": "2026-10-21", "quantity": 900, "price": 12}
        r = tax_alert.preview(p, typed)
        self.assertEqual((r["level"], r["before"], r["sold"]), ("over", 12000, 22800))
        self.assertIn("Esta venda leva o mês acima do limite", r["lines"][0])
        self.assertIsNone(tax_alert.preview(p, {**typed, "type": "compra"}))
        etf = tax_alert.preview(self.portfolio(tx("BOVA11", "compra", "2026-09-01", 10, 100)),
                                {**typed, "ticker": "BOVA11", "quantity": 5})
        self.assertEqual(etf["level"], "info")

    def test_confirmation_of_a_registered_sale(self):
        pid = store.apply_action({"action": "criar_carteira", "name": "IR"})["id"]
        sale = {"action": "nova_operacao", "id": pid, "ticker": "PETR4", "market": "b3", "date": "2021-09-01", "fees": "0"}
        store.apply_action({**sale, "type": "compra", "quantity": "2000", "price": "10"})
        small = store.apply_action({**sale, "type": "venda", "quantity": "100", "price": "11", "date": "2021-09-02"})
        self.assertNotIn("taxAlert", small)
        big = store.apply_action({**sale, "type": "venda", "quantity": "1500", "price": "11", "date": "2021-09-03"})
        self.assertEqual(big["taxAlert"]["level"], "near")                     # R$ 17.600 em setembro de 2021
        self.assertIn("setembro de 2021", big["taxAlert"]["title"])
