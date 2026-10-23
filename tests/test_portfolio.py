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
        self.assertEqual(partial["due"], [{"date": "2024-01-02", "price": 10, "hitDate": "2024-01-04", "dueDate": "2024-01-04"}])

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


class AssetChartTest(unittest.TestCase):
    """Card "Operações no gráfico" das Posições: alvos agrupados, marcadores e zoom."""
    CANDLES = day_candles({"2024-01-02": 10, "2024-01-03": 10.1, "2024-01-04": 10.2, "2024-01-05": 10.3,
                           "2024-01-06": 10.2, "2024-01-08": 10.4})
    RULE = {"key": "parcial", "target": 0.10, "fraction": 0.5, "timing": "alvo"}

    def test_close_targets_are_merged_and_sold_partials_are_left_out(self):
        def lot(price, qty, hit=None, realized=0):
            return {"date": "2024-01-02", "price": price, "quantity": qty, "remaining": qty - realized,
                    "realized": realized, "hitDate": hit}
        lots = [lot(10.0, 100), lot(10.1, 100),                      # alvos a 1% um do outro: juntos
                lot(12.0, 50),
                lot(10.05, 20, "2024-01-08"),                         # atingiu, parte ainda não vendida: à parte
                lot(8.0, 10, "2024-01-05", realized=5)]               # parcial já vendida: sem alvo
        levels = pf.target_levels(lots, self.RULE, 10.0)
        self.assertEqual([(len(level["lots"]), level["reached"]) for level in levels], [(2, False), (1, True), (1, False)])
        self.assertAlmostEqual(levels[0]["price"], (11.0 * 100 + 11.11 * 100) / 200)
        self.assertAlmostEqual(levels[2]["price"], 13.2)
        self.assertAlmostEqual(levels[0]["distance"], (levels[0]["price"] / 10 - 1) * 100)
        self.assertEqual(pf.target_levels(lots, {**self.RULE, "key": "reversao"}, 10.0), [])

    def test_chart_payload(self):
        txs = [tx("2024-01-02", "compra", 100, 10), tx("2024-01-02", "compra", 50, 10.1),
               tx("2024-01-07", "venda", 20, 10.3)]   # domingo: marcador no pregão seguinte
        out = pf.asset_chart(txs, "PETR4", self.CANDLES, "b3", self.RULE, now=self.CANDLES[-1]["date"] + 86400)
        self.assertEqual([(m["time"], m["kind"], m["text"]) for m in out["markers"]],
                         [("2024-01-02", "buy", "C×2"), ("2024-01-08", "sell", "V")])
        self.assertEqual((len(out["targets"]), out["nextTarget"]), (1, 0))
        self.assertIn("alvo em R$ 11,00", out["targets"][0]["detail"])
        self.assertEqual(out["range"], {"from": "2024-01-02", "to": "2024-01-08"})
        self.assertEqual((out["openLots"], out["transactions"]), (2, 3))
        self.assertIsNone(out["note"])
        reversal = pf.asset_chart(txs, "PETR4", self.CANDLES, "b3", {**self.RULE, "key": "reversao"})
        self.assertEqual(reversal["targets"], [])
        self.assertIn("reversão", reversal["note"])


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

    def test_rebalances_by_contribution_only(self):
        # Ações acima da meta (com um ativo abaixo do ideal), Internacionais abaixo: o aporte vai
        # todo para Internacionais, sem tratar o excesso de Ações como venda.
        classes = [{"name": "Ações", "target": 50}, {"name": "Internacionais", "target": 50}]
        item = lambda key, cls, value, target: {"key": key, "label": key, "cls": cls, "value": value, "kind": "quote",
                                                "market": "b3", "priceBRL": 10, "sell": False, "target": target,
                                                "fixedTarget": None, "setup": None}
        items = [item("BIG3", "Ações", 700, 25), item("SMALL3", "Ações", 100, 25), item("IVVB11", "Internacionais", 200, 50)]
        plan = pf.contribution_plan(classes, items, 1000, 200, "todos")
        rows = {r["key"]: r for r in plan["rows"]}
        self.assertEqual((rows["IVVB11"]["suggestion"], rows["SMALL3"]["suggestion"]), (200, 0))
        self.assertTrue(rows["BIG3"]["above"])
        self.assertEqual(plan["basis"], "falta")

    @staticmethod
    def _item(key, cls, value, target, price=10, **extra):
        return {"key": key, "label": key, "cls": cls, "value": value, "kind": "quote", "market": "b3", "priceBRL": price,
                "sell": False, "target": target, "fixedTarget": None, "setup": None, **extra}

    def test_cap_per_buy_leaves_the_rest_unallocated(self):
        classes = [{"name": "Ações", "target": 100}]
        items = [self._item("A3", "Ações", 500, 50), self._item("B3", "Ações", 500, 50)]
        opts = pf.contribution_options({"cap": "10", "reuse": False}, 1.5)
        plan = pf.contribution_plan(classes, items, 1000, 1000, "todos", options=opts)
        rows = {r["key"]: r for r in plan["rows"]}
        self.assertEqual((rows["A3"]["suggestion"], rows["B3"]["suggestion"]), (200, 200))  # 10% de 2.000
        self.assertEqual(plan["unallocated"], 600)
        # Com prioridade, o teto e o peso são 1,5x.
        items[0]["priority"] = True
        plan = pf.contribution_plan(classes, items, 1000, 1000, "todos", options=opts)
        self.assertEqual({r["key"]: r["suggestion"] for r in plan["rows"]}, {"A3": 300, "B3": 200})

    def test_max_assets_keeps_the_ones_that_get_more(self):
        classes = [{"name": "Ações", "target": 100}]
        items = [self._item("A3", "Ações", 100, 34), self._item("B3", "Ações", 300, 33), self._item("C3", "Ações", 320, 33)]
        opts = pf.contribution_options({"cap": "0", "maxAssets": "1", "reuse": False}, 1.5)
        plan = pf.contribution_plan(classes, items, 720, 280, "todos", options=opts)
        rows = {r["key"]: r for r in plan["rows"]}
        self.assertEqual(rows["A3"]["suggestion"], 280)
        self.assertFalse(rows["B3"]["eligible"])

    def test_reuses_the_leftover_in_whole_shares(self):
        classes = [{"name": "Ações", "target": 100}]
        items = [self._item("A3", "Ações", 0, 50, price=30), self._item("B3", "Ações", 0, 50, price=7)]
        plan = pf.contribution_plan(classes, items, 0, 100, "todos",
                                    options=pf.contribution_options({"cap": "0"}, 1.5))
        rows = {r["key"]: r for r in plan["rows"]}
        # 50 para cada: 1 cota de A3 (sobra 20) e 7 de B3 (sobra 1); a sobra compra mais cotas de B3.
        self.assertLessEqual(plan["effective"], 100)
        self.assertLess(100 - plan["effective"], 7)
        self.assertGreater(sum(r["extraShares"] for r in plan["rows"]), 0)

    def test_candidate_from_screener_is_eligible_in_any_mode(self):
        classes = [{"name": "Ações", "target": 50}, {"name": "Internacionais", "target": 50}]
        items = [self._item("PETR4", "Ações", 1000, 50),
                 self._item("AAPL34", "Internacionais", 0, 50, candidate=True,
                            setup={"ok": True, "ma": {"name": "MM20"}, "wait": []})]
        plan = pf.contribution_plan(classes, items, 1000, 500, "escolher", selected=[])
        rows = {r["key"]: r for r in plan["rows"]}
        self.assertEqual(rows["AAPL34"]["suggestion"], 500)
        self.assertEqual(rows["AAPL34"]["status"]["key"], "candidate")

    def test_path_until_classes_reach_target(self):
        rows = [{"cls": "Ações", "value": 1000, "target": 50}, {"cls": "Internacionais", "value": 0, "target": 50}]
        path = pf.contribution_path(rows, 500)
        self.assertEqual(path["reached"], 2)  # 1.000/500 no 1º mês, 1.000/1.000 no 2º
        self.assertLessEqual(path["months"][-1]["deviation"], pf.PLAN_TOLERANCE)

    def test_empty_class_below_target_is_reported(self):
        classes = [{"name": "Ações", "target": 60}, {"name": "Internacionais", "target": 40}]
        items = [{"key": "PETR4", "label": "PETR4", "cls": "Ações", "value": 1000, "kind": "quote", "market": "b3",
                  "priceBRL": 10, "sell": False, "target": 60, "fixedTarget": None, "setup": None}]
        plan = pf.contribution_plan(classes, items, 1000, 100, "todos")
        self.assertEqual(plan["rows"][0]["suggestion"], 100)
        self.assertEqual(plan["basis"], "meta")
        self.assertIn("Internacionais", plan["emptyClasses"][0])


