import unittest
from datetime import datetime, timezone

from marketpressure import core
from tests.support import candles


def ts(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


class ArithmeticTest(unittest.TestCase):
    def test_js_round_rounds_half_up(self):
        self.assertEqual(core.js_round(2.5), 3)
        self.assertEqual(core.js_round(-2.5), -2)

    def test_fmt_num_matches_pt_br_locale(self):
        self.assertEqual(core.fmt_num(1234.5, 1), "1.234,5")
        self.assertEqual(core.fmt_num(0.125, 2), "0,13")
        self.assertEqual(core.fmt_brl(1000), "R$ 1.000,00")
        self.assertEqual(core.fmt_qty(0.5), "0,5")
        self.assertEqual(core.fmt_date("2026-10-09"), "09/10/2026")

    def test_sma(self):
        self.assertEqual(core.sma([1, 2, 3, 4, 5], 2), [None, 1.5, 2.5, 3.5, 4.5])


class ClockTest(unittest.TestCase):
    def test_london_follows_eu_dst(self):
        self.assertEqual(core.local_time(ts("2026-07-01T12:00"), "Europe/London").hour, 13)
        self.assertEqual(core.local_time(ts("2026-01-15T12:00"), "Europe/London").hour, 12)

    def test_session_in_progress_until_18h_sao_paulo(self):
        today = [{"date": ts("2026-10-09T13:00")}]  # 10h em Brasília
        self.assertTrue(core.session_in_progress(today, now=ts("2026-10-09T18:00")))   # 15h
        self.assertFalse(core.session_in_progress(today, now=ts("2026-10-09T22:00")))  # 19h
        self.assertEqual(core.closed_candles(today, now=ts("2026-10-09T18:00")), [])


class SetupTest(unittest.TestCase):
    def test_uptrend_with_pullbacks_gives_signals(self):
        ind = core.compute_indicators(candles())
        signals = [i for i in range(220, 400) if core.evaluate_setup(ind, i)["ok"]]
        self.assertTrue(signals)
        setup = core.evaluate_setup(ind, signals[0])
        self.assertTrue(setup["trend"])
        self.assertIn(setup["ma"]["name"], ("MM20", "MM50", "MM200"))
        self.assertGreaterEqual(setup["pullback"], core.SETUP["minPullback"])

    def test_downtrend_has_no_setup_and_triggers_exit(self):
        ind = core.compute_indicators(candles(drift=-0.003, wave=0))
        last = len(ind["closes"]) - 1
        self.assertFalse(core.evaluate_setup(ind, last)["ok"])
        self.assertTrue(core.exit_signal(ind, last))


if __name__ == "__main__":
    unittest.main()


class DurationTest(unittest.TestCase):
    def test_sessions_become_years_and_months(self):
        from marketpressure.core import fmt_duration, fmt_sessions
        self.assertEqual([fmt_sessions(n) for n in (5, 21, 252, 303, 1025)],
                         ["menos de 1 mês", "1 mês", "1 ano", "1 ano e 2 meses", "4 anos e 1 mês"])
        self.assertEqual(fmt_duration(26), "2 anos e 2 meses")
