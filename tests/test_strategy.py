"""Estratégia de venda por tipo de ativo (aba Configurações) e a indicação do ranking do Screener."""

import unittest

from marketpressure import api, database as db, screener
from marketpressure import strategy as st

from .support import DatabaseTestCase

PARTIAL = {"key": "parcial", "target": 0.1, "fraction": 0.5}
REVERSAL = {"key": "reversao", "target": 0.1, "fraction": 0.5}


class StrategyTest(DatabaseTestCase):
    def test_default_until_saved(self):
        self.assertEqual(st.load(), st.DEFAULT)
        self.assertEqual(st.DEFAULT["kinds"]["internacionais"], "reversao")   # internacionais: só a reversão
        self.assertEqual(st.DEFAULT["kinds"]["acoes"], "parcial")
        saved, error = st.from_form({"target": "15,5", "fraction": "30", "kinds": {"etfs": "reversao"}})
        self.assertIsNone(error)
        st.save(saved)
        loaded = st.load()
        self.assertEqual((loaded["target"], loaded["fraction"]), (0.155, 0.3))
        self.assertEqual(loaded["kinds"]["etfs"], "reversao")
        self.assertEqual(st.for_group(loaded, "acoes"), {"key": "parcial", "target": 0.155, "fraction": 0.3, "group": "acoes"})
        acoes = next(k for k in st.describe(loaded)["kinds"] if k["group"] == "acoes")
        self.assertEqual(acoes["text"], "Vende 30% de cada compra ao atingir +15,5% sobre o preço dela; "
                                        "o restante na reversão de tendência.")

    def test_legacy_single_strategy_applies_to_every_group(self):
        db.write_tables({"settings": [{"strategy": "reversao", "partial_target": 0.1, "partial_fraction": 0.5}]})
        self.assertEqual(set(st.load()["kinds"].values()), {"reversao"})

    def test_groups(self):
        self.assertEqual(list(st.GROUPS.values()), ["Ações", "Fundos imobiliários", "ETFs Brasil", "Internacionais"])
        self.assertEqual(st.group_of("bdrs", "b3"), "internacionais")
        self.assertEqual(st.group_of(None, "b3"), "acoes")
        self.assertEqual(st.group_of("acoes", "us"), "internacionais")
        self.assertEqual(st.group_of("etfs", "b3", "ISHARES S&P 500 CLASSE DE ÍNDICE"), "internacionais")
        self.assertEqual(st.group_of("etfs", "b3", "ISHARES IBOVESPA CLASSE DE ÍNDICE"), "etfs")
        rule = st.asset_rules(st.DEFAULT)
        self.assertEqual(rule("AAPL34", "b3")["key"], "reversao")   # BDR pelo código
        self.assertEqual(rule("VOO", "us")["key"], "reversao")
        self.assertEqual(rule("PETR4", "b3")["key"], "parcial")
        by_class = st.asset_rules(st.DEFAULT, {"PETR4": "Internacionais"})   # na Carteira vale a classe
        self.assertEqual(by_class("PETR4", "b3")["key"], "reversao")

    def test_old_groups_are_migrated(self):
        db.write_tables({"settings": [{"strategy": "parcial", "partial_target": 0.1, "partial_fraction": 0.5}],
                         "settings_kinds": [{"kind": "bdrs", "strategy": "reversao"}]})
        self.assertEqual(st.load()["kinds"]["internacionais"], "reversao")

    def test_validation(self):
        for body in ({"target": 10, "fraction": 50, "kinds": {"acoes": "x"}},
                     {"target": 10, "fraction": 50, "kinds": {"outro": "parcial"}},
                     {"target": 0, "fraction": 50},
                     {"target": 10, "fraction": 0},
                     {"target": "abc", "fraction": 50}):
            self.assertIsNotNone(st.from_form(body)[1], body)

    def test_routes(self):
        body = {"target": 10, "fraction": 50, "kinds": {"acoes": "reversao"}}
        status, data = api.call("POST", "/api/configuracoes", {}, lambda: body)
        self.assertEqual(status, 200)
        got = api.call("GET", "/api/configuracoes", {}, lambda: None)[1]["strategy"]["kinds"]
        self.assertEqual(next(k for k in got if k["group"] == "acoes")["key"], "reversao")
        status, data = api.call("POST", "/api/configuracoes", {}, lambda: {"target": 900, "fraction": 50})
        self.assertEqual(status, 400)

    def test_portfolio_follows_the_rule_of_the_asset_type(self):
        def act(body):
            status, data = api.call("POST", "/api/carteira/acao", {}, lambda: body)
            self.assertEqual(status, 200, data)
            return data
        pid = act({"action": "criar_carteira", "name": "Teste"})["id"]
        act({"action": "nova_operacao", "id": pid, "ticker": "PETR4", "market": "b3", "cls": "Ações", "type": "compra",
             "date": "2021-06-01", "quantity": 10, "price": 100, "fees": 0, "fx": 1})

        def signal():
            status, body = api.call("GET", f"/api/carteira/{pid}/analise", {}, lambda: None)
            self.assertEqual(status, 200, body)
            return body["open"][0]
        first = signal()
        self.assertIn(first["status"], ("COMPRAR", "VENDER", "VENDER 50%", "MANTER"))
        self.assertTrue(first["statusReason"])
        api.call("POST", "/api/configuracoes", {}, lambda: {"target": 10, "fraction": 50, "kinds": {"acoes": "reversao"}})
        self.assertNotEqual(signal()["statusKey"], "partial")
        self.assertIsNone(signal()["partial"])


