"""Sub-aba Impostos: apuração mensal, DARF, prejuízos, exterior, declaração e alerta de vencimento."""

import unittest

from marketpressure.comum import database as db
from marketpressure.carteira import portfolio as pf, tax_report as tr

from .support import DatabaseTestCase

CATS = {t: {"category": c, "name": "", "assumed": False} for t, c in
        {"PETR4": "acao", "VALE3": "acao", "IVVB11": "etf", "AAPL34": "bdr", "HGLG11": "fii", "JURO11": "infra", "VOO": "exterior"}.items()}


def tx(ticker, kind, date, quantity, price, fees=0, fx=1):
    return {"id": f"{ticker}{date}{kind}", "ticker": ticker, "type": kind, "date": date, "quantity": quantity,
            "price": price, "fees": fees, "fx": fx}


def ledger(txs, opening=None, payments=None, today="2026-06-15"):
    return tr.ledger(tr.sales_of(txs, CATS), opening, payments, today)


class SalesTest(unittest.TestCase):
    def test_gain_uses_average_price_and_fees(self):
        sales = tr.sales_of([tx("PETR4", "compra", "2026-01-02", 100, 10, fees=10), tx("PETR4", "compra", "2026-01-05", 100, 20, fees=10),
                             tx("PETR4", "venda", "2026-02-02", 100, 30, fees=5)], CATS)
        self.assertEqual(sales[0]["gross"], 3000)
        self.assertEqual(sales[0]["cost"], 1510)
        self.assertEqual(sales[0]["gain"], 1485)
        self.assertFalse(sales[0]["sameDayBuy"])

    def test_foreign_converts_by_each_fx(self):
        sale = tr.sales_of([tx("VOO", "compra", "2026-01-02", 1, 100, fx=5), tx("VOO", "venda", "2026-02-02", 1, 110, fx=6)], CATS)[0]
        self.assertEqual(sale["gain"], 660 - 500)

    def test_same_day_buy_is_flagged(self):
        sale = tr.sales_of([tx("PETR4", "compra", "2026-01-02", 10, 10), tx("PETR4", "venda", "2026-01-02", 10, 11)], CATS)[0]
        self.assertTrue(sale["sameDayBuy"])


