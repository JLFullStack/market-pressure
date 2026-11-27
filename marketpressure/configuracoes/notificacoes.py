"""Notificações pelo Telegram (aba Configurações): o chat de cada usuário, o teste e o envio.

O usuário informa o id do próprio chat no Telegram e liga o aviso; a atualização diária
(screener/ranking_alert.py) envia o aviso do ranking a todos os que o ligaram. Tabela
settings_telegram (uma linha: enabled, chat_id; por usuário na versão publicada).

Dois caminhos, porque o token do bot é segredo:
- publicado (runtime.REMOTE: navegador ou a atualização com o token de serviço): quem envia é o
  Worker (worker/notify.js), que guarda o token. O teste vai para o chat SALVO do usuário logado
  (o navegador não escolhe o destino) e o envio diário, para todos os chats salvos com o aviso ligado;
- local: o token vem do .env (config.TELEGRAM_BOT_TOKEN) e o envio é direto (comum/telegram.py),
  para o chat salvo aqui e, se definido, o TELEGRAM_CHAT_ID do .env.
"""

import json
import logging
import re

from ..comum import config, runtime, telegram
from ..comum import database as db

log = logging.getLogger(__name__)

TABLE = "settings_telegram"
DEFAULT = {"enabled": False, "chatId": ""}
CHAT_ID = re.compile(r"^-?\d{5,20}$")           # o mesmo de worker/notify.js
HELPER_BOT = "https://t.me/userinfobot"         # responde com o id de quem fala com ele
TEST_TEXT = ("MarketPressure: mensagem de teste. Se ela chegou, as notificações pelo Telegram estão "
             "configuradas para a sua conta.")   # o mesmo de worker/notify.js
NO_BOT = ("O bot do Telegram ainda não foi configurado no servidor (segredo TELEGRAM_BOT_TOKEN). "
          "A sua escolha fica salva e passa a valer quando ele estiver.")


# region Configuração
def from_form(body):
    """Formulário → (configuração, erro). chatId: só números (grupos começam com "-")."""
    chat = re.sub(r"\s", "", str(body.get("chatId") or ""))
    enabled = body.get("enabled") is True
    if chat and not CHAT_ID.match(chat):
        return None, ("O id do chat tem só números (de 5 a 20 dígitos; o de um grupo começa com -). "
                      "Peça o seu ao @userinfobot no Telegram.")
    if enabled and not chat:
        return None, "Para receber os avisos, informe o id do seu chat no Telegram."
    return {"enabled": enabled, "chatId": chat}, None


def load():
    try:
        rows = db.read_table(TABLE)
    except Exception:
        log.warning("não foi possível ler a configuração do Telegram", exc_info=True)
        return dict(DEFAULT)
    if not rows:
        return dict(DEFAULT)
    chat = str(rows[0].get("chat_id") or "")
    chat = chat if CHAT_ID.match(chat) else ""
    return {"enabled": rows[0].get("enabled") is True and bool(chat), "chatId": chat}


def save(s):
    db.write_tables({TABLE: [{"enabled": s["enabled"], "chat_id": s["chatId"] or None}]})
    return s

# endregion


# region Bot
def _remote(method, path, body=None):
    """(status, JSON) de uma rota /notify do Worker; status 0 = sem resposta."""
    status, text = runtime.http_request(method, path, body)
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def bot_info():
    """{configured (None = não deu para saber), name}: se há bot e o @nome dele."""
    if runtime.REMOTE:
        status, data = _remote("GET", "/notify/info")
        if status != 200:
            log.warning("não foi possível consultar o bot do Telegram (HTTP %s)", status)
            return {"configured": None, "name": None}
        return {"configured": data.get("configured") is True, "name": data.get("bot")}
    return {"configured": telegram.configured(), "name": telegram.bot_name() if telegram.configured() else None}


def payload(s=None):
    """O que a aba Configurações mostra."""
    s = s or load()
    bot = bot_info()
    name = bot["name"]
    if bot["configured"] is False:
        text = NO_BOT
    elif s["enabled"]:
        text = f"Ligado: o aviso diário do ranking vai para o chat {s['chatId']}."
    else:
        text = "Desligado: nenhuma mensagem é enviada a você."
    return {"telegram": s, "text": text, "helperBot": HELPER_BOT,
            "bot": {"configured": bot["configured"], "name": f"@{name}" if name else None,
                    "link": f"https://t.me/{name}" if name else None}}

# endregion


# region Teste
def _explain(reason, bot_name):
    """Motivo do Telegram (ou do Worker) → mensagem para o usuário."""
    reason = reason or ""
    start = f"abra o {bot_name} no Telegram" if bot_name else "abra o bot no Telegram"
    lower = reason.lower()
    if "chat not found" in lower:
        return f"O Telegram não encontrou esse chat: {start}, toque em Iniciar (/start) e confira o id."
    if "blocked" in lower:
        return f"O bot está bloqueado no seu Telegram: desbloqueie-o, {start} e toque em Iniciar."
    if "forbidden" in lower or "can't initiate" in lower:
        return f"O bot ainda não pode falar com você: {start} e toque em Iniciar (/start)."
    if "bot não configurado" in lower:
        return NO_BOT
    if "nenhum chat salvo" in lower:
        return "Salve o id do seu chat antes de enviar o teste."
    if "limite" in lower:
        return f"Muitos testes seguidos ({reason}). Tente de novo daqui a pouco."
    return f"O Telegram não entregou a mensagem ({reason or 'sem motivo informado'})."


def send_test(s):
    """Envia a mensagem de teste ao chat salvo: None se chegou, senão a mensagem do problema."""
    if not s["chatId"]:
        return "Informe o id do seu chat no Telegram antes de enviar o teste."
    if runtime.REMOTE:
        status, data = _remote("POST", "/notify/test", "{}")
        if status == 200 and data.get("ok") is True:
            return None
        if status == 0:
            return "Sem resposta do servidor; tente de novo."
        reason = data.get("description") or data.get("message") or f"HTTP {status}"
        name = bot_info()["name"]
        return _explain(reason, f"@{name}" if name else None)
    if not telegram.configured():
        return NO_BOT
    ok, reason = telegram.deliver(s["chatId"], TEST_TEXT)
    if ok:
        return None
    name = telegram.bot_name()
    return _explain(reason, f"@{name}" if name else None)

# endregion


# region Envio diário
def available():
    """Dá para enviar? Publicado, quem sabe é o Worker (broadcast diz quantos receberam)."""
    return runtime.REMOTE or telegram.configured()


def _local_chats():
    s = load()
    chats = [s["chatId"]] if s["enabled"] else []
    if config.TELEGRAM_CHAT_ID and config.TELEGRAM_CHAT_ID not in chats:
        chats.append(config.TELEGRAM_CHAT_ID)
    return chats


def broadcast(text):
    """Envia o texto a todos que ligaram o aviso: (destinatários, entregues); destinatários None
    quando não deu para enviar (Worker fora do ar, recusa)."""
    if runtime.REMOTE:
        status, data = _remote("POST", "/notify/send", json.dumps({"text": text[:telegram.MAX_LENGTH]}))
        if status != 200:
            log.warning("o site não enviou o aviso pelo Telegram (HTTP %s): %s", status, data.get("message", ""))
            return None, 0
        if data.get("configured") is False:
            log.info("bot do Telegram não configurado no Worker (segredo TELEGRAM_BOT_TOKEN)")
        return data.get("recipients", 0), data.get("sent", 0)
    chats = _local_chats()
    return len(chats), sum(1 for chat in chats if telegram.deliver(chat, text)[0])

# endregion
