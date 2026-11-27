"""Envio direto pelo Telegram (Bot API): servidor local e atualização diária sem o site publicado.

Na versão publicada o token do bot fica só no Worker (worker/notify.js), e quem envia é ele (ver
configuracoes/notificacoes.py, que escolhe o caminho). Aqui o token vem de config
(TELEGRAM_BOT_TOKEN, do ambiente ou do .env). As mensagens vão em texto puro (sem parse_mode),
para que um nome de ativo não vire formatação nem link. O token fica na URL da Bot API, por isso
ela nunca vai para o log. Falhar no envio nunca derruba quem chamou: deliver devolve (False, motivo).
"""

import json
import logging
import urllib.error
import urllib.request

from . import config

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
MAX_LENGTH = 4096   # limite de caracteres de uma mensagem do Telegram
_bot_name = {}      # token → @nome do bot (getMe), uma vez por processo


def configured():
    """Há token do bot?"""
    return bool(config.TELEGRAM_BOT_TOKEN)


def split(blocks, header="", footer=""):
    """Junta os blocos de texto (separados por linha em branco) em mensagens de até MAX_LENGTH
    caracteres, sem partir um bloco; o cabeçalho vai na primeira e o rodapé na última."""
    messages, current = [], header
    for block in [*blocks, footer] if footer else blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if current and len(candidate) > MAX_LENGTH:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return [m[:MAX_LENGTH] for m in messages]


def _call(method, body):
    """(ok, resultado, motivo) de um método da Bot API."""
    if not configured():
        return False, None, "bot não configurado"
    request = urllib.request.Request(API_URL.format(token=config.TELEGRAM_BOT_TOKEN, method=method), method="POST",
                                     data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=config.HTTP_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:   # a descrição do Telegram diz o motivo (ex.: "chat not found")
        try:
            description = json.loads(err.read().decode("utf-8")).get("description")
        except (ValueError, OSError):
            description = None
        return False, None, description or f"HTTP {err.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        return False, None, f"sem resposta do Telegram ({type(err).__name__})"
    if data.get("ok") is not True:
        return False, None, data.get("description") or "o Telegram não confirmou"
    return True, data.get("result"), None


def deliver(chat_id, text):
    """Envia uma mensagem de texto puro a um chat: (True, None) ou (False, motivo)."""
    ok, _, reason = _call("sendMessage", {"chat_id": chat_id, "text": text[:MAX_LENGTH],
                                          "disable_web_page_preview": True})
    if not ok:
        log.warning("Telegram não entregou a mensagem: %s", reason)
    return ok, reason


def bot_name():
    """O @nome do bot (sem o @), ou None."""
    token = config.TELEGRAM_BOT_TOKEN
    if token and token not in _bot_name:
        ok, result, reason = _call("getMe", {})
        if not ok:
            log.warning("não foi possível ler o nome do bot do Telegram: %s", reason)
            return None
        _bot_name[token] = (result or {}).get("username")
    return _bot_name.get(token)
