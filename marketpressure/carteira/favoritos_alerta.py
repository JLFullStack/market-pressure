"""Aviso dos favoritos pelo Telegram, enviado pela atualização diária (update.py) depois do fechamento.

Para cada usuário que ligou o aviso dos favoritos (Configurações; configuracoes/notificacoes.py),
avisa os favoritos que MUDARAM para uma situação que pede atenção desde o último aviso:

- entrou no ranking com COMPRAR (a Indicação do Screener de hoje, com o lugar e o Score);
- entrou no setup fora do ranking ("em pullback": liquidez abaixo do piso ou Score menor);
- ficou com o preço travado (possível OPA, core.price_locked).

Quem continua na mesma situação não é repetido, e nada é enviado sem novidade. A situação vem de
carteira/favoritos.row, igual à da sub-aba Favoritos, mas sem a carteira (a atualização não lê as
carteiras, que são de cada usuário): a Indicação ignora as compras abertas, e a mensagem diz isso.
O estado (a última situação de cada favorito e o pregão) fica na tabela watchlist_alerts DO
usuário: publicado, o Worker a entrega com a lista e a grava quando o aviso chega
(/notify/watchlists e /notify/user); uma falha do Telegram é tentada de novo na próxima
atualização. Um aviso por pregão fechado, nada durante o pregão, e a rodada das 08:07 não repete
a das 18:30, como o aviso do ranking.
"""

import logging
import time

from . import favoritos
from ..comum.core import fmt_date, fmt_num, in_session_hours
from ..configuracoes import notificacoes
from ..screener import ranking_alert

log = logging.getLogger(__name__)

ALERT = {"comprar", "setup", "travado"}   # situações que geram aviso quando o favorito entra nelas
MAX_PER_MESSAGE = 30


def ranked_from(scan):
    """{ticker: linha do ranking com a Indicação} da visão padrão da varredura (sem carteira), no
    formato que favoritos.row usa."""
    payload = ranking_alert.advised_payload(scan)
    view = payload["views"][ranking_alert.MODE]
    out = {}
    for r in view["rankings"]:
        for rank, a in enumerate(r["loose"]["top"], 1):
            out.setdefault(a["ticker"], {"rank": rank, "row": a, "advice": a.get("advice") or {}, "kind": r["kind"],
                                         "kindLabel": favoritos.KIND_LABEL.get(r["kind"], r["kind"])})
    return view["refDate"], out


def _line(r):
    """Uma linha da mensagem: o ativo, a nova situação e o essencial do motivo."""
    s = r["status"]
    if s["key"] == "comprar":
        prio = " · prioridade" if r.get("priority") else ""
        return f"• {r['ticker']}: COMPRAR ({r['rank']}º do ranking, Score {fmt_num(r['score'])}{prio})"
    if s["key"] == "setup":
        return f"• {r['ticker']}: {s['label'].lower()}, fora do Top 20 do Screener"
    return f"• {r['ticker']}: preço travado (possível OPA ou fusão com preço fixado); confira as notícias"


def news(rows, state):
    """Os favoritos que entraram numa situação de ALERT desde o último aviso (rows: favoritos.row)."""
    before = {(x["ticker"], x.get("market") or "b3"): x.get("status") for x in state or []}
    return [r for r in rows if r["status"]["key"] in ALERT and before.get((r["ticker"], r["market"])) != r["status"]["key"]]


def message(ref_date, changed, site=""):
    lines = [f"MarketPressure: seus favoritos no fechamento de {fmt_date(ref_date)}", ""]
    lines += [_line(r) for r in changed[:MAX_PER_MESSAGE]]
    if len(changed) > MAX_PER_MESSAGE:
        lines.append(f"• e mais {len(changed) - MAX_PER_MESSAGE}")
    lines += ["", "A Indicação não considera as suas compras abertas. Veja os detalhes na aba Carteira → Favoritos."]
    if site:
        lines.append(site)
    return "\n".join(lines)


def notify(scan, site="", now=None):
    """Envia o aviso dos favoritos a cada usuário com novidade. Devolve um resumo: "enviado a N",
    "sem novidade", "pregão aberto", "não configurado", "sem inscritos" ou "falhou"."""
    if not notificacoes.available():
        return "não configurado"
    now = time.time() if now is None else now
    if in_session_hours(now):
        return "pregão aberto"
    users = notificacoes.favorite_lists()
    if users is None:
        return "falhou"
    if not users:
        return "sem inscritos"
    ref_date, ranked = ranked_from(scan)
    if not ref_date:
        return "sem novidade"
    universe = favoritos._universe()
    cache = {}

    def row_of(item):
        key = (item["ticker"], item.get("market") or "b3")
        if key not in cache:
            try:
                cache[key] = favoritos.row({"ticker": key[0], "market": key[1]}, ranked, None, universe)
            except Exception:
                log.warning("favorito %s sem análise para o aviso", key[0], exc_info=True)
                cache[key] = None
        return cache[key]

    sent = failed = 0
    for u in users:
        if any((s.get("ref_date") or "") >= ref_date for s in u.get("state") or []):
            continue   # já avisado neste pregão (a rodada das 08:07 depois da das 18:30)
        rows = [r for r in (row_of(i) for i in u["list"]) if r]
        changed = news(rows, u.get("state"))
        # O estado é gravado todo pregão, com ou sem mensagem: quem sai de COMPRAR e volta depois
        # é avisado de novo.
        state = [{"ticker": r["ticker"], "market": r["market"], "status": r["status"]["key"], "ref_date": ref_date} for r in rows]
        if not changed:
            notificacoes.deliver_user(u["user"], "", state)
            continue
        if notificacoes.deliver_user(u["user"], message(ref_date, changed, site), state):
            sent += 1
        else:
            failed += 1
    if failed and not sent:
        return "falhou"
    return f"enviado a {sent}" if sent else "sem novidade"
