"""Aviso de imposto (aba Configurações): avisa quando as vendas de ações do mês se aproximam do
limite de isenção do imposto de renda.

Pessoa física, operações comuns: vendas de ações na B3 que somam até R$ 20 mil no mês ficam
isentas; acima disso, o lucro de todas as vendas de ações do mês paga 15% (DARF até o último dia
útil do mês seguinte). ETFs, BDRs e fundos não têm a isenção: o lucro paga em qualquer valor (15%;
fundos imobiliários 20%).

O aviso aparece:
- na Visão geral da Carteira (status do mês, com as vendas que a Situação indica hoje);
- no formulário "Nova operação", ao digitar uma venda (como o mês fica com ela);
- na confirmação de uma venda registrada;
- no texto do "ANALISAR CARTEIRA".

Configuração (tabela settings_alerts, uma linha; por usuário na versão publicada): ligado e o
valor a partir do qual avisar. As contas ficam em portfolio.stock_sales (pura).
"""

import logging

from . import database as db
from . import market_data
from . import portfolio as pf
from .core import fmt_brl, today_iso

log = logging.getLogger(__name__)

TABLE = "settings_alerts"
LIMIT = pf.STOCK_EXEMPTION
DEFAULT = {"enabled": True, "warnAt": 15_000}
WARN_RANGE = (1_000, LIMIT)          # aviso a partir de (R$)
MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro")
NO_EXEMPTION = ("Vendas de ETFs, BDRs e fundos não têm a isenção de R$ 20 mil: o lucro paga imposto em qualquer "
                "valor (15%; fundos imobiliários 20%).")
PARTIAL_HINT = ("Uma venda parcial pode ficar para o mês seguinte para caber na isenção; a venda pela reversão de "
                "tendência (VENDER) não deve esperar por causa do imposto.")


# ---------------------------------------------------------------- Configuração

def from_form(body):
    """Formulário → (configuração, erro). warnAt em reais."""
    enabled = body.get("enabled") is not False
    warn = pf.parse_number(body.get("warnAt"))
    if warn is None or not WARN_RANGE[0] <= warn <= WARN_RANGE[1]:
        return None, (f"O aviso deve começar entre {fmt_brl(WARN_RANGE[0])} e {fmt_brl(WARN_RANGE[1])} "
                      "de vendas de ações no mês.")
    return {"enabled": enabled, "warnAt": warn}, None


def load():
    try:
        rows = db.read_table(TABLE)
    except Exception:
        log.warning("não foi possível ler o aviso de imposto; usando o padrão", exc_info=True)
        return dict(DEFAULT)
    if not rows:
        return dict(DEFAULT)
    r = rows[0]
    warn = r.get("warn_at")
    return {"enabled": r.get("enabled") is not False,
            "warnAt": warn if warn is not None and WARN_RANGE[0] <= warn <= WARN_RANGE[1] else DEFAULT["warnAt"]}


def save(s):
    db.write_tables({TABLE: [{"enabled": s["enabled"], "warn_at": s["warnAt"]}]})
    return s


def payload(s=None):
    """O que a aba Configurações mostra do aviso."""
    s = s or load()
    return {"alert": s, "default": DEFAULT, "limit": LIMIT, "range": WARN_RANGE,
            "text": (f"Avisa quando as vendas de ações do mês passarem de {fmt_brl(s['warnAt'])} "
                     f"(o limite da isenção é {fmt_brl(LIMIT)})." if s["enabled"] else "Aviso desligado.")}


# ---------------------------------------------------------------- Status do mês

def month_label(month):
    return f"{MONTHS[int(month[5:7]) - 1]} de {month[:4]}"


def stock_tickers(portfolio, extra=()):
    """Ativos da carteira (e extra) que são ações da B3 (ON, PN e units), pelo tipo da lista do
    screener (tabela universe) ou, sem ela, pelo código."""
    try:
        universe = {r["ticker"]: r for r in db.read_table("universe")}
    except Exception:
        log.warning("não foi possível ler a lista de ativos salva; tipo pelo código", exc_info=True)
        universe = {}
    out = set()
    for t in set(portfolio.get("assets") or {}) | {tx["ticker"] for tx in portfolio.get("transactions") or []} | set(extra):
        if pf.market_of(portfolio, t) != "b3":
            continue
        u = universe.get(t) or {}
        if (u.get("kind") or market_data.asset_kind(t, u.get("name") or "")) == "acoes":
            out.add(t)
    return out


def _level(sold, warn_at):
    return "over" if sold > LIMIT else "near" if sold >= warn_at else "ok"


def _sold_line(month, sold):
    return f"Vendas de ações em {month_label(month)}: {fmt_brl(sold)} de {fmt_brl(LIMIT)}"


def _over_line(gain):
    if gain <= 0:
        return (f"Passaram de {fmt_brl(LIMIT)}, mas as vendas de ações do mês não deram lucro "
                f"({fmt_brl(gain)}): não há imposto; o prejuízo pode compensar lucros dos meses seguintes.")
    return (f"Passaram de {fmt_brl(LIMIT)}: o lucro das vendas de ações do mês ({fmt_brl(gain)}) paga 15% de imposto, "
            f"cerca de {fmt_brl(gain * pf.STOCK_TAX)} (sem descontar prejuízos de meses anteriores), com DARF até o "
            "último dia útil do mês seguinte.")


