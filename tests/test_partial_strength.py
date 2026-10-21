"""Venda parcial "quando a alta perder força" (core.PARTIAL_TIMINGS = "forca") em cada ponto do app:
o histórico do Screener (screener.with_partials), a Carteira (portfolio.open_lots e a Situação) e o
backtest de carteira (portfolio_backtest._partial_price)."""

import unittest
from datetime import datetime, timezone
from unittest import mock

from marketpressure import core, portfolio as pf, portfolio_backtest as pb, screener


def ts(iso):
    return int(datetime.fromisoformat(f"{iso}T15:00:00").replace(tzinfo=timezone.utc).timestamp())


class WeakCloseTest(unittest.TestCase):
    def test_needs_the_target_and_no_strength(self):
        ind = {"closes": [111.0], "mm20": [100.0], "atr14": [5.0], "rsi": [70.0]}
        self.assertFalse(core.weak_close(ind, 0, 110))      # 2,2 ATR acima da MM20 e RSI 70: forte
        ind["rsi"] = [60.0]
        self.assertTrue(core.weak_close(ind, 0, 110))       # RSI abaixo de 65: sem força, no alvo
        self.assertFalse(core.weak_close(ind, 0, 112))      # sem força, mas abaixo do alvo


class ScreenerTest(unittest.TestCase):
    # Compra no candle 1 a 100; a máxima toca o alvo (110) no 3; o fechamento 4 está forte; o 5 está
    # sem força e no alvo: a parte sai na abertura do 6.
    CANDLES = [{"o": 100, "h": 100, "c": 100}, {"o": 100, "h": 101, "c": 100}, {"o": 100, "h": 105, "c": 104},
               {"o": 104, "h": 111, "c": 108}, {"o": 108, "h": 113, "c": 112}, {"o": 112, "h": 112, "c": 111},
               {"o": 110.5, "h": 111, "c": 110}, {"o": 110, "h": 110, "c": 109}]
    WEAK = {5, 6, 7}

    def setUp(self):
        self.ind = {"candles": self.CANDLES, "closes": [c["c"] for c in self.CANDLES]}
        patcher = mock.patch.object(screener, "weak_close",
                                    lambda ind, i, goal: ind["closes"][i] >= goal and i in self.WEAK)
        patcher.start()
        self.addCleanup(patcher.stop)

    def lot(self, exit_i=None):
        return {"entry": {"i": 1, "price": 100}, "partial": None,
                "exit": {"i": exit_i, "price": self.CANDLES[exit_i]["o"]} if exit_i else None}

    def test_sells_at_the_next_open_after_the_first_weak_close(self):
        alvo, = screener.with_partials(self.ind, [self.lot()], 0.10)
        forca, = screener.with_partials(self.ind, [self.lot()], 0.10, "forca")
        self.assertEqual((alvo["partial"]["i"], round(alvo["partial"]["price"], 6)), (3, 110))   # no alvo, no dia em que a máxima chega
        self.assertEqual(forca["partial"], {"i": 6, "price": 110.5})       # fechamento 5 sem força → abertura 6

    def test_no_partial_when_the_rule_b_sells_first(self):
        # A regra B dispara no fechamento 5 (saída na abertura 6): o sinal de força no mesmo
        # fechamento chega tarde, a compra sai inteira.
        forca, = screener.with_partials(self.ind, [self.lot(exit_i=6)], 0.10, "forca")
        self.assertIsNone(forca["partial"])

    def test_force_waits_for_a_close_at_or_above_the_target(self):
        self.WEAK = {3}   # sem força só no fechamento 3, que ficou abaixo do alvo (108)
        forca, = screener.with_partials(self.ind, [self.lot()], 0.10, "forca")
        self.assertIsNone(forca["partial"])


