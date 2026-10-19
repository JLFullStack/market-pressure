import unittest
from datetime import datetime, timezone

from marketpressure import portfolio as pf


def tx(date, kind, qty, price, ticker="PETR4", fees=0, fx=1):
    return {"id": f"{date}-{kind}", "date": date, "ticker": ticker, "type": kind, "quantity": qty,
            "price": price, "fees": fees, "fx": fx}


class InputTest(unittest.TestCase):
    def test_parse_number_accepts_comma(self):
        self.assertEqual(pf.parse_number("1,5"), 1.5)
        self.assertEqual(pf.parse_number("", 0), 0)
        self.assertIsNone(pf.parse_number("abc"))
        self.assertIsNone(pf.parse_number(True))

    def test_normalize_ticker(self):
        self.assertEqual(pf.normalize_ticker(" petr4.sa "), "PETR4")

    def test_suggest_class(self):
        self.assertEqual(pf.suggest_class("PETR4", "b3"), "Ações")
        self.assertEqual(pf.suggest_class("HGLG11", "b3"), "Fundos imobiliários")
        self.assertEqual(pf.suggest_class("AAPL34", "b3"), "Internacionais")
        self.assertEqual(pf.suggest_class("VOO", "us"), "Internacionais")
        self.assertEqual(pf.suggest_class("BOVA11", "b3", kind="etfs"), "ETFs Brasil")
        self.assertEqual(pf.suggest_class("IVVB11", "b3", kind="etfs", international=True), "Internacionais")


class MigrationTest(unittest.TestCase):
    def test_legacy_targets_become_classes_and_migration_is_idempotent(self):
        legacy = {"version": 1, "portfolios": [{
            "id": "p", "name": "Antiga", "targets": {"Ações": 60, "Renda fixa": 40},
            "assets": {"PETR4": {"class": "Ações"}}, "transactions": [],
            "fixedIncome": [{"id": "f", "name": "Tesouro", "entries": []}]}]}
        doc = pf.normalize_doc(legacy)
        p = doc["portfolios"][0]
        self.assertEqual(doc["version"], 2)
        self.assertEqual(p["classes"], [{"name": n, "target": {"Ações": 60, "Renda fixa": 40}.get(n, 0)} for n in pf.CLASS_NAMES])
        self.assertNotIn("targets", p)
        self.assertEqual(p["fixedIncome"][0]["class"], "Renda fixa")
        self.assertEqual(pf.normalize_doc(doc), doc)

    def test_free_classes_become_the_fixed_ones(self):
        old = {"id": "p", "name": "ARCA", "transactions": [], "fixedIncome": [],
               "classes": [{"name": "Ações", "target": 60}, {"name": "ETFs", "target": 25},
                           {"name": "Ativos Internacionais", "target": 10}, {"name": "Minha", "target": 5}],
               "assets": {"PETR4": {"class": "Ações"}, "IVVB11": {"class": "Ativos Internacionais"},
                          "AAPL34": {"class": "Minha"}, "VOO": {"class": "Minha", "market": "us"}}}
        p = pf.normalize_portfolio(old)
        self.assertEqual([c["name"] for c in p["classes"]], pf.CLASS_NAMES)
        targets = {c["name"]: c["target"] for c in p["classes"]}
        self.assertEqual((targets["Ações"], targets["ETFs Brasil"], targets["Internacionais"]), (60, 25, 15))
        self.assertEqual({t: a["class"] for t, a in p["assets"].items()},
                         {"PETR4": "Ações", "IVVB11": "Internacionais", "AAPL34": "Internacionais", "VOO": "Internacionais"})
        self.assertEqual(pf.normalize_portfolio(p), p)


class PositionsTest(unittest.TestCase):
    def test_sell_uses_average_price(self):
        positions = pf.build_positions([tx("2024-01-02", "compra", 100, 10), tx("2024-01-03", "compra", 100, 20),
                                        tx("2024-01-04", "venda", 50, 30)])
        p = positions["PETR4"]
        self.assertEqual(p["quantity"], 150)
        self.assertAlmostEqual(p["avgPrice"], 15)
        self.assertAlmostEqual(p["realized"], 50 * 30 - 50 * 15)

    def test_sale_larger_than_holding_is_rejected(self):
        error = pf.check_sequence([tx("2024-01-02", "compra", 10, 10), tx("2024-01-03", "venda", 11, 10)])
        self.assertIn("excede a quantidade possuída", error)
        # No mesmo dia, a compra vem antes da venda.
        self.assertIsNone(pf.check_sequence([tx("2024-01-02", "venda", 10, 10), tx("2024-01-02", "compra", 10, 10)]))

    def test_foreign_flow_is_converted_with_fx(self):
        self.assertAlmostEqual(pf.tx_flow(tx("2024-01-02", "compra", 2, 100, "VOO", fees=1, fx=5)), (200 + 1) * 5)


