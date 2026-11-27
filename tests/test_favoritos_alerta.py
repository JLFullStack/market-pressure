"""Aviso dos favoritos pelo Telegram (carteira/favoritos_alerta.py): só a novidade de cada usuário,
um aviso por pregão, o estado gravado todo pregão, e o caminho publicado pelo Worker."""
import json
from unittest import mock

from marketpressure.carteira import favoritos as fav
from marketpressure.carteira import favoritos_alerta as fa
from marketpressure.comum import config, database, telegram
from marketpressure.configuracoes import notificacoes as nt
from marketpressure.screener import screener
from tests.support import DatabaseTestCase, candles, raw_history
from tests.test_ranking_alert import SATURDAY, WEDNESDAY, patch, remote


class FavoritesAlertTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patch(self, config, TELEGRAM_BOT_TOKEN="123:segredo", TELEGRAM_CHAT_ID="")
        nt.save({"enabled": False, "favorites": True, "chatId": "424242"})
        self.sent = []
        patcher = mock.patch.object(telegram, "deliver", side_effect=lambda chat, text: self.sent.append((chat, text)) or (True, None))
        patcher.start()
        self.addCleanup(patcher.stop)
        n = next(n for n in range(262, 400) if screener.analyze_asset(candles(n))["setup"]["ok"])
        universe = [{"ticker": "AAAA3", "name": "Empresa A", "sector": "Energia", "kind": "acoes"}]
        with mock.patch.object(screener, "load_universe", return_value=universe), \
                mock.patch.object(screener.market_data, "raw_history", lambda t, market="b3": raw_history(t, n=n)):
            self.scan = screener.run_scan()
        self.ref_date, self.ranked = fa.ranked_from(self.scan)
        self.assertEqual(self.ranked["AAAA3"]["advice"]["action"], "comprar")
        fav.change({"op": "add", "ticker": "AAAA3"})

    def test_first_alert_and_no_repeat(self):
        self.assertEqual(fa.notify(self.scan, "https://site", now=SATURDAY), "enviado a 1")
        self.assertEqual(len(self.sent), 1)
        chat, text = self.sent[0]
        self.assertEqual(chat, "424242")
        for part in ("seus favoritos no fechamento de", "• AAAA3: COMPRAR (1º do ranking, Score", "não considera as suas compras abertas", "https://site"):
            self.assertIn(part, text)
        state = database.read_table(nt.STATE_TABLE)
        self.assertEqual([(r["ticker"], r["status"], r["ref_date"]) for r in state], [("AAAA3", "comprar", self.ref_date)])
        # Mesmo pregão (a rodada das 08:07): nada de novo.
        self.assertEqual(fa.notify(self.scan, now=SATURDAY), "sem novidade")
        self.assertEqual(len(self.sent), 1)

    def test_same_status_is_not_repeated_but_the_state_moves_on(self):
        database.write_tables({nt.STATE_TABLE: [{"ticker": "AAAA3", "market": "b3", "status": "comprar", "ref_date": "2020-01-02"}]})
        self.assertEqual(fa.notify(self.scan, now=SATURDAY), "sem novidade")
        self.assertEqual(self.sent, [])
        self.assertEqual(database.read_table(nt.STATE_TABLE)[0]["ref_date"], self.ref_date)   # gravado mesmo sem aviso

    def test_news_only_on_entering_an_alert_status(self):
        row = lambda key: {"ticker": "X", "market": "b3", "status": {"key": key}}
        state = [{"ticker": "X", "market": "b3", "status": "perto"}]
        self.assertEqual(len(fa.news([row("comprar")], state)), 1)
        self.assertEqual(fa.news([row("perto")], state), [])
        self.assertEqual(fa.news([row("fora")], []), [])
        self.assertEqual(fa.news([row("travado")], [{"ticker": "X", "market": "b3", "status": "travado"}]), [])

    def test_off_session_and_settings(self):
        self.assertEqual(fa.notify(self.scan, now=WEDNESDAY), "pregão aberto")
        nt.save({"enabled": True, "favorites": False, "chatId": "424242"})
        self.assertEqual(fa.notify(self.scan, now=SATURDAY), "sem inscritos")
        with mock.patch.object(config, "TELEGRAM_BOT_TOKEN", ""):
            self.assertEqual(fa.notify(self.scan, now=SATURDAY), "não configurado")
        self.assertEqual(self.sent, [])

    def test_published_path_goes_through_the_worker(self):
        def answer(method, path, body):
            if path == "/notify/watchlists":
                return 200, {"configured": True, "users": [{"user": "u1", "list": [{"ticker": "AAAA3", "market": "b3"}], "state": []}]}
            return 200, {"ok": True}
        calls = remote(self, answer)
        self.assertEqual(fa.notify(self.scan, "https://site", now=SATURDAY), "enviado a 1")
        sent = [json.loads(body) for method, path, body in calls if path == "/notify/user"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["user"], "u1")
        self.assertIn("AAAA3: COMPRAR", sent[0]["text"])
        self.assertEqual(sent[0]["state"][0]["status"], "comprar")
        self.assertEqual(self.sent, [])   # nada sai direto: quem envia é o Worker