class RuleByKindTest(DatabaseTestCase):
    def test_ranking_uses_the_backtest_of_the_rule_of_each_type(self):
        a = {"ticker": "AAAA3", "kind": "acoes", "setup": {"ok": True, "okStrict": False}, "pressure": {"total": 50.0},
             "backtest": {"loose": {"winRate": 40.0}, "looseRev": {"winRate": 80.0}}}
        self.assertEqual(screener.build_ranking([a], False)[0]["score"], 50 * 0.6 + 40 * 0.4)
        self.assertEqual(screener.build_ranking([a], False, {"acoes": "reversao"})[0]["score"], 50 * 0.6 + 80 * 0.4)

    def test_backtests_have_both_rules(self):
        from .support import candles
        bts = screener.backtests(screener.compute_indicators(candles(400)))
        self.assertEqual(set(bts), {"loose", "strict", "looseRev", "strictRev"})
        self.assertEqual(bts["loose"]["trades"], bts["looseRev"]["trades"])   # mesmas compras e saídas

    def test_portfolio_backtest_follows_the_configured_rules(self):
        from marketpressure import portfolio_backtest as pb
        p = pb.parse_params({"universe": "acoes", "saida": "config"})
        self.assertEqual(p["rules"]["internacionais"][1], 0.0)    # padrão dos internacionais: só a reversão
        self.assertEqual(p["rules"]["acoes"][1], 0.5)
        self.assertNotIn("rules", pb.parse_params({"universe": "acoes", "saida": "manual"}))
        with self.assertRaises(ValueError):
            pb.parse_params({"universe": "acoes", "saida": "x"})


