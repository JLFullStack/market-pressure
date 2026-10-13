"""Metas da Carteira: validação do formulário e projeção (goals.py)."""

import unittest

from marketpressure import goals

TODAY = "2026-10-13"


def form(**kw):
    base = {"name": "Aposentadoria", "kind": "renda", "target": "10000", "withdrawal": "4", "initial": "0",
            "usePortfolio": False, "monthly": "2000", "rate": "10", "inflation": "4", "adjust": True, "targetDate": ""}
    return {**base, **kw}


def goal(**kw):
    g, error = goals.goal_from_form(form(**kw), TODAY, "g1")
    assert error is None, error
    return g


class GoalFormTest(unittest.TestCase):
    def test_numbers_typed_with_comma_and_defaults(self):
        g = goal(target="10000,5", rate="9,5", withdrawal="")
        self.assertEqual((g["target"], g["rate"], g["withdrawal"], g["targetDate"]), (10000.5, 9.5, 4.0, None))

    def test_invalid_fields(self):
        cases = {"name": "", "kind": "x", "target": "0", "monthly": "-1", "rate": "abc", "inflation": "60",
                 "withdrawal": "25", "targetDate": "2020-01-01"}
        for field, value in cases.items():
            g, error = goals.goal_from_form(form(**{field: value}), TODAY)
            self.assertIsNone(g, field)
            self.assertTrue(error, field)
        g, error = goals.goal_from_form(form(monthly="0", initial="0"), TODAY)
        self.assertEqual(error, "Informe um aporte mensal ou um patrimônio inicial.")


class ProjectionTest(unittest.TestCase):
    def test_income_goal_needs_income_times_12_over_the_withdrawal_rate(self):
        self.assertAlmostEqual(goals.target_capital(goal()), 3_000_000)
        self.assertAlmostEqual(goals.target_capital(goal(kind="patrimonio", target="500000")), 500_000)

    def test_real_rate_discounts_inflation(self):
        r = goals.rates(goal())
        self.assertAlmostEqual(r["realYear"], 1.10 / 1.04 - 1)
        self.assertAlmostEqual((1 + r["realMonth"]) ** 12, 1.10 / 1.04)

    def test_months_to_reach_without_interest(self):
        self.assertEqual(goals.months_to_reach(0, 1000, 0, 0, True, 12000), 12)
        self.assertEqual(goals.months_to_reach(20000, 1000, 0, 0, True, 12000), 0)
        self.assertIsNone(goals.months_to_reach(0, 1, 0, 0, True, 1e9))

    def test_fixed_contribution_loses_purchasing_power(self):
        fixed = goals.simulate(0, 1000, 0, 0.01, False, 12)
        self.assertLess(fixed[-1][2], 12000)
        self.assertAlmostEqual(goals.simulate(0, 1000, 0, 0.01, True, 12)[-1][2], 12000)

    def test_projection(self):
        result = goals.project(goal(), 0, TODAY)
        reach = result["reach"]
        self.assertGreater(reach["months"], 0)
        rows = goals.simulate(0, 2000, goals.rates(goal())["realMonth"], 0, True, reach["months"])
        self.assertGreaterEqual(rows[-1][1], 3_000_000)
        self.assertLess(rows[-2][1], 3_000_000)
        self.assertAlmostEqual(reach["contributed"] + reach["interest"], reach["value"])
        self.assertTrue(result["summary"].startswith("Você alcança a meta em "))
        self.assertEqual([m["pct"] for m in result["milestones"]], [25, 50, 75, 100])
        self.assertEqual(result["milestones"][-1]["months"], reach["months"])
        self.assertIsNotNone(result["turningPoint"])
        self.assertEqual(len(result["scenarios"]["rows"]), 3)
        self.assertTrue(result["scenarios"]["rows"][0]["cells"][1]["current"])
        self.assertEqual(result["yearly"][0]["value"], 0)

    def test_required_monthly_reaches_the_goal_on_the_target_date(self):
        g = goal(targetDate="2046-10-13")
        by = goals.project(g, 50_000, TODAY)["byDate"]
        self.assertEqual(by["months"], 240)
        self.assertFalse(by["onTrack"])
        m = goals.rates(g)["realMonth"]
        value = goals.simulate(50_000, by["requiredMonthly"], m, 0, True, 240)[-1][1]
        self.assertAlmostEqual(value, 3_000_000, delta=1)

    def test_goal_out_of_reach(self):
        result = goals.project(goal(monthly="10", rate="4", inflation="4"), 0, TODAY)
        self.assertIsNone(result["reach"])
        self.assertIn("não é alcançada em 100 anos", result["summary"])
        self.assertTrue(result["warnings"])

    def test_average_contribution_of_the_last_12_months(self):
        flows = [("2025-01-10", 50_000), ("2025-11-01", 2000), ("2026-03-01", 3000), ("2026-06-01", -1000)]
        avg = goals.average_contribution(flows, TODAY)
        self.assertEqual(avg["from"], "2025-10-13")
        self.assertAlmostEqual(avg["monthly"], 4000 / 12, delta=1)
        young = goals.average_contribution([("2026-08-13", 3000), ("2026-09-13", 3000)], TODAY)
        self.assertAlmostEqual(young["monthly"], 6000 / young["months"])
        self.assertAlmostEqual(young["months"], 2, delta=0.05)
        self.assertIsNone(goals.average_contribution([], TODAY))

    def test_annualized_return(self):
        self.assertAlmostEqual(goals.annualized_return(10, "2025-10-13", "2026-10-13"), 10)
        self.assertAlmostEqual(goals.annualized_return(21, "2024-10-13", "2026-10-13"), 10, delta=0.01)
        self.assertIsNone(goals.annualized_return(5, "2026-08-13", TODAY))

    def test_tracking_against_the_plan(self):
        g = {**goal(rate="0", inflation="0", monthly="1000"), "created": "2026-07-13"}
        series = {"dates": ["2026-07-13", "2026-08-13", "2026-09-14"], "value": [10_000, 11_500, 12_000]}
        t = goals.tracking(g, series, 13_500, TODAY)
        self.assertTrue(t["started"])
        # 10.000 + 3 aportes de 1.000, sem juros, + a fração já passada do 4º mês (92 dias)
        expected = 13_000 + 1000 * (92 / goals.DAYS_PER_MONTH - 3)
        self.assertAlmostEqual(t["planned"], expected)
        self.assertAlmostEqual(t["diff"], 13_500 - expected)
        self.assertTrue(t["ahead"])
        self.assertEqual(t["chart"]["labels"], ["2026-07-13", "2026-08-13", "2026-09-13", "2026-10-13", TODAY])
        self.assertEqual(t["chart"]["actual"][:3], [10_000, 11_500, 11_500])
        self.assertFalse(goals.tracking({**g, "created": TODAY}, series, 13_500, TODAY)["started"])

    def test_projections_with_real_data(self):
        g = goal()
        rows = goals.projections(g, 100_000, TODAY, {"monthly": 4000, "months": 12}, {"rate": 20, "note": "12 meses"})
        self.assertEqual([r["label"] for r in rows], ["Plano da meta", "Seu aporte real", "Rentabilidade real da carteira",
                                                      "Aporte e rentabilidade reais"])
        self.assertTrue(rows[0]["months"] > rows[1]["months"] > rows[3]["months"])
        self.assertEqual(len(goals.projections(g, 0, TODAY)), 1)

    def test_duration_text(self):
        self.assertEqual([goals.duration_text(m) for m in (0, 1, 12, 32)],
                         ["já alcançada", "1 mês", "1 ano", "2 anos e 8 meses"])
