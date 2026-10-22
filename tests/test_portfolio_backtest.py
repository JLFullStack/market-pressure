"""Backtest de carteira (portfolio_backtest): um único caixa para vários ativos."""

from marketpressure import portfolio_backtest as pb
from tests.support import DatabaseTestCase, raw_history


def params(**kw):
    base = {"universe": "lista", "tickers": "PETR4, VALE3, ITUB4", "initial": "10000", "cdi": False, "saida": "manual"}
    return pb.parse_params({**base, **kw})


class PortfolioBacktestTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        pb._signals_cache.clear()
        # Ativos com ondas de períodos diferentes, para os sinais não caírem todos no mesmo dia.
        periods = {"PETR4": 40, "VALE3": 33, "ITUB4": 47, "BOVA11": 40}
        from marketpressure import market_data
        market_data.raw_history = lambda t, market="b3": raw_history(t, period=periods.get(t, 40))

    def test_cash_is_shared_and_profit_adds_up(self):
        result = pb.run(params())
        self.assertEqual([s["key"] for s in result["strategies"]], ["rule", "onlyB", "bova", "ivvb"])
        rule = result["strategies"][0]
        m = rule["metrics"]
        self.assertAlmostEqual(m["profit"], m["final"] - m["contributed"])
        self.assertEqual(m["contributed"], 10000)
        # Sem CDI e sem aportes: patrimônio final = capital inicial + lucro de cada compra.
        self.assertAlmostEqual(m["final"], 10000 + sum(t["profit"] for t in result["trades"]), places=4)
        self.assertGreater(len(result["trades"]), 3)
        self.assertEqual({t["ticker"] for t in result["trades"]} <= {"PETR4", "VALE3", "ITUB4"}, True)
        self.assertEqual(len(rule["curve"]), result["sessions"] + 1)
        self.assertAlmostEqual(rule["curve"][-1]["value"], m["profit"])
        self.assertIn("a mais que", result["compare"])

    def test_monthly_contributions_and_benchmark_get_the_same_flows(self):
        result = pb.run(params(monthly="500"))
        months = len({p["time"][:7] for p in result["strategies"][0]["curve"]})
        for s in result["strategies"]:
            self.assertEqual(s["metrics"]["contributed"], 10000 + 500 * (months - 1), s["key"])

    def test_max_assets_limits_distinct_tickers_held_together(self):
        trades = pb.run(params(maxAssets="1", fraction="0"))["trades"]
        end = lambda t: (t["exit"] or {"date": "9999-12-31"})["date"]
        for a in trades:
            for b in trades:
                if a["ticker"] != b["ticker"]:
                    self.assertFalse(a["entry"]["date"] < end(b) and b["entry"]["date"] < end(a), (a["ticker"], b["ticker"]))

    def test_only_rule_b_runs_once(self):
        result = pb.run(params(fraction="0"))
        self.assertEqual([s["key"] for s in result["strategies"]], ["rule", "bova", "ivvb"])
        self.assertIsNone(result["compare"])

    def test_illiquid_assets_never_enter_the_ranking(self):
        from marketpressure import market_data
        normal = market_data.raw_history

        def thin(t, market="b3"):
            hist = normal(t, market)
            if t == "VALE3":  # R$ 100 × 100 ações = R$ 10 mil por dia, bem abaixo do piso de R$ 5 milhões
                hist["candles"] = [{**c, "volume": 100} for c in hist["candles"]]
            return hist
        market_data.raw_history = thin
        pb._signals_cache.clear()
        result = pb.run(params())
        self.assertNotIn("VALE3", {t["ticker"] for t in result["trades"]})
        self.assertEqual((result["universe"]["candidates"], result["universe"]["ranked"], result["universe"]["illiquid"]), (3, 2, 1))

    def test_daily_ranking_keeps_the_top_of_each_kind(self):
        candidates = [("A", 50, "acoes"), ("B", 90, "acoes"), ("C", 70, "acoes"), ("F", 60, "fundos")]
        self.assertEqual(pb.daily_ranking(candidates, 2), [("B", 90, 1), ("C", 70, 2), ("F", 60, 1)])

    def test_top_one_buys_only_the_best_of_the_day(self):
        result = pb.run(params(topN="1", fraction="0"))
        self.assertTrue(all(t["rank"] == 1 for t in result["trades"]))
        self.assertEqual(set(result["markers"]), {t["ticker"] for t in result["trades"]})
        self.assertEqual(sum(a["trades"] for a in result["assets"]), len(result["trades"]))
        ranking = result["profitRanking"]  # gráfico "Lucro por ativo": do maior lucro ao maior prejuízo
        self.assertEqual([r["profit"] for r in ranking], sorted((r["profit"] for r in ranking), reverse=True))
        self.assertTrue(all(r["profit"] != 0 for r in ranking))

    def test_invalid_parameters(self):
        for kw in ({"tickers": ""}, {"position": "0"}, {"universe": "x"}, {"from": "2021-12-01", "to": "2021-11-01"},
                   {"from": "ontem"}, {"cost": "9"}):
            with self.assertRaises(ValueError, msg=kw):
                params(**kw)


class RobustnessTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        pb._signals_cache.clear()
        periods = {"PETR4": 40, "VALE3": 33, "ITUB4": 47, "BOVA11": 40}
        from marketpressure import market_data
        market_data.raw_history = lambda t, market="b3": raw_history(t, n=1300, period=periods.get(t, 40))

    def test_one_row_per_start_year_until_the_same_end(self):
        result = pb.robustness(params(**{"from": "2099-01-01"}))   # o "De" é ignorado
        rows = result["rows"]
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(result["periods"], len(rows))
        self.assertEqual({r["to"] for r in rows}, {result["end"]})
        self.assertEqual([r["from"][5:7] for r in rows[1:]], ["01"] * (len(rows) - 1))
        self.assertEqual(len({r["from"][:4] for r in rows}), len(rows))
        # O primeiro início é o período inteiro: o mesmo lucro do backtest normal.
        full = pb.run(params())
        self.assertAlmostEqual(rows[0]["rule"]["profit"], full["strategies"][0]["metrics"]["profit"], places=4)
        self.assertAlmostEqual(rows[0]["onlyB"]["profit"], full["strategies"][1]["metrics"]["profit"], places=4)
        for r in rows:
            self.assertEqual(r["beats"]["bova"], r["rule"]["profit"] > r["bova"]["profit"])
            self.assertEqual(r["rule"]["contributed"], r["bova"]["contributed"])
        self.assertEqual(result["wins"]["bova"], sum(r["beats"]["bova"] for r in rows))
        self.assertTrue(result["verdict"])
        self.assertIn(f"de {len(rows)} inícios", result["summary"][0])

    def test_fixed_windows(self):
        result = pb.robustness(params(janela="2"))
        rows = result["rows"]
        self.assertEqual(result["window"], 2)
        self.assertGreaterEqual(len(rows), 2)
        for r in rows:
            self.assertLess(r["to"], f"{int(r['from'][:4]) + 2}{r['from'][4:]}")
            self.assertGreater(r["to"], f"{int(r['from'][:4]) + 1}{r['from'][4:]}")
            self.assertIn("–", r["label"])
            self.assertAlmostEqual(r["excess"], r["rule"]["irr"] - r["bova"]["irr"])   # sem metas, a régua é o BOVA11
            self.assertAlmostEqual(r["diffOnlyB"], r["rule"]["profit"] - r["onlyB"]["profit"])
        self.assertIn("janelas", result["summary"][0])
        cards = result["cards"]
        self.assertEqual(cards["drawdown"]["worst"], min(r["rule"]["maxDrawdown"] for r in rows))
        self.assertEqual(cards["excess"]["positive"], sum(r["excess"] > 0 for r in rows))
        self.assertEqual([w["key"] for w in cards["wins"]], ["bova", "ivvb", "onlyB"])
        with self.assertRaises(ValueError):
            params(janela="4")
        with self.assertRaises(ValueError):
            pb.robustness(params(janela="5"))

    def test_analysis_prompt(self):
        from marketpressure import backtest_prompt as bp
        result = pb.robustness(params(janela="2"))
        text = bp.robustness(result)["text"]
        self.assertIn("janelas de 2 anos", text)
        self.assertIn(result["verdict"], text)
        for r in result["rows"]:
            self.assertIn(r["label"], text)
        self.assertIn("**ROBUSTO**", text)
        # Mesmos parâmetros no mesmo dia → o resultado guardado, sem simular de novo.
        p = params(janela="2")
        first = bp.run_portfolio(p, pb.robustness)
        self.assertIs(bp.run_portfolio(params(janela="2"), pb.robustness), first)

    def test_median(self):
        self.assertEqual(pb._median([3, 1, 2]), 2)
        self.assertEqual(pb._median([4, 1, 2, 3]), 2.5)
        self.assertIsNone(pb._median([None]))

    def test_with_class_targets_the_reference_is_the_index_mix(self):
        p = params(alocacao="classes", metas={"acoes": "60", "internacionais": "40"})
        result = pb.robustness(p)
        self.assertEqual(result["ref"]["key"], "mix")
        self.assertIn("60% BOVA11 + 40% IVVB11", result["ref"]["label"])
        self.assertEqual([c["key"] for c in result["columns"]], ["rule", "onlyB", "bova", "ivvb", "mix"])
        for r in result["rows"]:
            self.assertAlmostEqual(r["excess"], r["rule"]["irr"] - r["mix"]["irr"])
            self.assertEqual(r["beats"]["mix"], r["rule"]["profit"] > r["mix"]["profit"])
        self.assertIn("índices", result["verdict"])

    def test_tax_uses_net_results(self):
        result = pb.robustness(params(ir=True, initial="1000000"))
        self.assertTrue(result["tax"])
        for r in result["rows"]:
            self.assertGreaterEqual(r["rule"]["taxDue"], 0)
            self.assertGreaterEqual(r["bova"]["taxDue"], 0)
            self.assertTrue(0 <= r["rule"]["avgCash"] <= 100)

    def test_short_period_is_refused(self):
        with self.assertRaises(ValueError):
            pb.robustness(params(to="2022-01-01"))


class ComparisonsAndTaxTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        pb._signals_cache.clear()
        periods = {"PETR4": 40, "VALE3": 33, "ITUB4": 47, "BOVA11": 40, "IVVB11": 55}
        from marketpressure import market_data
        market_data.raw_history = lambda t, market="b3": raw_history(t, period=periods.get(t, 40))

    def test_ivvb_and_index_mix_get_the_same_flows(self):
        result = pb.run(params(monthly="500", alocacao="classes", metas={"acoes": "60", "internacionais": "40"}))
        keys = [s["key"] for s in result["strategies"]]
        self.assertEqual(keys, ["rule", "onlyB", "bova", "ivvb", "mix"])
        mix = result["strategies"][-1]
        self.assertEqual(mix["label"], "Índices nas metas (60% BOVA11 + 40% IVVB11)")
        contributed = {s["metrics"]["contributed"] for s in result["strategies"]}
        self.assertEqual(len(contributed), 1)

    def test_mix_curve(self):
        cal = ["2024-01-02", "2024-01-03", "2024-02-01"]
        prices = {"A": {"2024-01-02": 10, "2024-01-03": 20, "2024-02-01": 20}, "B": {"2024-01-02": 5, "2024-02-01": 10}}
        curve = pb._mix_curve(cal, [("2024-01-02", 100), ("2024-02-01", 100)], {"A": 0.6, "B": 0.4}, prices)
        self.assertAlmostEqual(curve[1]["equity"], 60 * 2 + 40)          # A dobrou, B sem preço novo
        self.assertAlmostEqual(curve[2]["equity"], 120 + 80 + 100)       # B dobrou e o aporte entra
        self.assertEqual(pb._mix_weights({"acoes": 0.5, "fundos": 0.1, "internacionais": 0.4}), {"BOVA11": 0.6, "IVVB11": 0.4})

    def test_stock_sales_up_to_20k_a_month_are_exempt(self):
        m = pb.run(params(ir=True))["strategies"][0]["metrics"]   # R$ 10 mil: vendas de ações bem abaixo de R$ 20 mil/mês
        self.assertEqual((m["taxPaid"], m["taxDue"]), (0, 0))
        self.assertAlmostEqual(m["profitNet"], m["profit"])

    def test_tax_is_paid_and_lowers_the_result(self):
        free = pb.run(params(initial="1000000"))["strategies"][0]["metrics"]
        taxed = pb.run(params(initial="1000000", ir=True))
        m = taxed["strategies"][0]["metrics"]
        self.assertGreater(m["taxPaid"], 0)
        self.assertLess(m["final"], free["final"])
        self.assertAlmostEqual(m["profitNet"], m["profit"] - m["taxDue"])
        bova = taxed["strategies"][2]["metrics"]
        self.assertAlmostEqual(bova["taxDue"], max(0, bova["profit"]) * 0.15)
        self.assertNotIn("taxPaid", free)

    def test_cash_yield_is_net_of_tax(self):
        self.assertAlmostEqual(pb._cash_yield(0.001, {"tax": True}), 0.00085)
        self.assertEqual(pb._cash_yield(0.001, {"tax": False}), 0.001)
        curve = [{"cash": 50, "equity": 100}, {"cash": 0, "equity": 100}]
        self.assertEqual(pb._avg_cash(curve), 25)

    def test_large_position_warning(self):
        self.assertTrue(any("Fatia de 15%" in w for w in pb.run(params(position="15"))["warnings"]))
        self.assertFalse(any("Fatia de" in w for w in pb.run(params(position="10"))["warnings"]))


