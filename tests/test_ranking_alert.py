"""Notificações pelo Telegram: o aviso do ranking (screener/ranking_alert.py), a configuração de cada
usuário com o teste (configuracoes/notificacoes.py) e o envio direto (comum/telegram.py)."""

import io
import json
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest import mock

from marketpressure import api
from marketpressure.comum import config, database, runtime, telegram
from marketpressure.configuracoes import notificacoes as nt
from marketpressure.screener import ranking_alert as ra
from marketpressure.screener import screener
from tests.support import DatabaseTestCase, candles, raw_history

SATURDAY = datetime(2026, 11, 28, 12, tzinfo=timezone.utc).timestamp()     # sem pregão
WEDNESDAY = datetime(2026, 11, 25, 15, tzinfo=timezone.utc).timestamp()    # 12:00 em Brasília, pregão aberto


def patch(test, target, **values):
    patcher = mock.patch.multiple(target, **values)
    patcher.start()
    test.addCleanup(patcher.stop)


def remote(test, answer):
    """Simula o site publicado: runtime.REMOTE, um /db em memória e as rotas /notify do Worker
    respondendo answer(method, path, body); devolve os pedidos feitos ao /notify."""
    calls, tables = [], {}

    def request(method, path, body=None):
        if path.startswith("/db"):
            if method == "PUT":
                tables.update(json.loads(body)["tables"])
                return 200, '{"ok": true}'
            names = path.split("names=")[1].split("%2C")
            return 200, "{" + ",".join(f'"{n}":{tables.get(n, "null")}' for n in names) + "}"
        calls.append((method, path, body))
        status, data = answer(method, path, body)
        return status, json.dumps(data)
    patch(test, runtime, REMOTE=True, http_request=request)
    return calls


class NotifyTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patch(self, config, TELEGRAM_BOT_TOKEN="123:segredo", TELEGRAM_CHAT_ID="")
        nt.save({"enabled": True, "chatId": "424242"})
        self.sent = []
        patcher = mock.patch.object(telegram, "deliver", side_effect=lambda chat, text: self.sent.append((chat, text)) or (True, None))
        patcher.start()
        self.addCleanup(patcher.stop)
        n = next(n for n in range(262, 400) if screener.analyze_asset(candles(n))["setup"]["ok"])
        universe = [{"ticker": "AAAA3", "name": "Empresa A", "sector": "Energia", "kind": "acoes"}]
        with mock.patch.object(screener, "load_universe", return_value=universe), \
                mock.patch.object(screener.market_data, "raw_history", lambda t, market="b3": raw_history(t, n=n)):
            self.scan = screener.run_scan()
        self.ref_date, kinds = ra.current(ra.advised_payload(self.scan))
        self.assertEqual([a["ticker"] for _, a in kinds["acoes"]["rows"]], ["AAAA3"])   # COMPRAR no Top

    def test_first_alert_lists_the_buys_and_saves_the_state(self):
        self.assertEqual(ra.notify(self.scan, "https://site", now=SATURDAY), "enviado")
        self.assertEqual(len(self.sent), 1)
        chat, text = self.sent[0]
        self.assertEqual(chat, "424242")
        for part in ("ranking do fechamento de", "primeiro aviso", "Ações: regime", "Entraram com COMPRAR:",
                     "• AAAA3 (Empresa A): 1º, Score", "Com COMPRAR no Top 20: 1", "sem considerar a sua carteira",
                     "https://site"):
            self.assertIn(part, text)
        state = {r["kind"]: r for r in database.read_table(ra.TABLE)}
        self.assertEqual(state["acoes"]["tickers"], "AAAA3")
        self.assertEqual(state["acoes"]["ref_date"], self.ref_date)

    def test_same_session_is_not_sent_twice(self):
        ra.notify(self.scan, now=SATURDAY)
        self.assertEqual(ra.notify(self.scan, now=SATURDAY), "já avisado")
        self.assertEqual(len(self.sent), 1)

    def test_nothing_new_sends_nothing_but_moves_the_state(self):
        _, kinds = ra.current(ra.advised_payload(self.scan))
        database.write_tables({ra.TABLE: ra.state_rows("2000-01-03", kinds)})
        self.assertEqual(ra.notify(self.scan, now=SATURDAY), "sem novidade")
        self.assertEqual(self.sent, [])
        self.assertEqual({r["ref_date"] for r in database.read_table(ra.TABLE)}, {self.ref_date})

    def test_session_hours_and_missing_bot_send_nothing(self):
        self.assertEqual(ra.notify(self.scan, now=WEDNESDAY), "pregão aberto")
        with mock.patch.object(config, "TELEGRAM_BOT_TOKEN", ""):
            self.assertEqual(ra.notify(self.scan, now=SATURDAY), "não configurado")
        self.assertEqual(self.sent, [])

    def test_nobody_subscribed_or_failure_keeps_the_state_for_the_next_try(self):
        nt.save({"enabled": False, "chatId": "424242"})
        self.assertEqual(ra.notify(self.scan, now=SATURDAY), "sem destinatários")
        nt.save({"enabled": True, "chatId": "424242"})
        with mock.patch.object(telegram, "deliver", return_value=(False, "chat not found")):
            self.assertEqual(ra.notify(self.scan, now=SATURDAY), "falhou")
        self.assertEqual(database.read_table(ra.TABLE), [])
        self.assertEqual(ra.notify(self.scan, now=SATURDAY), "enviado")

    def test_published_version_asks_the_worker_to_send(self):
        calls = remote(self, lambda method, path, body: (200, {"configured": True, "recipients": 2, "sent": 2}))
        self.assertEqual(ra.notify(self.scan, now=SATURDAY), "enviado")
        method, path, body = calls[0]
        self.assertEqual((method, path), ("POST", "/notify/send"))
        self.assertIn("AAAA3", json.loads(body)["text"])
        self.assertEqual(self.sent, [])                    # nada sai direto: o token é do Worker


