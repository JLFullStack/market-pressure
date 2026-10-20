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
        self.assertEqual([s["key"] for s in result["strategies"]], ["rule", "onlyB", "bova"])
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
        self.assertEqual([s["key"] for s in result["strategies"]], ["rule", "bova"])
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

    def test_invalid_parameters(self):
        for kw in ({"tickers": ""}, {"position": "0"}, {"universe": "x"}, {"from": "2021-12-01", "to": "2021-11-01"},
                   {"from": "ontem"}, {"cost": "9"}):
            with self.assertRaises(ValueError, msg=kw):
                params(**kw)


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