class StrengthTextTest(unittest.TestCase):
    def test_partial_reason_tells_if_the_rally_is_still_strong(self):
        money = lambda v: f"R$ {v:.2f}"
        strong = pf._strength_text({"strong": True, "rsi": 72.0, "atrAboveMm20": 1.8, "mm20": 52.81}, money)
        self.assertIn("A alta ainda está forte", strong)
        self.assertIn("Venda parcial quando a alta perder força", strong)
        faded = pf._strength_text({"strong": False, "rsi": 55.0, "atrAboveMm20": 0.4, "mm20": 52.81}, money)
        self.assertIn("já perdeu força", faded)


class DividendMonthsTest(unittest.TestCase):
    def test_received_by_month_fills_the_last_twelve_months(self):
        divs = [{"exDate": "2026-10-05", "total": 10.0}, {"exDate": "2026-10-20", "total": 2.5},
                {"exDate": "2026-01-10", "total": 4.0}, {"exDate": "2025-10-31", "total": 99.0}]  # fora da janela
        months = pf.dividends_by_month(divs, "2026-10-22")
        self.assertEqual(len(months), 12)
        self.assertEqual(months[0]["month"], "2025-11")
        self.assertEqual(months[-1], {"month": "2026-10", "total": 12.5})
        self.assertEqual(next(m for m in months if m["month"] == "2026-01")["total"], 4.0)
        self.assertEqual(sum(m["total"] for m in months), 16.5)

    def test_receivables_by_payment_month_with_undefined_last(self):
        rows = [{"paymentDate": "2026-11-10", "total": 5.0, "confirmed": True},
                {"paymentDate": None, "total": 1.0, "confirmed": False},
                {"paymentDate": "2026-11-28", "total": 3.0, "confirmed": False},
                {"paymentDate": "2026-12-01", "total": 2.0, "confirmed": True}]
        self.assertEqual(pf.receivables_by_month(rows), [
            {"month": "2026-11", "total": 8.0, "confirmed": 5.0, "expected": 3.0},
            {"month": "2026-12", "total": 2.0, "confirmed": 2.0, "expected": 0},
            {"month": None, "total": 1.0, "confirmed": 0, "expected": 1.0}])