class AdviceTest(unittest.TestCase):
    ROW = {"ticker": "PETR4", "price": 50.0, "score": 60.0, "pressure": 65.0,
           "setup": {"ma": {"name": "MM20", "dist": 0.012}},
           "bt": {"wins": 6, "trades": 8, "winRate": 66.0, "avgReturn": 4.2,
                  "recentTrades": 3, "recentWins": 2, "recentReturn": 3.0}}

    def test_buy_with_exit_plan(self):
        a = screener.advice(self.ROW, None, PARTIAL)
        self.assertEqual(a["action"], "comprar")
        self.assertIn("vender 50% em +10% (R$ 55,00)", a["reason"])
        a = screener.advice(self.ROW, None, REVERSAL)
        self.assertIn("vender tudo na reversão", a["reason"])

    def test_wait_when_already_bought_in_the_region(self):
        lots = [{"date": "2026-09-28", "price": 49.0}]
        a = screener.advice(self.ROW, lots, PARTIAL)
        self.assertEqual(a["action"], "aguardar")
        self.assertIn("28/09/2026", a["reason"])
        far = screener.advice(self.ROW, [{"date": "2026-09-28", "price": 60.0}], PARTIAL)
        self.assertEqual(far["action"], "comprar")
        self.assertIn("Já na carteira", far["reason"])

    def test_wait_when_the_trend_is_weak_except_internationals(self):
        firm = {**self.ROW, "mm200Slope": 0.03, "return12m": 0.2}
        self.assertEqual(screener.advice(firm, None, PARTIAL, "acoes")["action"], "comprar")
        for weak in ({"mm200Slope": 0.004}, {"return12m": -0.05}, {"setup": {"ma": {"name": "MM200", "dist": 0.01}}}):
            row = {**firm, **weak}
            a = screener.advice(row, None, PARTIAL, "acoes")
            self.assertEqual(a["action"], "aguardar", weak)
            self.assertIn("tendência está fraca", a["reason"])
            self.assertEqual(screener.advice(row, None, PARTIAL, "internacionais")["action"], "comprar")

    def test_wait_when_weaker_than_the_index(self):
        row = {**self.ROW, "mm200Slope": 0.03, "return12m": 0.2, "return6m": 0.02, "benchmark6m": 0.08}
        a = screener.advice(row, None, PARTIAL, "acoes")
        self.assertEqual(a["action"], "aguardar")
        self.assertIn("abaixo dos 8,0% do Ibovespa", a["reason"])
        self.assertEqual(screener.advice(row, None, PARTIAL, "internacionais")["action"], "comprar")
        stronger = {**row, "return6m": 0.1}
        self.assertEqual(screener.advice(stronger, None, PARTIAL, "acoes")["action"], "comprar")

    def test_priority_for_the_top_5_or_a_high_score(self):
        firm = {**self.ROW, "mm200Slope": 0.03, "return12m": 0.2}
        self.assertEqual(screener.advice(firm, None, PARTIAL, "acoes", 6)["action"], "comprar")
        top = screener.advice(firm, None, PARTIAL, "acoes", 3)
        self.assertEqual((top["action"], top["label"], top.get("priority")), ("comprar", "COMPRAR", True))
        self.assertIn("3º do ranking", top["reason"])
        self.assertTrue(screener.advice({**firm, "score": 66.0}, None, PARTIAL, "acoes", 12).get("priority"))
        weak = {**firm, "return12m": -0.1}   # tendência fraca: AGUARDAR mesmo no Top 5
        self.assertEqual(screener.advice(weak, None, PARTIAL, "acoes", 1)["action"], "aguardar")

    def test_history_never_blocks_only_warns(self):
        old_loss = {**self.ROW, "bt": {**self.ROW["bt"], "avgReturn": -1.4}}   # 10 anos negativo
        self.assertEqual(screener.advice(old_loss, None, PARTIAL, "acoes")["action"], "comprar")
        recent = {**self.ROW, "bt": {**self.ROW["bt"], "recentTrades": 2, "recentWins": 0, "recentReturn": -2.5}}
        a = screener.advice(recent, None, PARTIAL, "acoes")
        self.assertEqual(a["action"], "comprar")
        self.assertIn("Atenção: nos últimos 3 anos", a["reason"])
        self.assertNotIn("Atenção", screener.advice(recent, None, PARTIAL, "internacionais")["reason"])   # não prevê
        few = {**self.ROW, "bt": {**self.ROW["bt"], "recentTrades": 1, "recentWins": 0, "recentReturn": -2.5}}
        self.assertNotIn("Atenção", screener.advice(few, None, PARTIAL, "acoes")["reason"])


if __name__ == "__main__":
    unittest.main()