class BlocksTest(unittest.TestCase):
    @staticmethod
    def row(ticker, priority=False):
        return {"ticker": ticker, "name": ticker, "score": 61.25, "setup": {"ma": {"name": "MM50"}},
                "advice": {"action": "comprar", "priority": priority}}

    def test_only_new_buys_and_regime_changes(self):
        kinds = {"acoes": {"regime": "alta", "rows": [(1, self.row("AAAA3", True)), (4, self.row("BBBB4"))]},
                 "fundos": {"regime": "misto", "rows": [(2, self.row("CCCC11"))]},
                 "etfs": {"regime": "defensivo", "rows": []}}
        previous = {"acoes": {"regime": "alta", "tickers": "BBBB4"},
                    "fundos": {"regime": "misto", "tickers": "CCCC11"},
                    "etfs": {"regime": "misto", "tickers": ""}}
        found = ra.blocks(kinds, previous, 20)
        self.assertEqual(len(found), 2)
        self.assertIn("• AAAA3: 1º, Score 61,3, pullback na MM50, prioridade", found[0])
        self.assertNotIn("BBBB4", found[0])                                # já avisado, continua no Top
        self.assertEqual(found[1], "ETFs de ações: regime defensivo (antes misto)\nCom COMPRAR no Top 20: 0")