class YearlyTest(DatabaseTestCase):
    def test_years_add_up_to_the_period(self):
        curve = [{"date": d, "equity": e, "contributed": 0, "cash": 0} for d, e in
                 (("2023-11-01", 1000), ("2023-12-29", 1100), ("2024-01-02", 2100), ("2024-12-30", 2310))]
        flows = [("2023-11-01", 1000), ("2024-01-02", 1000)]
        y = pb._yearly(curve, flows)
        self.assertEqual([r["year"] for r in y], ["2023", "2024"])
        self.assertAlmostEqual(y[0]["profit"], 100)            # 1.100 − 0 − 1.000
        self.assertAlmostEqual(y[0]["return"], 10)
        self.assertAlmostEqual(y[1]["profit"], 2310 - 1100 - 1000)
        self.assertAlmostEqual(y[1]["return"], 10)              # o aporte de janeiro não conta como rendimento
        self.assertAlmostEqual(sum(r["profit"] for r in y), 2310 - 2000)

    def test_run_has_years_for_every_strategy(self):
        from marketpressure import market_data
        market_data.raw_history = lambda t, market="b3": raw_history(t)
        pb._signals_cache.clear()
        result = pb.run(pb.parse_params({"universe": "lista", "tickers": "PETR4", "initial": "10000", "cdi": False}))
        for s in result["strategies"]:
            self.assertAlmostEqual(sum(y["profit"] for y in s["years"]), s["metrics"]["profit"], places=4, msg=s["key"])


class AdviceAndClassesTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        pb._signals_cache.clear()

    def test_follow_the_screener_indication(self):
        on = pb.run(params())
        off = pb.run(params(indicacao=False))
        self.assertIsInstance(on["adviceSkipped"], int)
        self.assertIsNone(off["adviceSkipped"])
        self.assertTrue(on["params"]["advice"])
        self.assertGreaterEqual(on["adviceSkipped"], 0)

    def test_class_targets(self):
        p = params(alocacao="classes", metas={"acoes": "60", "internacionais": "40"})
        self.assertAlmostEqual(p["classes"]["acoes"], 0.6)
        result = pb.run(p)
        self.assertEqual(result["params"]["classes"]["acoes"], 60)
        for body in ({"alocacao": "classes", "metas": {"acoes": "0"}}, {"alocacao": "classes", "metas": {"acoes": "80", "etfs": "40"}}):
            with self.assertRaises(ValueError):
                params(**body)

    def test_single_asset_marks_the_buys_the_indication_would_skip(self):
        from marketpressure import screener
        view = screener.backtest_view("PETR4", "b3")
        self.assertTrue(all(isinstance(lot["wait"], list) for lot in view["lots"]))
        adv = view["stats"]["advice"]
        self.assertEqual(adv["wait"] + adv["buy"], len(view["lots"]))