class CarteiraTest(unittest.TestCase):
    DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    HIGHS = [10, 10.5, 11.2, 11.5, 11.3]
    CLOSES = [10, 10.4, 11.0, 11.4, 11.1]

    def candles(self):
        return [{"date": ts(d), "o": c, "h": h, "l": c, "c": c, "v": 1, "adj": c}
                for d, h, c in zip(self.DAYS, self.HIGHS, self.CLOSES)]

    def lots(self, weak_days):
        candles = self.candles()
        ind = {"candles": candles, "closes": [c["c"] for c in candles]}
        weak = lambda ind, i, goal: ind["closes"][i] >= goal and self.DAYS[i] in weak_days
        txs = [{"id": "1", "date": "2024-01-02", "ticker": "PETR4", "type": "compra", "quantity": 100, "price": 10,
                "fees": 0, "fx": 1}]
        with mock.patch.object(pf, "weak_close", weak):
            return pf.open_lots(txs, "PETR4", candles, 0.10, 0.5, "forca", ind)

    def test_due_only_after_the_rally_loses_strength(self):
        armed, = self.lots(weak_days=set())
        self.assertEqual((armed["hitDate"], armed["dueDate"], armed["pending"], armed["waiting"]), ("2024-01-04", None, 0, 50))
        self.assertIsNone(pf.partial_to_sell([armed], "b3", {"key": "forca", "target": 0.1, "fraction": 0.5}))
        due, = self.lots(weak_days={"2024-01-05"})
        self.assertEqual((due["dueDate"], due["pending"]), ("2024-01-05", 50))

    def test_situation_texts(self):
        strategy = {"key": "forca", "target": 0.1, "fraction": 0.5, "timing": "forca"}
        money = lambda v: f"R$ {v:.2f}"
        due, = self.lots(weak_days={"2024-01-05"})
        partial = pf.partial_to_sell([due], "b3", strategy)
        reason = pf._partial_reason(partial, strategy, money)
        self.assertIn("a alta perdeu força no fechamento de 05/01/2024", reason)
        self.assertIn("na abertura", reason)
        armed, = self.lots(weak_days=set())
        n = len(self.DAYS)
        ind = {"candles": self.candles(), "closes": self.CLOSES, "mm20": [9.0] * n, "mm50": [8.0] * n,
               "mm200": [7.0] * n, "atr14": [0.5] * n, "rsi": [70.0] * n}
        signal = pf.position_signal(ind, n - 1, None, [armed], strategy)
        self.assertEqual(signal["key"], "hold")
        self.assertIn("fica para a abertura seguinte ao primeiro fechamento em que a alta perder força", signal["reason"])
        # 1 cota na B3: 50% dela não chega a 1 cota, então não há parcial a esperar.
        one = {**armed, "quantity": 1, "remaining": 1, "waiting": 0.5}
        signal = pf.position_signal(ind, n - 1, None, [one], strategy, whole=True)
        self.assertNotIn("fica para a abertura", signal["reason"])
        self.assertIn("50% de 1 cota não chega a 1 cota inteira", signal["reason"])
        self.assertIn("atingiu +10% em 04/01/2024", signal["reason"])
        self.assertIn("fica para a abertura", pf.position_signal(ind, n - 1, None, [one], strategy)["reason"])


class PortfolioBacktestTest(unittest.TestCase):
    def test_signal_at_the_close_sells_at_the_next_open(self):
        a = {"candles": [{"o": 100, "h": 100, "c": 100}, {"o": 100, "h": 111, "c": 112}, {"o": 113, "h": 114, "c": 111},
                         {"o": 109, "h": 110, "c": 108}],
             "strong": [False, True, False, False]}
        lot = {"i": 0, "hit": False}
        prices = [pb._partial_price(lot, 110, 0.5, "forca", a, i, False) for i in range(1, 4)]
        self.assertEqual(prices, [None, None, 109])    # forte no 1, sem força no 2 (no alvo) → abertura do 3
        alvo = {"i": 0, "hit": False}
        self.assertEqual(pb._partial_price(alvo, 110, 0.5, "alvo", a, 1, False), 110)
        self.assertIsNone(pb._partial_price({"i": 0, "hit": False}, 110, 0.0, "forca", a, 2, False))


if __name__ == "__main__":
    unittest.main()
