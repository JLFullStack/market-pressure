"""Favoritos (carteira/favoritos.py) e o card do gráfico para qualquer ativo (rotas /api/favoritos e
/api/grafico)."""
from unittest import mock

from marketpressure import api
from marketpressure.carteira import favoritos as fav, portfolio_store as store
from marketpressure.comum import market_data
from tests.support import DatabaseTestCase


def call(method, path, query=None, body=None):
    return api.call(method, path, query or {}, lambda: body)


class FavoritesTest(DatabaseTestCase):
    def portfolio(self):
        pid = store.apply_action({"action": "criar_carteira", "name": "Principal"})["id"]
        for kind, date in (("compra", "2021-06-01"), ("venda", "2021-09-01")):
            store.apply_action({"action": "nova_operacao", "id": pid, "ticker": "CSMG3", "market": "b3", "type": kind,
                                "date": date, "quantity": "100", "price": "30", "fees": "0"})
        return pid

    def test_add_remove_and_validation(self):
        status, r = call("POST", "/api/favoritos", body={"op": "add", "ticker": "petr4", "market": "b3"})
        self.assertEqual((status, r["message"]), (200, "PETR4 entrou nos favoritos."))
        self.assertEqual(r["list"], [{"ticker": "PETR4", "market": "b3"}])
        self.assertEqual(call("POST", "/api/favoritos", body={"op": "add", "ticker": "PETR4"})[1]["message"],
                         "PETR4 já está nos favoritos.")
        self.assertEqual(call("POST", "/api/favoritos", body={"op": "add", "ticker": "!!"})[0], 400)
        self.assertEqual(call("POST", "/api/favoritos", body={"op": "x", "ticker": "VALE3"})[0], 400)
        with mock.patch.object(market_data, "history", side_effect=market_data.UpstreamError("sem dados")):
            status, r = call("POST", "/api/favoritos", body={"op": "add", "ticker": "XYZW3"})
        self.assertEqual(status, 400)
        self.assertIn("Sem cotações", r["message"])
        with mock.patch.object(fav, "MAX_ITEMS", 1):
            self.assertEqual(call("POST", "/api/favoritos", body={"op": "add", "ticker": "VALE3"})[0], 400)
        status, r = call("POST", "/api/favoritos", body={"op": "remove", "ticker": "PETR4", "market": "b3"})
        self.assertEqual((status, r["list"]), (200, []))
        self.assertEqual(call("POST", "/api/favoritos", body={"op": "remove", "ticker": "PETR4"})[0], 400)

    def test_rows_have_a_status_and_closed_positions_are_listed(self):
        pid = self.portfolio()
        for t in ("PETR4", "CSMG3"):
            call("POST", "/api/favoritos", body={"op": "add", "ticker": t})
        status, d = call("GET", "/api/favoritos", {"carteira": [pid]})
        self.assertEqual(status, 200)
        rows = {r["ticker"]: r for r in d["items"]}
        self.assertEqual(set(rows), {"PETR4", "CSMG3"})
        for r in rows.values():
            self.assertIn(r["status"]["key"], fav.STATUS_ORDER)
            self.assertTrue(r["status"]["reason"])
            self.assertIsNotNone(r["price"])
            self.assertEqual(set(r["mas"]), {"mm20", "mm50", "mm200"})
        self.assertTrue(rows["CSMG3"]["traded"])
        self.assertFalse(rows["CSMG3"]["held"])
        self.assertFalse(rows["PETR4"]["traded"])
        # CSMG3 foi comprada e vendida por inteiro: aparece em "Já passaram pela carteira".
        self.assertEqual([c["ticker"] for c in d["closed"]], ["CSMG3"])
        c = d["closed"][0]
        self.assertEqual((c["operations"], c["first"], c["last"], c["favorite"]), (2, "2021-06-01", "2021-09-01", True))

    def test_chart_for_any_asset(self):
        pid = self.portfolio()
        # Sem lançamentos: só o gráfico, sem marcadores, zoom no último ano.
        status, d = call("GET", f"/api/carteira/{pid}/grafico", {"ticker": ["PETR4"], "market": ["b3"]})
        self.assertEqual(status, 200)
        self.assertFalse(d["held"])
        self.assertEqual((d["markers"], d["targets"], d["closed"]), ([], [], None))
        self.assertTrue(d["cls"])
        # Já vendido por inteiro: compra e venda no gráfico e o resultado da posição encerrada.
        status, d = call("GET", f"/api/carteira/{pid}/grafico", {"ticker": ["CSMG3"]})
        self.assertEqual(status, 200)
        self.assertTrue(d["held"])
        self.assertEqual([m["kind"] for m in d["markers"]], ["buy", "sell"])
        self.assertEqual(d["closed"]["lastDate"], "2021-09-01")
        # Fora da Carteira (linha do ranking do Screener): com a carteira escolhida, ou sem nenhuma.
        status, d = call("GET", "/api/grafico", {"ticker": ["CSMG3"], "carteira": [pid]})
        self.assertEqual((status, len(d["markers"])), (200, 2))
        status, d = call("GET", "/api/grafico", {"ticker": ["VALE3"]})
        self.assertEqual((status, d["held"]), (200, False))
        self.assertEqual(call("GET", "/api/grafico", {"ticker": [""]})[0], 400)


class PressureTest(DatabaseTestCase):
    """A pressão do recuo fora do ranking (screener.pressure_info): favoritos e posições no setup."""

    def test_pressure_only_in_the_setup_and_like_the_ranking(self):
        from marketpressure.screener import screener
        from tests.support import candles
        n = next(n for n in range(262, 400) if screener.analyze_asset(candles(n))["setup"]["ok"])
        info = screener.pressure_info(candles(n), "acoes")
        self.assertAlmostEqual(info["pressure"], screener.analyze_asset(candles(n))["pressure"]["total"])
        self.assertEqual(info["bottle"], screener.bottle_state(info["pressure"]))
        self.assertEqual([p["key"] for p in info["pressureParts"]], ["volume", "momentum", "trend", "structure"])
        self.assertNotIn("volume", [p["key"] for p in screener.pressure_info(candles(n), "bdrs")["pressureParts"]])
        off = next(k for k in range(262, 400) if not screener.analyze_asset(candles(k))["setup"]["ok"])
        self.assertIsNone(screener.pressure_info(candles(off), "acoes"))
        self.assertIsNone(screener.pressure_info(candles(n)[:100], "acoes"))   # histórico curto
        self.assertIn("Sem pressão", screener.NO_PRESSURE)

    def test_carteira_positions_get_the_pressure_and_the_texts(self):
        payload = {"open": [{"ticker": "PETR4", "market": "b3"}]}
        info = {"pressure": 66.0, "bottle": "quase", "pressureParts": []}
        with mock.patch("marketpressure.screener.screener.pressure_info", return_value=info):
            out = fav.with_pressure(payload)
        self.assertEqual(out["open"][0]["bottle"], "quase")
        self.assertEqual(set(out["bottleStates"]), {"cheia", "quase", "enchendo"})
        self.assertTrue(out["noPressureText"])
        with mock.patch("marketpressure.screener.screener.pressure_info", return_value=None):
            self.assertNotIn("pressure", fav.with_pressure({"open": [{"ticker": "PETR4", "market": "b3"}]})["open"][0])