class ContributionTest(unittest.TestCase):
    def test_split_cents_sums_exactly(self):
        parts = pf.split_cents(100, [1, 1, 1])
        self.assertEqual(parts, [33.34, 33.33, 33.33])
        self.assertEqual(round(sum(parts), 2), 100)
        self.assertEqual(pf.split_cents(10, [0, 0]), [5, 5])

    def test_batch_totals(self):
        rows = [{"kind": "quote", "include": True, "market": "b3", "qty": "10", "price": "2,5", "fees": ""},
                {"kind": "fixed", "include": True, "amount": "100"},
                {"kind": "quote", "include": False, "market": "b3", "qty": "", "price": "1"}]
        result = pf.batch_totals(rows, 200)
        self.assertEqual(result["rows"], [25, 100, None])
        self.assertEqual((result["total"], result["complete"], result["included"], result["diff"]), (125, True, 2, 75))


def day_candles(highs):
    """Candles diários a partir de {AAAA-MM-DD: máxima} (meio-dia em Brasília)."""
    out = []
    for iso, high in sorted(highs.items()):
        ts = int(datetime.fromisoformat(f"{iso}T15:00:00").replace(tzinfo=timezone.utc).timestamp())
        out.append({"date": ts, "o": high, "h": high, "l": high, "c": high, "v": 1, "adj": high})
    return out


