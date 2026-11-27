"""Reamostragem das operações do backtest (backtest/reamostragem.py)."""
import unittest

from marketpressure.backtest import reamostragem as rs


def trades(*profits):
    """Operações com lucro em R$ e resultado em % sobre R$ 1.000 cada."""
    return [{"profit": p, "return": p / 10, "n": k} for k, p in enumerate(profits, 1)]


class ResampleTest(unittest.TestCase):
    def test_same_input_same_answer_and_ordered_bands(self):
        ops = trades(*[50, -20, 80, 10, -40, 120, 30, -10, 60, 5, 15, -5])
        a, b = rs.analyze(ops, sum(t["profit"] for t in ops)), rs.analyze(ops, sum(t["profit"] for t in ops))
        self.assertEqual(a, b)   # semente fixa
        self.assertLessEqual(a["profit"]["low"], a["profit"]["mid"])
        self.assertLessEqual(a["profit"]["mid"], a["profit"]["high"])
        self.assertEqual(sum(b["count"] for b in a["histogram"]), rs.DRAWS)
        self.assertEqual([w["n"] for w in a["withoutTop"]], [1, 3, 5])
        self.assertAlmostEqual(a["withoutTop"][0]["profit"], a["actual"] - 120)

    def test_verdicts(self):
        steady = trades(*([100] * 12 + [-20] * 3))
        self.assertEqual(rs.analyze(steady, sum(t["profit"] for t in steady))["verdict"]["key"], "consistente")
        lucky = trades(5000, 900, 800, *([-100] * 12))
        verdict = rs.analyze(lucky, sum(t["profit"] for t in lucky), label=lambda t: f"compra {t['n']}")["verdict"]
        self.assertEqual(verdict["key"], "fragil")
        self.assertIn("compra 1", verdict["text"])
        losing = trades(*([-50] * 10 + [30] * 3))
        self.assertEqual(rs.analyze(losing, sum(t["profit"] for t in losing))["verdict"]["key"], "abaixo")
        few = trades(100, 200, -50)
        self.assertEqual(rs.analyze(few, 250)["verdict"]["key"], "inconclusivo")
        self.assertIsNone(rs.analyze([], 0))

    def test_reference_and_fixed_part(self):
        ops = trades(*([200] * 12))
        # O que não é operação (CDI do caixa, imposto) fica fixo: o lucro do período é 2.400 + 600.
        r = rs.analyze(ops, 3000, [{"key": "bova", "label": "BOVA11", "profit": 2000}])
        self.assertEqual(r["profit"]["low"], 3000)   # operações iguais: todo sorteio dá o mesmo
        self.assertEqual(r["refs"][0]["belowChance"], 0)
        self.assertEqual(r["verdict"]["key"], "consistente")
        self.assertIn("BOVA11", r["verdict"]["text"])
        below = rs.analyze(ops, 3000, [{"key": "bova", "label": "BOVA11", "profit": 3500}])
        self.assertEqual(below["verdict"]["key"], "abaixo")
        self.assertFalse(below["withoutTop"][0]["beats"]["bova"])

    def test_portfolio_uses_net_profit_with_tax_and_ref_order(self):
        result = {
            "strategies": [
                {"key": "rule", "label": "Estratégia", "metrics": {"profit": 1500, "profitNet": 1300}},
                {"key": "bova", "label": "BOVA11 (mesmos aportes)", "metrics": {"profit": 900, "profitNet": 800}},
                {"key": "cdi", "label": "CDI", "metrics": {"profit": 700, "profitNet": 600}},
                {"key": "mix", "label": "Índices nas metas", "metrics": {"profit": 1000, "profitNet": 850}},
            ],
            "trades": [{"profit": 100, "return": 10, "ticker": "PETR4", "entry": {"date": "2024-03-05"}}] * 12,
        }
        r = rs.portfolio(result)
        self.assertEqual(r["actual"], 1300)
        self.assertEqual([x["key"] for x in r["refs"]], ["mix", "bova", "cdi"])
        self.assertEqual(r["refs"][1]["label"], "BOVA11")
        self.assertEqual(r["withoutTop"][0]["names"], ["PETR4 de 05/03/2024"])


if __name__ == "__main__":
    unittest.main()