def status(portfolio, open_positions=None, settings=None, month=None):
    """O mês (padrão: o atual) para a Visão geral, ou None com o aviso desligado. open_positions
    (analysis["open"]): as vendas que a Situação indica hoje (VENDER e VENDER 50%) nas ações, que
    entram como "indicadas" e podem levar o mês acima do limite."""
    s = settings or load()
    if not s["enabled"]:
        return None
    month = month or today_iso()[:7]
    stocks = stock_tickers(portfolio)
    m = pf.stock_sales(portfolio.get("transactions") or [], stocks, month)
    level = _level(m["sold"], s["warnAt"])
    lines = []
    if level == "over":
        lines.append(_over_line(m["gain"]))
    elif level == "near":
        lines.append(f"Faltam {fmt_brl(LIMIT - m['sold'])} para o limite da isenção. Acima dele, o lucro de todas as "
                     "vendas de ações do mês paga 15% de imposto.")
    indicated = None
    if open_positions is not None and month == today_iso()[:7]:
        items = pf.indicated_stock_sales(open_positions, stocks)
        if items:
            value = sum(i["value"] for i in items)
            after = m["sold"] + value
            indicated = {"items": items, "value": value, "after": after, "over": m["sold"] <= LIMIT < after}
            if indicated["over"] or (level != "over" and after >= s["warnAt"]):
                names = ", ".join(f"{i['ticker']} ({i['label']})" for i in items)
                lines.append(f"As vendas que a Situação indica hoje nas ações — {names}, cerca de {fmt_brl(value)} — "
                             f"levariam o mês a {fmt_brl(after)}"
                             + (f", acima de {fmt_brl(LIMIT)}. " + PARTIAL_HINT if indicated["over"] else "."))
    show = level != "ok" or bool(indicated and (indicated["over"] or indicated["after"] >= s["warnAt"]))
    return {"month": month, "monthLabel": month_label(month), "limit": LIMIT, "warnAt": s["warnAt"],
            "sold": m["sold"], "gain": m["gain"], "count": m["count"], "remaining": max(0.0, LIMIT - m["sold"]),
            "pct": m["sold"] / LIMIT * 100, "level": level, "indicated": indicated, "show": show,
            "title": _sold_line(month, m["sold"]), "lines": lines}


def preview(portfolio, tx, settings=None):
    """Formulário "Nova operação": como o mês da venda digitada fica com ela. tx = {ticker, market,
    type, date, quantity, price, fx}; None quando não é uma venda completa ou o aviso está desligado."""
    s = settings or load()
    if not s["enabled"] or tx.get("type") != "venda" or not tx.get("ticker") or not tx.get("date") \
            or not tx.get("quantity") or not tx.get("price") or tx["quantity"] <= 0 or tx["price"] <= 0:
        return None
    month = tx["date"][:7]
    known = (portfolio.get("assets") or {}).get(tx["ticker"])
    market = (known or {}).get("market") or tx.get("market") or "b3"
    stocks = stock_tickers(portfolio, extra=[tx["ticker"]] if market == "b3" else [])
    if tx["ticker"] not in stocks:
        return {"show": True, "level": "info", "title": "Esta venda não entra na isenção de ações", "lines": [NO_EXEMPTION]}
    txs = portfolio.get("transactions") or []
    before = pf.stock_sales(txs, stocks, month)
    after = pf.stock_sales(txs + [{**tx, "id": "_previa", "fees": tx.get("fees") or 0, "fx": tx.get("fx") or 1}], stocks, month)
    level = _level(after["sold"], s["warnAt"])
    lines = []
    if level == "over":
        lines.append(("Esta venda leva o mês acima do limite. " if before["sold"] <= LIMIT else "") + _over_line(after["gain"]))
        if before["sold"] <= LIMIT:
            lines.append(PARTIAL_HINT)
    elif level == "near":
        lines.append(f"Depois dela, faltam {fmt_brl(LIMIT - after['sold'])} para o limite da isenção.")
    return {"show": level != "ok", "level": level, "month": month, "monthLabel": month_label(month),
            "before": before["sold"], "sold": after["sold"], "gain": after["gain"], "limit": LIMIT,
            "title": f"Com esta venda, as vendas de ações em {month_label(month)} vão a {fmt_brl(after['sold'])} de {fmt_brl(LIMIT)}",
            "lines": lines}


def after_sale(portfolio, tx):
    """Aviso junto da confirmação de uma venda de ações registrada (o mês dela já com ela), ou
    None quando o mês segue abaixo do aviso, o ativo não é ação ou o aviso está desligado."""
    if tx.get("type") != "venda":
        return None
    s = load()
    stocks = stock_tickers(portfolio)
    if not s["enabled"] or tx["ticker"] not in stocks:
        return None
    month = tx["date"][:7]
    m = pf.stock_sales(portfolio.get("transactions") or [], stocks, month)
    level = _level(m["sold"], s["warnAt"])
    if level == "ok":
        return None
    return {"show": True, "level": level, "month": month, "sold": m["sold"], "gain": m["gain"],
            "title": _sold_line(month, m["sold"]),
            "lines": [_over_line(m["gain"]) if level == "over" else f"Faltam {fmt_brl(LIMIT - m['sold'])} para o limite da isenção."]}


def prompt_line(portfolio, open_positions):
    """Linha do "ANALISAR CARTEIRA" sobre o imposto do mês (vazia com o aviso desligado)."""
    st = status(portfolio, open_positions)
    if not st:
        return ""
    text = (f"- **Imposto do mês**: vendas de ações em {st['monthLabel']}: {fmt_brl(st['sold'])} (isenção de IR até {fmt_brl(LIMIT)} em vendas "
            f"de ações no mês; avisar a partir de {fmt_brl(st['warnAt'])}). ")
    text += " ".join(st["lines"]) if st["lines"] else "Ainda longe do limite."
    return text + " " + NO_EXEMPTION + " Considere o imposto ao sugerir vendas parciais e o mês delas, nunca para adiar uma VENDER."

