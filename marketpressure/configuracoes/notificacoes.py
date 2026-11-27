"""Notificações pelo Telegram (aba Configurações): o chat de cada usuário, o teste e o envio.

O usuário informa o id do próprio chat no Telegram e liga os avisos; a atualização diária envia o
aviso do ranking (screener/ranking_alert.py) a todos os que o ligaram e o aviso dos favoritos
(carteira/favoritos_alerta.py) a cada um, sobre a lista dele. Tabela settings_telegram (uma linha:
enabled = aviso do ranking, favorites = aviso dos favoritos, chat_id; por usuário na versão
publicada).

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
DEFAULT = {"enabled": False, "favorites": False, "chatId": ""}
STATE_TABLE = "watchlist_alerts"   # o que já foi avisado de cada favorito (local; publicado, o Worker grava)
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
    favorites = body.get("favorites") is True
    if chat and not CHAT_ID.match(chat):
        return None, ("O id do chat tem só números (de 5 a 20 dígitos; o de um grupo começa com -). "
                      "Peça o seu ao @userinfobot no Telegram.")
    if (enabled or favorites) and not chat:
        return None, "Para receber os avisos, informe o id do seu chat no Telegram."
    return {"enabled": enabled, "favorites": favorites, "chatId": chat}, None


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
    return {"enabled": rows[0].get("enabled") is True and bool(chat),
            "favorites": rows[0].get("favorites") is True and bool(chat), "chatId": chat}


def save(s):
    db.write_tables({TABLE: [{"enabled": s["enabled"], "favorites": s.get("favorites") is True, "chat_id": s["chatId"] or None}]})
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
    elif s["enabled"] or s.get("favorites"):
        what = " e ".join(w for w, on in (("o aviso diário do ranking", s["enabled"]),
                                          ("o aviso dos seus favoritos", s.get("favorites"))) if on)
        text = f"Ligado: {what} vai para o chat {s['chatId']}."
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
    if lower in ("not found", "unauthorized"):
        # O Telegram responde assim quando o token do bot está errado, incompleto ou foi revogado.
        return ("O Telegram recusou o token do bot (está incompleto, errado ou foi revogado). "
                "Confira o token completo, no formato número:letras, e cadastre-o de novo.")
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


# region Aviso dos favoritos
def favorite_lists():
    """[{user, list: [{ticker, market}], state: [{ticker, market, status, ref_date}]}] de quem ligou o
    aviso dos favoritos, ou None quando não deu para saber. Publicado, o Worker devolve só as listas
    (nada de carteiras nem o chat); local, a lista e o estado daqui (user None)."""
    if runtime.REMOTE:
        status, data = _remote("GET", "/notify/watchlists")
        if status != 200:
            log.warning("o site não devolveu as listas de favoritos (HTTP %s): %s", status, data.get("message", ""))
            return None
        return data.get("users") or []
    s = load()
    if not s.get("favorites") and not config.TELEGRAM_CHAT_ID:
        return []
    try:
        items = [{"ticker": r["ticker"], "market": r.get("market") or "b3"} for r in db.read_table("watchlist") if r.get("ticker")]
        state = db.read_table(STATE_TABLE)
    except Exception:
        log.warning("favoritos locais indisponíveis para o aviso", exc_info=True)
        return None
    return [{"user": None, "list": items, "state": state}] if items else []


def deliver_user(user, text, state):
    """Envia o aviso dos favoritos a um usuário e, se chegou, grava o estado: True/False. Sem texto,
    só grava o estado (dia sem novidade). Publicado, o Worker envia ao chat salvo do usuário e grava
    o estado na tabela dele; local, envio direto ao chat salvo aqui (e ao TELEGRAM_CHAT_ID do .env)."""
    if runtime.REMOTE:
        status, data = _remote("POST", "/notify/user", json.dumps({"user": user, "text": text[:telegram.MAX_LENGTH], "state": state}))
        if status != 200:
            log.warning("aviso dos favoritos não enviado a um usuário (HTTP %s): %s", status, data.get("description", ""))
        return status == 200
    if text:
        s = load()
        chats = [s["chatId"]] if s.get("favorites") and s["chatId"] else []
        if config.TELEGRAM_CHAT_ID and config.TELEGRAM_CHAT_ID not in chats:
            chats.append(config.TELEGRAM_CHAT_ID)
        if not any([telegram.deliver(chat, text)[0] for chat in chats]):
            return False
    db.write_tables({STATE_TABLE: state})
    return True

# endregion
