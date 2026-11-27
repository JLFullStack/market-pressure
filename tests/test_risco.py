"""Risco da carteira (carteira/risco.py): maior queda, queda atual, VaR/CVaR e volatilidade."""
import unittest
from datetime import date, timedelta

from marketpressure.carteira import risco


def series(index, bova=None):
    """Série no formato de portfolio.build_series a partir de uma cota (1 = início)."""
    days = [(date(2025, 1, 2) + timedelta(days=k)).isoformat() for k in range(len(index))]
    bova = bova or index
    return {"dates": days, "portfolio": [(v - 1) * 100 for v in index], "bova": [(v - 1) * 100 for v in bova]}


class RiskTest(unittest.TestCase):
    def test_drawdown_recovery_and_current(self):
        # sobe a 1,2, cai a 0,9 (−25%), volta a 1,2 e passa; depois cai 10% do novo topo.
        index = [1.0, 1.1, 1.2, 1.0, 0.9, 1.05, 1.2, 1.3, 1.17] + [1.17] * 20
        r = risco.analyze(series(index), 1000)
        dd = r["portfolio"]["maxDrawdown"]
        self.assertAlmostEqual(dd["pct"], -25)
        self.assertEqual((dd["peak"], dd["trough"], dd["recovered"]), ("2025-01-04", "2025-01-06", "2025-01-08"))
        self.assertEqual((dd["fallSessions"], dd["recoverySessions"]), (2, 2))
        self.assertAlmostEqual(r["portfolio"]["current"]["pct"], -10)
        self.assertEqual(r["portfolio"]["current"]["peak"], "2025-01-09")
        self.assertEqual(len(r["chart"]["portfolio"]), len(index))
        self.assertTrue(r["warning"])   # menos de 1 ano

    def test_var_and_cvar_are_the_worst_tail(self):
        # 100 dias: 95 com +0,1% e 5 quedas de −1% a −5%.
        rets = [0.001] * 95 + [-0.01, -0.02, -0.03, -0.04, -0.05]
        index, v = [1.0], 1.0
        for r in rets:
            v *= 1 + r
            index.append(v)
        out = risco.analyze(series(index), 10000)
        day = out["portfolio"]["day"]
        self.assertAlmostEqual(day["var"], -1.0, places=6)     # a perda que só 5% dos dias superaram
        self.assertAlmostEqual(day["cvar"], -3.0, places=6)    # a média das 5 piores
        self.assertAlmostEqual(day["varBRL"], 100.0, places=4)
        self.assertAlmostEqual(day["cvarBRL"], 300.0, places=4)
        self.assertLessEqual(out["portfolio"]["month"]["cvar"], out["portfolio"]["month"]["var"])

    def test_comparison_with_bova_and_short_history(self):
        calm = [1 + 0.001 * k for k in range(300)]
        wild = [1 + 0.001 * k + (0.08 if k % 30 < 15 else 0) for k in range(300)]
        r = risco.analyze(series(calm, wild), 5000)
        self.assertIsNone(r["warning"])   # mais de 1 ano de dias e de janelas mensais
        self.assertLess(r["portfolio"]["volatility"], r["bova"]["volatility"])
        self.assertIsNone(risco.analyze(series([1.0] * 10), 1000))
        self.assertIsNone(risco.analyze(None, 1000))
        # Sem queda nenhuma: maior queda 0, nenhuma perda em R$.
        flat = risco.analyze(series(calm), 5000)
        self.assertEqual(flat["portfolio"]["maxDrawdown"]["pct"], 0)
        self.assertEqual(flat["portfolio"]["day"]["varBRL"], 0)


if __name__ == "__main__":
    unittest.main()
