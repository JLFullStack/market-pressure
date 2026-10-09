import unittest

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


if __name__ == "__main__":
    unittest.main()