class SettingsTest(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patch(self, config, TELEGRAM_BOT_TOKEN="123:segredo", TELEGRAM_CHAT_ID="")
        patcher = mock.patch.object(telegram, "bot_name", return_value="MarketPressureBot")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_form_validation(self):
        self.assertEqual(nt.from_form({"enabled": True, "chatId": " 123 456 789 "}), ({"enabled": True, "favorites": False, "chatId": "123456789"}, None))
        self.assertEqual(nt.from_form({"favorites": True, "chatId": "123456789"})[0]["favorites"], True)
        self.assertIsNone(nt.from_form({"favorites": True, "chatId": ""})[0])   # o aviso dos favoritos também precisa do chat
        self.assertEqual(nt.from_form({"enabled": False, "chatId": "-1001234567890"})[0]["chatId"], "-1001234567890")
        for body in ({"enabled": True, "chatId": ""}, {"chatId": "@fulano"}, {"chatId": "12"}, {"chatId": "1" * 21}):
            s, error = nt.from_form(body)
            self.assertIsNone(s)
            self.assertTrue(error)

    def test_save_and_payload(self):
        self.assertEqual(nt.load(), nt.DEFAULT)
        nt.save({"enabled": True, "chatId": "123456789"})
        data = nt.payload()
        self.assertEqual(data["telegram"], {"enabled": True, "favorites": False, "chatId": "123456789"})
        self.assertEqual(data["bot"], {"configured": True, "name": "@MarketPressureBot", "link": "https://t.me/MarketPressureBot"})
        self.assertIn("123456789", data["text"])
        with mock.patch.object(config, "TELEGRAM_BOT_TOKEN", ""):
            self.assertEqual(nt.payload()["text"], nt.NO_BOT)

    def test_test_button_saves_and_sends_the_standard_message(self):
        with mock.patch.object(telegram, "deliver", return_value=(True, None)) as deliver:
            status, data = api.call("POST", "/api/configuracoes/telegram/teste", "",
                                    lambda: {"enabled": True, "chatId": "123456789"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        deliver.assert_called_once_with("123456789", nt.TEST_TEXT)
        self.assertEqual(nt.load()["chatId"], "123456789")

    def test_test_failure_explains_what_to_do(self):
        with mock.patch.object(telegram, "deliver", return_value=(False, "Bad Request: chat not found")):
            status, data = api.call("POST", "/api/configuracoes/telegram/teste", "",
                                    lambda: {"enabled": True, "chatId": "123456789"})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("@MarketPressureBot", data["message"])
        self.assertIn("Iniciar", data["message"])
        status, data = api.call("POST", "/api/configuracoes/telegram", "", lambda: {"enabled": True, "chatId": "abc"})
        self.assertEqual(status, 400)
        # Token errado ou revogado: o Telegram responde "Not Found"/"Unauthorized".
        for reason in ("Not Found", "Unauthorized"):
            self.assertIn("token do bot", nt._explain(reason, None))
        self.assertIn("Iniciar", nt._explain("Bad Request: chat not found", None))

    def test_published_test_goes_to_the_worker_without_the_chat(self):
        s = {"enabled": True, "chatId": "123456789"}
        calls = remote(self, lambda method, path, body: (200, {"ok": True}) if path == "/notify/test"
                       else (200, {"configured": True, "bot": "MarketPressureBot"}))
        self.assertIsNone(nt.send_test(s))
        self.assertEqual(calls[0][:2], ("POST", "/notify/test"))
        self.assertNotIn("123456789", calls[0][2])         # o Worker usa o chat salvo, não o do pedido
        calls = remote(self, lambda method, path, body: (429, {"ok": False, "description": "limite de 5 testes a cada 15 minutos"})
                       if path == "/notify/test" else (200, {"configured": True, "bot": None}))
        self.assertIn("Muitos testes", nt.send_test(s))
        self.assertEqual(nt.payload()["bot"]["configured"], True)

    def test_local_broadcast_adds_the_env_chat_once(self):
        nt.save({"enabled": True, "chatId": "111111"})
        with mock.patch.object(config, "TELEGRAM_CHAT_ID", "222222"), \
                mock.patch.object(telegram, "deliver", return_value=(True, None)) as deliver:
            self.assertEqual(nt.broadcast("oi"), (2, 2))
        self.assertEqual([c[0][0] for c in deliver.call_args_list], ["111111", "222222"])


class TelegramTest(unittest.TestCase):
    def setUp(self):
        patch(self, config, TELEGRAM_BOT_TOKEN="123:segredo")

    def test_split_keeps_blocks_whole_and_under_the_limit(self):
        blocks = ["a" * 3000, "b" * 3000, "c" * 100]
        messages = telegram.split(blocks, "cabeçalho", "rodapé")
        self.assertEqual(len(messages), 2)
        self.assertTrue(messages[0].startswith("cabeçalho\n\naaa"))
        self.assertTrue(messages[1].endswith("ccc\n\nrodapé"))
        self.assertTrue(all(len(m) <= telegram.MAX_LENGTH for m in messages))

    def test_deliver_posts_plain_text(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(telegram.deliver("42", "PETR4 <b>"), (True, None))
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.telegram.org/bot123:segredo/sendMessage")
        body = json.loads(request.data)
        self.assertEqual((body["chat_id"], body["text"]), ("42", "PETR4 <b>"))
        self.assertNotIn("parse_mode", body)

    def test_refused_message_is_logged_without_the_token(self):
        error = urllib.error.HTTPError("https://api.telegram.org/bot123:segredo/sendMessage", 400, "Bad Request", {},
                                       io.BytesIO(b'{"ok": false, "description": "Bad Request: chat not found"}'))
        with mock.patch("urllib.request.urlopen", side_effect=error), \
                self.assertLogs("marketpressure.comum.telegram", "WARNING") as logs:
            self.assertEqual(telegram.deliver("42", "oi"), (False, "Bad Request: chat not found"))
        self.assertIn("chat not found", logs.output[0])
        self.assertNotIn("segredo", "".join(logs.output))

    def test_network_failure_returns_false(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("sem rede")), \
                self.assertLogs("marketpressure.comum.telegram", "WARNING"):
            ok, reason = telegram.deliver("42", "oi")
        self.assertFalse(ok)
        self.assertIn("sem resposta", reason)


if __name__ == "__main__":
    unittest.main()
