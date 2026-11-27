"""Preço travado (core.LOCK): OPA, fechamento de capital ou fusão com preço fixado."""
import math
import random
import unittest

from marketpressure.carteira import portfolio as pf
from marketpressure.comum import core
from marketpressure.screener import screener as sc
from tests.support import DAY, START

STRATEGY = {"key": "reversao", "target": 0.1, "fraction": 0.0}


def noisy(n=700, locked_last=0, daily=0.02, calm=0.002, seed=1):
    """Candles em alta com ruído diário `daily`; os últimos `locked_last` com ruído `calm` (preço
    colado numa oferta)."""
    rnd, p, out = random.Random(seed), 20.0, []
    for t in range(n):
        sd = calm if t >= n - locked_last else daily
        p *= math.exp(0.0008 + rnd.gauss(0, sd))
        out.append({"date": START + t * DAY, "o": p, "h": p * (1 + sd / 2), "l": p * (1 - sd / 2), "c": p, "v": 1e6,
                    "adj": p})
    return out


def ranking_row(**extra):
    return {"ticker": "XPTO3", "price": 20.0, "score": 60, "pressure": 60, "bt": {},
            "setup": {"ma": {"name": "MM20", "dist": 0.01}}, "mm200Slope": 0.05, "return12m": 0.2, **extra}


class PriceLockTest(unittest.TestCase):
    def test_calm_after_normal_volatility_is_locked(self):
        closes = [c["c"] for c in noisy(locked_last=80)]
        stats = core.price_lock_stats(closes, len(closes) - 1)
        self.assertLess(stats["priceVol"], core.LOCK["maxVol"])
        self.assertGreater(stats["priceVolPast"], 0.2)
        self.assertIn("preço travado", core.price_locked(stats))

    def test_normal_or_always_calm_series_is_not_locked(self):
        closes = [c["c"] for c in noisy()]
        self.assertIsNone(core.price_locked(core.price_lock_stats(closes, len(closes) - 1)))
        # Sempre calmo (um ativo de baixa volatilidade): abaixo de maxVol, mas igual ao habitual.
        calm = [c["c"] for c in noisy(daily=0.004, calm=0.004)]
        self.assertIsNone(core.price_locked(core.price_lock_stats(calm, len(calm) - 1)))
        # Histórico curto: sem dado, nunca travado.
        self.assertIsNone(core.price_locked(core.price_lock_stats(closes[:30], 29)))

    def test_screener_waits_on_a_locked_stock_only(self):
        locked = ranking_row(priceVol=0.05, priceVolPast=0.4)
        advice = sc.advice(locked, None, STRATEGY, "acoes", 1)
        self.assertEqual(advice["label"], "AGUARDAR")
        self.assertTrue(advice["locked"])
        self.assertIn("OPA", advice["reason"])
        # Fundos não usam a regra (fases calmas de FIIs comuns pareceriam travadas).
        self.assertEqual(sc.advice(locked, None, STRATEGY, "fundos", 1)["label"], "COMPRAR")
        self.assertEqual(sc.advice(ranking_row(priceVol=0.3, priceVolPast=0.4), None, STRATEGY, "acoes", 1)["label"],
                         "COMPRAR")

    def test_analysis_carries_the_stats_for_stocks_only(self):
        candles = noisy(locked_last=80)
        self.assertIsNotNone(sc.analyze_asset(candles, False, "acoes")["priceVol"])
        self.assertIsNone(sc.analyze_asset(candles, False, "fundos")["priceVol"])

    def test_portfolio_position_warns_and_never_buys(self):
        candles = noisy(locked_last=80)
        tx = [{"id": "1", "date": core.day_of(candles[300]["date"]), "ticker": "XPTO3", "type": "compra",
               "quantity": 100, "price": candles[300]["c"], "fees": 0, "fx": 1}]
        held = pf.holding_signal(tx, "XPTO3", candles, "b3", STRATEGY, check_entry=True, check_lock=True)
        self.assertNotEqual(held["signal"]["key"], "buy")
        self.assertTrue(held["signal"]["reason"].startswith("Atenção: preço travado"))
        self.assertIn("aceitar a oferta", held["signal"]["reason"])
        self.assertFalse(pf.holding_signal(tx, "XPTO3", candles, "b3", STRATEGY)["signal"].get("locked"))
        setup = pf.setup_now(candles, "b3", "fechamento", check_lock=True)
        self.assertIn("preço travado", setup["locked"])
        self.assertIsNone(pf.setup_now(candles, "b3", "fechamento")["locked"])


if __name__ == "__main__":
    unittest.main()