class AssetTargetRowTest(unittest.TestCase):
    def test_above_target_flag(self):
        from marketpressure import portfolio_service as ps
        row = lambda value, target: ps._asset_target_row(
            {"key": "X", "label": "X", "value": value, "target": target, "autoTarget": True}, 1000)
        self.assertTrue(row(300, 25)["above"])          # 30% com meta de 25%
        self.assertFalse(row(250, 25)["above"])         # exatamente na meta
        self.assertFalse(row(100, 25)["above"])
        self.assertAlmostEqual(row(300, 25)["pct"], 30)
        self.assertFalse(ps._asset_target_row({"key": "X", "label": "X", "value": 0, "target": 0, "autoTarget": True}, 0)["above"])


class PatrimonyBarsTest(unittest.TestCase):
    def test_monthly_bars_by_class(self):
        from tests.support import candles
        hist = {"candles": candles(400), "dividends": []}
        portfolio = {
            "classes": [{"name": "Ações", "target": 90}, {"name": "Renda fixa", "target": 10}],
            "assets": {"PETR4": {"class": "Ações", "market": "b3"}},
            "transactions": [tx("2021-03-01", "compra", 100, 90, fees=10), tx("2021-09-01", "venda", 40, 120)],
            "fixedIncome": [{"id": "f", "name": "Selic", "class": "Renda fixa", "entries": [
                {"id": "a", "date": "2021-02-01", "type": "aporte", "amount": 1000},
                {"id": "b", "date": "2021-06-01", "type": "valor_atual", "amount": 1100}]}],
        }
        a = pf.analyze_portfolio(portfolio, {"PETR4": hist, "BOVA11": hist}, [], as_of="2022-02-04",
                                 now=datetime(2022, 2, 4, 22, tzinfo=timezone.utc).timestamp())
        bars = pf.patrimony_bars(a["series"])
        self.assertEqual([g["key"] for g in bars["groups"]], ["", "Ações", "Renda fixa"])
        self.assertEqual(bars["months"][0], "2021-02")
        self.assertEqual(bars["periods"]["Tudo"], {"daily": False, "start": 0})
        self.assertEqual(bars["periods"]["1A"]["start"], len(bars["months"]) - 12)
        self.assertTrue(bars["periods"]["30D"]["daily"])
        # Últimos 30 dias corridos: uma barra por pregão, a última igual à do mês corrente.
        self.assertGreaterEqual(bars["days"][0], "2022-01-05")
        self.assertEqual(len(bars["days"]), len(bars["groups"][0]["dayBars"]))
        self.assertEqual(bars["groups"][0]["dayBars"][-1], bars["groups"][0]["bars"][-1])
        last = bars["groups"][0]["bars"][-1]
        self.assertAlmostEqual(last["value"], a["series"]["value"][-1])
        # Custo médio: 60 ações a (9000 + 10) / 100 + 1000 aplicados na renda fixa.
        self.assertAlmostEqual(last["invested"], 60 * 90.1 + 1000)
        self.assertAlmostEqual(last["base"] + last["up"] - last["down"], last["value"])
        # Aportes líquidos: 9010 da compra − 4800 da venda + 1000 da renda fixa.
        total = bars["groups"][0]
        self.assertAlmostEqual(sum(b["flow"] for b in total["bars"]), 5210)
        self.assertAlmostEqual(last["gainPct"], last["gain"] / last["invested"] * 100)
        everything = total["summary"]["Tudo"]
        self.assertIsNone(everything["startDate"])
        self.assertAlmostEqual(everything["flows"], 5210)
        self.assertAlmostEqual(everything["gain"], last["value"] - 5210)
        # A valorização do período se divide entre as classes.
        for label in ("30D", "1A", "Tudo"):
            self.assertAlmostEqual(sum(g["summary"][label]["gain"] for g in bars["groups"][1:]),
                                   total["summary"][label]["gain"])
        year = total["summary"]["1A"]
        self.assertEqual(year["startDate"], total["bars"][bars["periods"]["1A"]["start"] - 1]["date"])
        fi = bars["groups"][2]["bars"]
        self.assertEqual((fi[0]["value"], fi[0]["invested"]), (1000, 1000))
        self.assertEqual((fi[-1]["value"], fi[-1]["gain"]), (1100, 100))
