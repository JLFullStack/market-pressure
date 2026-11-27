"""Aviso diário do ranking pelo Telegram, enviado pela atualização (update.py) depois do fechamento.

Lê o ranking da varredura na visão padrão da página (base "fechamento", sem exigir o candle de
rejeição, liquidez padrão, estratégia de venda padrão de cada tipo, strategy.DEFAULT) com a
Indicação sem carteira (a atualização não lê as carteiras, que são de cada usuário) e avisa, por
tipo de ativo, o que mudou desde o último aviso:

- os ativos que entraram no Top com COMPRAR (com o lugar, o Score, a média testada e se é
  prioridade); quem continua no Top com COMPRAR não é repetido;
- a mudança de regime do tipo (alta, misto, defensivo).

Vai para todos os usuários que ligaram o aviso na aba Configurações (configuracoes/notificacoes.py;
publicado, quem envia é o Worker, que guarda o token do bot). Sem novidade, nada é enviado.
Um aviso por pregão fechado: o que já foi avisado fica na tabela
comum ranking_alerts (uma linha por tipo: o pregão, o regime e os tickers com COMPRAR), gravada só
depois de o aviso chegar a alguém, então uma falha do Telegram (ou ninguém inscrito ainda) é
tentada de novo na próxima atualização; quem se inscreve depois recebe as novidades seguintes. Durante o
pregão nada é avisado (a varredura tem o candle em andamento), e a rodada das 08:07 não repete a
das 18:30. A mensagem é informativa: o Screener e a Carteira continuam sendo a referência.
"""

import logging
import time

from ..comum import database as db
from ..comum import telegram
from ..configuracoes import notificacoes
from ..comum.core import fmt_date, fmt_num, in_session_hours
from ..configuracoes import strategy as st
from . import screener

log = logging.getLogger(__name__)

TABLE = "ranking_alerts"
MODE = "fechamento"
LIQUIDITY = "padrao"
KIND_TITLE = {"acoes": "Ações", "fundos": "Fundos listados", "etfs": "ETFs de ações", "bdrs": "BDRs"}


def advised_payload(scan):
    """O ranking da visão padrão com a Indicação sem carteira e a estratégia padrão."""
    settings = st.DEFAULT
    payload = screener.scan_payload(scan, LIQUIDITY, st.screener_kinds(settings))
    return screener.with_advice(payload, {}, lambda group: st.for_group(settings, group))


def current(payload):
    """(pregão do ranking, {tipo: {regime, rows: [(lugar, linha com COMPRAR)]}})."""
    view = payload["views"][MODE]
    kinds = {}
    for r in view["rankings"]:
        rows = [(rank, a) for rank, a in enumerate(r["loose"]["top"], 1)
                if (a.get("advice") or {}).get("action") == "comprar"]
        kinds[r["kind"]] = {"regime": r["regime"], "rows": rows}
    return view["refDate"], kinds


def read_state():
    """O último aviso de cada tipo: {tipo: {ref_date, regime, tickers}}."""
    return {r["kind"]: r for r in db.read_table(TABLE)}


def _tickers(state):
    return {t for t in ((state or {}).get("tickers") or "").split(",") if t}


def _row_line(rank, a):
    ma = ((a.get("setup") or {}).get("ma") or {}).get("name")
    parts = [f"{rank}º", f"Score {fmt_num(a['score'])}"]
    if ma:
        parts.append(f"pullback na {ma}")
    if a["advice"].get("priority"):
        parts.append("prioridade")
    name = f" ({a['name']})" if a.get("name") and a["name"] != a["ticker"] else ""
    return f"• {a['ticker']}{name}: " + ", ".join(parts)


def blocks(kinds, previous, top_n):
    """Um bloco de texto por tipo com novidade (entradas com COMPRAR ou mudança de regime)."""
    out = []
    for kind, info in kinds.items():
        prev = previous.get(kind)
        seen = _tickers(prev)
        new = [(rank, a) for rank, a in info["rows"] if a["ticker"] not in seen]
        changed = bool(prev and prev.get("regime") and info["regime"] and prev["regime"] != info["regime"])
        if not new and not changed:
            continue
        regime = info["regime"] or "sem amostra suficiente"
        lines = [f"{KIND_TITLE.get(kind, kind)}: regime {regime}"
                 + (f" (antes {prev['regime']})" if changed else "")]
        if new:
            lines.append("Entraram com COMPRAR:")
            lines += [_row_line(rank, a) for rank, a in new]
        lines.append(f"Com COMPRAR no Top {top_n}: {len(info['rows'])}")
        out.append("\n".join(lines))
    return out


def messages(ref_date, kinds, previous, top_n, site=""):
    """As mensagens do aviso (vazia sem novidade)."""
    found = blocks(kinds, previous, top_n)
    if not found:
        return []
    header = f"MarketPressure: ranking do fechamento de {fmt_date(ref_date)}"
    if not previous:
        header += "\n(primeiro aviso: todos os COMPRAR do Top aparecem como novos)"
    footer = ("Indicação sem considerar a sua carteira (ela pode dizer AGUARDAR se você já comprou nesta região). "
              "Confira no Screener antes de comprar.") + (f"\n{site}" if site else "")
    return telegram.split(found, header, footer)


def state_rows(ref_date, kinds):
    return [{"kind": kind, "ref_date": ref_date, "regime": info["regime"],
             "tickers": ",".join(a["ticker"] for _, a in info["rows"])} for kind, info in kinds.items()]


def notify(scan, site="", now=None):
    """Envia o aviso do ranking da varredura, se houver novidade desde o último. Devolve
    "enviado", "sem novidade", "já avisado", "pregão aberto", "não configurado", "sem destinatários"
    ou "falhou"."""
    if not notificacoes.available():
        return "não configurado"
    now = time.time() if now is None else now
    if in_session_hours(now):
        return "pregão aberto"
    payload = advised_payload(scan)
    ref_date, kinds = current(payload)
    if not ref_date:
        return "sem novidade"
    previous = read_state()
    if any((p.get("ref_date") or "") >= ref_date for p in previous.values()):
        return "já avisado"
    texts = messages(ref_date, kinds, previous, payload["topN"], site)
    for text in texts:
        recipients, sent = notificacoes.broadcast(text)
        if recipients == 0:
            return "sem destinatários"   # o estado não é gravado: a próxima atualização tenta de novo
        if not sent:
            return "falhou"
    db.write_tables({TABLE: state_rows(ref_date, kinds)})
    return "enviado" if texts else "sem novidade"
