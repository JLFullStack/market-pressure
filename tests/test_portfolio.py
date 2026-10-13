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
        classes = [dict(c) for c in pf.DEFAULT_CLASSES]
        self.assertEqual(pf.suggest_class("PETR4", "b3", classes), "Ações")
        self.assertEqual(pf.suggest_class("HGLG11", "b3", classes), "FIIs")
        self.assertEqual(pf.suggest_class("VOO", "us", classes), "Ativos Internacionais")


class MigrationTest(unittest.TestCase):
    def test_legacy_targets_become_classes_and_migration_is_idempotent(self):
        legacy = {"version": 1, "portfolios": [{
            "id": "p", "name": "Antiga", "targets": {"Ações": 60, "Renda fixa": 40},
            "assets": {"PETR4": {"class": "Ações"}}, "transactions": [],
            "fixedIncome": [{"id": "f", "name": "Tesouro", "entries": []}]}]}
        doc = pf.normalize_doc(legacy)
        p = doc["portfolios"][0]
        self.assertEqual(doc["version"], 2)
        self.assertEqual(p["classes"], [{"name": "Ações", "target": 60}, {"name": "Renda fixa", "target": 40}])
        self.assertNotIn("targets", p)
        self.assertEqual(p["fixedIncome"][0]["class"], "Renda fixa")
        self.assertEqual(pf.normalize_doc(doc), doc)


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
        self.assertEqual(pf.partial_to_sell(lots, "b3"), {"quantity": 50, "lots": 1, "target": 10.0, "fraction": 50.0})

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

    def test_whole_shares_on_b3_and_status(self):
        lots = pf.open_lots([tx("2024-01-02", "compra", 3, 10)], "PETR4", self.CANDLES)
        partial = pf.partial_to_sell(lots, "b3")
        self.assertEqual(partial["quantity"], 1)
        self.assertEqual(pf.partial_to_sell(lots, "us")["quantity"], 1.5)
        ind = {"closes": [10], "mm20": [9], "mm50": [9], "mm200": [8]}
        self.assertEqual(pf.position_status(ind, 0, partial), "Realizar 50%")
        self.assertEqual(pf.position_status(ind, 0, None), "Manter")
        sell = {"closes": [7], "mm20": [9], "mm50": [8], "mm200": [8.5]}
        self.assertEqual(pf.position_status(sell, 0, partial), "Vender")  # a regra B vem antes


if __name__ == "__main__":
    unittest.main()