class PartialExitTest(unittest.TestCase):
    CANDLES = day_candles({"2024-01-02": 12, "2024-01-03": 10.5, "2024-01-04": 11.2, "2024-01-05": 11,
                           "2024-01-08": 19, "2024-01-09": 20.5})

    def test_target_is_counted_only_after_the_purchase_day(self):
        lots = pf.open_lots([tx("2024-01-02", "compra", 100, 10)], "PETR4", self.CANDLES)
        self.assertEqual(lots[0]["hitDate"], "2024-01-04")  # a máxima de 12 no dia da compra não conta
        partial = pf.partial_to_sell(lots, "b3")
        self.assertEqual({k: partial[k] for k in ("quantity", "lots", "target", "fraction", "rounded")},
                         {"quantity": 50, "lots": 1, "target": 10.0, "fraction": 50.0, "rounded": False})
        self.assertEqual(partial["due"], [{"date": "2024-01-02", "price": 10, "hitDate": "2024-01-04"}])

    def test_sale_after_the_target_settles_the_partial(self):
        txs = [tx("2024-01-02", "compra", 100, 10), tx("2024-01-05", "venda", 50, 11)]
        lots = pf.open_lots(txs, "PETR4", self.CANDLES)
        self.assertEqual((lots[0]["remaining"], lots[0]["pending"]), (50, 0))
        self.assertIsNone(pf.partial_to_sell(lots, "b3"))

    def test_sales_settle_pending_partials_first_then_oldest_lots(self):
        txs = [tx("2024-01-02", "compra", 100, 10), tx("2024-01-03", "compra", 100, 20),
               tx("2024-01-05", "venda", 30, 11)]
        lots = pf.open_lots(txs, "PETR4", self.CANDLES)
        self.assertEqual([(lot["remaining"], lot["pending"]) for lot in lots], [(70, 20), (100, 0)])
        txs[-1] = tx("2024-01-05", "venda", 150, 11)
        lots = pf.open_lots(txs, "PETR4", self.CANDLES)
        self.assertEqual([(lot["price"], lot["remaining"], lot["pending"]) for lot in lots], [(20, 50, 0)])

    def test_whole_shares_on_b3_and_signal(self):
        lots = pf.open_lots([tx("2024-01-02", "compra", 3, 10)], "PETR4", self.CANDLES)
        partial = pf.partial_to_sell(lots, "b3")
        self.assertEqual((partial["quantity"], partial["rounded"]), (1, True))
        self.assertEqual(pf.partial_to_sell(lots, "us")["quantity"], 1.5)
        day = self.CANDLES[-1:]
        ind = {"candles": day, "closes": [10], "mm20": [9], "mm50": [9], "mm200": [8]}
        signal = pf.position_signal(ind, 0, partial, lots)
        self.assertEqual((signal["key"], signal["label"]), ("partial", "VENDER 50%"))
        self.assertIn("atingiu +10% em 04/01/2024", signal["reason"])
        self.assertIn("venda 1 cota", signal["reason"])
        hold = pf.position_signal(ind, 0, None, [])
        self.assertEqual((hold["key"], hold["label"]), ("hold", "MANTER"))
        self.assertTrue(hold["reason"])
        sell = {"candles": day, "closes": [7], "mm20": [9], "mm50": [8], "mm200": [8.5]}
        self.assertEqual(pf.position_signal(sell, 0, partial, lots)["label"], "VENDER")  # a reversão vem antes
        weak = {"candles": day, "closes": [8], "mm20": [9], "mm50": [8.6], "mm200": [8.5]}
        self.assertEqual(pf.position_signal(weak, 0, None, lots)["label"], "MANTER")

    def test_buy_only_in_a_new_pullback(self):
        day = self.CANDLES[-1:]
        ind = {"candles": day, "closes": [10], "mm20": [9.9], "mm50": [9], "mm200": [8]}
        setup = {"ok": True, "ma": {"name": "MM20", "dist": 0.01}}
        far = [{"date": "2023-12-01", "price": 12, "hitDate": None, "pending": 0}]
        buy = pf.position_signal(ind, 0, None, far, setup=setup)
        self.assertEqual((buy["key"], buy["label"]), ("buy", "COMPRAR"))
        near = [{"date": "2023-12-01", "price": 10.2, "hitDate": None, "pending": 0}]
        hold = pf.position_signal(ind, 0, None, near, setup=setup)
        self.assertEqual(hold["label"], "MANTER")
        self.assertIn("mesma região", hold["reason"])

    def test_one_share_keeps_holding_with_the_reason(self):
        txs = [tx("2024-01-02", "compra", 1, 10)]
        self.assertEqual(pf.partial_to_sell(pf.open_lots(txs, "PETR4", self.CANDLES), "b3")["quantity"], 0)
        day = self.CANDLES[-1:]
        ind = {"candles": day, "closes": [20.5], "mm20": [9], "mm50": [9], "mm200": [8]}
        lots = pf.open_lots(txs, "PETR4", self.CANDLES)
        signal = pf.position_signal(ind, 0, pf.partial_to_sell(lots, "b3"), lots)
        self.assertEqual((signal["key"], signal["label"]), ("hold", "MANTER"))
        self.assertIn("não chega a 1 cota inteira", signal["reason"])
        two = [tx("2024-01-02", "compra", 2, 10)]
        lots = pf.open_lots(two, "PETR4", self.CANDLES)
        self.assertEqual(pf.position_signal(ind, 0, pf.partial_to_sell(lots, "b3"), lots)["label"], "VENDER 50%")

    def test_strategy_changes_the_partial(self):
        txs = [tx("2024-01-02", "compra", 10, 10)]
        reversal = {"key": "reversao", "target": 0.10, "fraction": 0.5}
        self.assertIsNone(pf.partial_to_sell(pf.open_lots(txs, "PETR4", self.CANDLES, 0.10, 0), "b3", reversal))
        far = pf.open_lots(txs, "PETR4", self.CANDLES, 1.5, 0.3)
        self.assertIsNone(pf.partial_to_sell(far, "b3"))
        rule = {"key": "parcial", "target": 0.10, "fraction": 0.3}
        hit = pf.partial_to_sell(pf.open_lots(txs, "PETR4", self.CANDLES, 0.10, 0.3), "b3", rule)
        self.assertEqual((hit["quantity"], hit["fraction"]), (3, 30.0))

if __name__ == "__main__":
    unittest.main()


class EntryFilterAporteTest(unittest.TestCase):
    def test_pullback_mode_skips_weak_trend(self):
        classes = [{"name": "Ações", "target": 100}]
        item = lambda key, wait: {"key": key, "label": key, "cls": "Ações", "value": 1000, "kind": "quote", "market": "b3",
                                  "priceBRL": 10, "sell": False, "target": 50, "fixedTarget": None,
                                  "setup": {"enough": True, "ok": True, "ma": {"name": "MM20"}, "wait": wait}}
        plan = pf.contribution_plan(classes, [item("FIRM3", []), item("WEAK3", ["o preço caiu 5,0% em 12 meses"])],
                                    2000, 1000, "pullback")
        rows = {r["key"]: r for r in plan["rows"]}
        self.assertTrue(rows["FIRM3"]["eligible"])
        self.assertFalse(rows["WEAK3"]["eligible"])
        self.assertIn("tendência fraca", rows["WEAK3"]["excluded"])