class LedgerTest(unittest.TestCase):
    def test_stocks_up_to_limit_are_exempt(self):
        m = ledger([tx("PETR4", "compra", "2026-01-02", 1000, 10), tx("PETR4", "venda", "2026-02-02", 1000, 20)])["months"][0]
        self.assertEqual((m["stockSold"], m["exemptGain"], m["tax"], m["darf"], m["status"]), (20000, 10000, 0, 0, "none"))

    def test_above_limit_taxes_all_the_stock_gain(self):
        m = ledger([tx("PETR4", "compra", "2026-01-02", 1001, 10), tx("PETR4", "venda", "2026-02-02", 1001, 20)])["months"][0]
        self.assertEqual(m["exemptGain"], 0)
        self.assertEqual(m["tax"], 10010 * 0.15)
        self.assertAlmostEqual(m["sourceTax"], 20020 * 0.00005)
        self.assertAlmostEqual(m["darf"], 10010 * 0.15 - 20020 * 0.00005)

    def test_etf_has_no_exemption(self):
        m = ledger([tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-02-02", 10, 350)])["months"][0]
        self.assertEqual((m["exemptGain"], m["tax"]), (0, 75))
        self.assertEqual(m["darf"], 75)   # R$ 3.500 vendidos: IRRF de R$ 0,18, dispensado

    def test_exempt_gain_does_not_consume_the_loss(self):
        d = ledger([tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-02-02", 10, 200),
                    tx("PETR4", "compra", "2026-01-02", 100, 10), tx("PETR4", "venda", "2026-03-02", 100, 20)])
        self.assertEqual(d["months"][1]["exemptGain"], 1000)
        self.assertEqual(d["lossCommon"], 1000)

    def test_stock_loss_in_exempt_month_accumulates_and_offsets_etf(self):
        d = ledger([tx("PETR4", "compra", "2026-01-02", 100, 20), tx("PETR4", "venda", "2026-02-02", 100, 10),
                    tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-03-02", 10, 450)])
        self.assertEqual(d["months"][0]["lossCommon"], 1000)
        march = d["months"][1]
        self.assertEqual((march["lossUsedCommon"], march["baseCommon"], march["taxCommon"]), (1000, 500, 75))
        self.assertEqual(d["lossCommon"], 0)

    def test_fund_loss_is_separate_and_rate_is_20(self):
        d = ledger([tx("HGLG11", "compra", "2026-01-02", 10, 100), tx("HGLG11", "venda", "2026-02-02", 10, 50),
                    tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-03-02", 10, 400),
                    tx("HGLG11", "compra", "2026-03-03", 10, 50), tx("HGLG11", "venda", "2026-04-02", 10, 150)])
        self.assertEqual(d["months"][1]["taxCommon"], 150)   # o prejuízo do fundo não abate o ETF
        april = d["months"][2]
        self.assertEqual((april["lossUsedFunds"], april["baseFunds"], april["taxFunds"]), (500, 500, 100))

    def test_infra_is_exempt(self):
        m = ledger([tx("JURO11", "compra", "2026-01-02", 10, 100), tx("JURO11", "venda", "2026-02-02", 10, 200)])["months"][0]
        self.assertEqual((m["infraGain"], m["tax"], m["darf"]), (1000, 0, 0))

    def test_opening_loss_is_used(self):
        m = ledger([tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-02-02", 10, 350)],
                   opening={"common": 200})["months"][0]
        self.assertEqual((m["lossUsedCommon"], m["baseCommon"]), (200, 300))

    def test_below_ten_reais_carries_to_next_month(self):
        d = ledger([tx("IVVB11", "compra", "2026-01-02", 10, 100), tx("IVVB11", "venda", "2026-02-02", 5, 110),
                    tx("IVVB11", "venda", "2026-03-02", 5, 110)])
        self.assertEqual((d["months"][0]["darf"], d["months"][0]["carryOut"]), (0, 7.5))
        self.assertEqual((d["months"][1]["carriedIn"], d["months"][1]["darf"]), (7.5, 15))

    def test_unused_source_tax_offsets_a_later_month(self):
        d = ledger([tx("PETR4", "compra", "2026-01-02", 3000, 10), tx("PETR4", "venda", "2026-02-02", 3000, 10),
                    tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-03-02", 10, 350)])
        self.assertAlmostEqual(d["months"][0]["sourceTax"], 1.5)
        self.assertEqual(d["months"][0]["darf"], 0)
        self.assertAlmostEqual(d["months"][1]["creditUsed"], 1.5)
        self.assertAlmostEqual(d["months"][1]["darf"], 73.5)

    def test_foreign_stays_out_of_the_month(self):
        self.assertEqual(ledger([tx("VOO", "compra", "2026-01-02", 1, 100, fx=5), tx("VOO", "venda", "2026-02-02", 1, 200, fx=5)])["months"], [])

    def test_status_and_late_fine(self):
        txs = [tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-02-02", 10, 350)]
        self.assertEqual(ledger(txs, today="2026-03-31")["months"][0]["status"], "pending")
        late = ledger(txs, today="2026-04-10")["months"][0]
        self.assertEqual((late["dueDate"], late["status"], late["lateDays"]), ("2026-03-31", "overdue", 10))
        self.assertAlmostEqual(late["lateFine"], 75 * 0.033)
        self.assertAlmostEqual(ledger(txs, today="2027-04-10")["months"][0]["lateFine"], 75 * 0.20)
        paid = ledger(txs, payments={"2026-02": {"amount": 75, "paidOn": "2026-03-10"}}, today="2026-04-10")["months"][0]
        self.assertEqual((paid["status"], paid["paidDiff"]), ("paid", False))
        changed = ledger(txs, payments={"2026-02": {"amount": 50, "paidOn": "2026-03-10"}})["months"][0]
        self.assertTrue(changed["paidDiff"])

    def test_due_date_is_last_weekday_of_next_month(self):
        self.assertEqual(tr.due_date("2026-02"), "2026-03-31")
        self.assertEqual(tr.due_date("2026-04"), "2026-05-29")   # 31/05/2026 é domingo
        self.assertEqual(tr.due_date("2026-12"), "2027-01-29")   # 31/01/2027 é domingo


class GuideTest(unittest.TestCase):
    TXS = [tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-02-02", 10, 350)]

    def steps(self, today):
        return tr.darf_guide(ledger(self.TXS, today=today)["months"][0], today)

    def test_uses_the_values_of_the_month(self):
        steps = self.steps("2026-03-02")
        fields = {f["label"]: f for s in steps for f in s.get("fields", [])}
        self.assertEqual(fields["Período de apuração"]["copy"], "02/2026")
        self.assertEqual(fields["Valor principal"]["copy"], "75,00")
        self.assertEqual(fields["Código ou nome da receita"]["copy"], "6015")
        self.assertTrue(any(s.get("link") for s in steps))
        self.assertTrue(any("31/03/2026" in s["text"] for s in steps))

    def test_late_explains_the_fine(self):
        self.assertTrue(any(s.get("warn") and "multa" in s["warn"] for s in self.steps("2026-04-10")))

    def test_general_without_a_month(self):
        fields = [f for s in tr.darf_guide(None, "2026-04-10") for f in s.get("fields", [])]
        self.assertEqual([f.get("copy") for f in fields], ["6015", None, None])


class ForeignTest(unittest.TestCase):
    def test_loss_carries_to_next_year(self):
        sales = tr.sales_of([tx("VOO", "compra", "2025-01-02", 2, 100, fx=5), tx("VOO", "venda", "2025-02-02", 1, 50, fx=5),
                             tx("VOO", "venda", "2026-02-02", 1, 300, fx=5)], CATS)
        y = tr.foreign_years(sales)
        self.assertEqual(y["2025"]["lossOut"], 250)
        self.assertEqual((y["2026"]["lossUsed"], y["2026"]["base"], y["2026"]["tax"]), (250, 750, 112.5))


class DueAlertTest(unittest.TestCase):
    TXS = [tx("IVVB11", "compra", "2026-01-02", 10, 300), tx("IVVB11", "venda", "2026-02-02", 10, 350)]

    def alert(self, today):
        return tr.due_alert_from(ledger(self.TXS, today=today)["months"], today)

    def test_levels_follow_the_due_date(self):
        self.assertEqual(self.alert("2026-03-02")["level"], "info")
        self.assertEqual(self.alert("2026-03-24")["level"], "near")
        today = self.alert("2026-03-31")
        self.assertEqual(today["level"], "near")
        self.assertIn("vence hoje", today["title"])
        late = self.alert("2026-04-02")
        self.assertEqual(late["level"], "over")
        self.assertIn("há 2 dias", late["title"])

    def test_none_when_paid_or_nothing_due(self):
        months = ledger(self.TXS, payments={"2026-02": {"amount": 75, "paidOn": "2026-03-10"}})["months"]
        self.assertIsNone(tr.due_alert_from(months, "2026-04-02"))
        self.assertIsNone(tr.due_alert_from([], "2026-04-02"))


class PersistenceTest(DatabaseTestCase):
    def portfolio(self):
        return {"id": "c1", "name": "Teste", "assets": {"IVVB11": {"class": "ETFs Brasil", "market": "b3"}},
                "classes": [], "fixedIncome": [], "transactions": DueAlertTest.TXS}

    def test_mark_and_unmark_paid(self):
        p = self.portfolio()
        self.assertIsNotNone(tr.due_alert(p))
        tr.mark_paid(p, {"month": "2026-02"})
        self.assertIsNone(tr.due_alert(p))
        self.assertEqual(tr.load_payments("c1")["2026-02"]["amount"], 75)
        tr.mark_paid(p, {"month": "2026-02", "paid": False})
        self.assertEqual(tr.load_payments("c1"), {})
        with self.assertRaises(ValueError):
            tr.mark_paid(p, {"month": "2026-05"})

    def test_opening_loss_changes_the_ledger(self):
        p = self.portfolio()
        tr.save_opening(p, {"common": "1000,50", "funds": ""})
        self.assertEqual(tr.load_opening("c1"), {"common": 1000.5, "funds": 0})
        self.assertIsNone(tr.due_alert(p))   # o prejuízo cobre o lucro de R$ 500
        with self.assertRaises(ValueError):
            tr.save_opening(p, {"common": "-1"})

    def test_due_alert_respects_the_warning_switch(self):
        self.assertIsNone(tr.due_alert(self.portfolio(), {"enabled": False, "warnAt": 15000}))

    def test_declaration(self):
        p = {**self.portfolio(), "transactions": [tx("IVVB11", "compra", "2025-03-02", 10, 300), tx("PETR4", "compra", "2025-03-02", 10, 30),
                                                  tx("PETR4", "venda", "2026-02-02", 10, 40), tx("VOO", "compra", "2026-03-02", 1, 100, fx=5)],
             "assets": {"VOO": {"class": "Internacionais", "market": "us"}},
             "fixedIncome": [{"id": "f", "name": "Tesouro", "entries": [{"id": "e", "date": "2025-01-10", "type": "aporte", "amount": 1000}]}]}
        data = tr.payload(p, "2026", {"dividends": [{"ticker": "PETR4", "exDate": "2026-01-10", "total": 25}]})
        decl = data["declaration"]
        rows = {r["ticker"]: r for r in decl["holdings"]}
        self.assertEqual((rows["IVVB11"]["costBefore"], rows["IVVB11"]["costAfter"]), (3000, 3000))
        self.assertEqual((rows["PETR4"]["quantityBefore"], rows["PETR4"]["quantityAfter"]), (10, 0))
        self.assertIn("posição zerada", rows["PETR4"]["text"])
        self.assertEqual(rows["VOO"]["costAfter"], 500)
        self.assertIn("exterior", rows["VOO"]["text"])
        self.assertEqual(decl["exemptGain"], 100)
        self.assertEqual(decl["income"][0]["total"], 25)
        self.assertEqual(decl["fixedIncome"], [{"name": "Tesouro", "before": 1000, "after": 1000}])
        with self.assertRaises(ValueError):
            tr.payload(p, "1999")

    def test_attention_flags(self):
        p = {**self.portfolio(), "transactions": [tx("ZZZZ11", "compra", "2026-02-02", 10, 300), tx("ZZZZ11", "venda", "2026-02-02", 10, 350)],
             "assets": {"ZZZZ11": {"class": "ETFs Brasil", "market": "b3"}}}
        titles = [a["title"] for a in tr.payload(p, portfolio_count=2)["attention"]]
        self.assertIn("Possível day trade", titles)
        self.assertIn("Tipo de ativo deduzido da classe", titles)
        self.assertIn("A Receita apura por CPF, não por carteira", titles)
        self.assertIn("Proventos indisponíveis agora", titles)

    def test_jcp_rate_of_2026(self):
        self.assertEqual(pf.JCP_TAX, 0.175)
        self.assertIn("tax_payments", db.SCHEMAS)


if __name__ == "__main__":
    unittest.main()
