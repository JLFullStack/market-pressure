"""Previsão do cruzamento da MM50 abaixo da MM200 (cruzamento.py) e o registro ao vivo."""

import unittest
from datetime import datetime

from marketpressure.carteira import portfolio as pf
from marketpressure.comum import cruzamento as cz
from marketpressure.comum import database
from marketpressure.comum.core import compute_indicators, day_of
from marketpressure.screener import screener
from tests.support import DAY, START, DatabaseTestCase


def falling(n_up=300, n_down=60, start=START):
    """Alta longa e depois queda: o fechamento perde a MM200 com a MM50 ainda acima, e as médias
    se aproximam até cruzar."""
    out, p = [], 100.0
    for t in range(n_up + n_down):
        p *= 1.002 if t < n_up else 0.985
        out.append({"date": start + t * DAY, "o": p, "h": p * 1.01, "l": p * 0.99, "c": p, "v": 1e6, "adj": p})
    return out


def days_of(candles):
    return [day_of(c["date"]) for c in candles]


class ForecastTest(unittest.TestCase):
    def test_only_below_mm200_with_mm50_above(self):
        c = falling()
        ind = compute_indicators(c)
        days = days_of(c)
        states = [i for i in range(len(c)) if cz.in_state(ind, i)]
        self.assertTrue(states)
        i = states[0]
        f = cz.forecast(ind, i, days[i])
        self.assertEqual(f["event"], "abaixo")
        self.assertLess(f["distance"], 0)            # fechamento abaixo da MM200
        self.assertGreater(f["spread"], 0)            # MM50 ainda acima
        self.assertIsNotNone(f["sessions"])           # médias se aproximando
        self.assertTrue(f["projection"])              # projeção desenhada até o encontro
        self.assertGreater(f["date"], days[i])
        self.assertIn("% em", f["label"])
        # Numa alta, sem fechamento abaixo da MM200: nada a prever.
        self.assertIsNone(cz.forecast(ind, 299, days[299]))

    def test_probabilities_follow_the_projection(self):
        self.assertEqual(cz.probabilities(5), {h: cz.PROB[h][0] for h in cz.HORIZONS})
        self.assertEqual(cz.probabilities(None), {h: cz.PROB[h][1] for h in cz.HORIZONS})
        p = cz.probabilities(15)
        self.assertEqual((p[10], p[20], p[40]), (cz.PROB[10][1], cz.PROB[20][0], cz.PROB[40][0]))
        # A chance nunca cai com um prazo maior.
        for s in (None, 3, 12, 30, 80):
            p = cz.probabilities(s)
            self.assertLessEqual(p[10], p[20])
            self.assertLessEqual(p[20], p[40])

    def test_add_sessions_skips_weekends(self):
        self.assertEqual(cz.add_sessions("2026-11-27", 1), "2026-11-30")   # sexta → segunda
        self.assertEqual(cz.add_sessions("2026-11-25", 0), "2026-11-25")


class LiveLogTest(unittest.TestCase):
    def test_rows_and_stats(self):
        c = falling()
        ind = compute_indicators(c)
        days = days_of(c)
        rows = cz.live_rows("TEST3", ind, days, since=days[0])
        self.assertTrue(rows)
        self.assertTrue(all(r["ticker"] == "TEST3" for r in rows))
        self.assertTrue(any(r["hit"] is not None for r in rows))   # o cruzamento veio
        stats = cz.live_stats(rows)
        s = stats["40"]
        self.assertEqual(s["closed"] + s["open"], len(rows))
        self.assertEqual(s["real"], 100.0)   # todas as encerradas cruzaram
        self.assertIn("ao vivo", cz.accuracy_text(stats))
        self.assertIn("nenhuma previsão encerrada", cz.accuracy_text(cz.live_stats([])))

    def test_alert_when_live_calibration_drifts(self):
        rows = [{"date": "2026-12-01", "ticker": f"T{k}", "sessions": 5, "hit": None, "avail": 50,
                 "p10": 0.85, "p20": 0.87, "p40": 0.91} for k in range(cz.LIVE_MIN_CLOSED)]
        stats = cz.live_stats(rows)
        self.assertTrue(stats["20"]["alert"])        # previu 87% e nenhum cruzou
        self.assertIn("precisa ser revisto", cz.accuracy_text(stats))
        self.assertFalse(cz.live_stats(rows[:5])["20"]["alert"])   # poucas encerradas: sem alerta

    def test_merge_keeps_assets_out_of_the_scan(self):
        old = [{"date": "2026-12-01", "ticker": "SAIU3"}, {"date": "2026-12-01", "ticker": "FICA3"}]
        new = [{"date": "2026-12-02", "ticker": "FICA3"}]
        merged = cz.merge_log(old, new, {"FICA3"})
        self.assertEqual([(r["ticker"], r["date"]) for r in merged], [("SAIU3", "2026-12-01"), ("FICA3", "2026-12-02")])


class SavedLogTest(DatabaseTestCase):
    def test_scan_writes_the_live_log(self):
        row = {"date": "2026-12-01", "ticker": "TEST3", "sessions": 12.5, "hit": None, "avail": 3,
               "p10": 0.104, "p20": 0.87, "p40": 0.909}
        scan = {"version": screener.SCAN_VERSION, "timestamp": 1, "universeSize": 0, "failures": [], "analyses": []}
        screener.write_scan(scan)   # sem previsões calculadas: a tabela não é tocada
        self.assertFalse(database.exists(cz.LOG_TABLE))
        a = {"ticker": "TEST3", "name": "T", "sector": "", "kind": "acoes", "traded": 1e7, "crossingLog": [row]}
        screener.write_scan({**scan, "analyses": [{**a, **{m: {"date": 0, "price": 1, "change": 0, "setup": {"ok": False}}
                                                          for m in screener.MODES}}]})
        saved = cz.read_log()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["ticker"], "TEST3")
        self.assertAlmostEqual(saved[0]["p20"], 0.87)

    def test_ticker_crossing_log_only_since_freeze(self):
        self.assertEqual(screener.crossing_log("TEST3", falling()), [])   # histórico todo antes de FREEZE
        # Queda começando depois de FREEZE: só os pregões de FREEZE em diante entram no registro.
        freeze = datetime.fromisoformat(cz.FREEZE + "T12:00:00+00:00").timestamp()
        c = falling(start=int(freeze) - 310 * DAY)
        rows = screener.crossing_log("TEST3", c)
        self.assertTrue(rows)
        self.assertTrue(all(r["date"] >= cz.FREEZE for r in rows))


class AssetChartTest(unittest.TestCase):
    def test_chart_carries_the_forecast(self):
        c = falling()
        ind = compute_indicators(c)
        i = next(i for i in range(len(c)) if cz.in_state(ind, i))
        cut = c[:i + 1]
        txs = [{"id": "1", "date": day_of(c[250]["date"]), "ticker": "TEST3", "type": "compra", "quantity": 10,
                "price": c[250]["c"], "fees": 0, "fx": 1}]
        rule = {"key": "reversao", "target": 0.1, "fraction": 0.5, "timing": "alvo"}
        out = pf.asset_chart(txs, "TEST3", cut, "b3", rule, now=cut[-1]["date"] + 2 * DAY)
        self.assertEqual(out["crossing"]["event"], "abaixo")
        up = pf.asset_chart(txs, "TEST3", c[:280], "b3", rule, now=c[279]["date"] + 2 * DAY)
        self.assertIsNone(up["crossing"])


if __name__ == "__main__":
    unittest.main()
