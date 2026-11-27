"""Favoritos (lista de observação): os ativos que o usuário quer acompanhar, mesmo fora do ranking.

A lista é do usuário (tabela `watchlist`, por usuário na versão publicada), não de uma carteira. A
situação de cada favorito é calculada no histórico dele (pregões fechados, como a Situação da
Carteira) e cruzada com o ranking do Screener de hoje (posição, Score e Indicação, com as compras
abertas da carteira escolhida). Fica no nível do aporte porque usa o ranking do Screener.
"""
import logging
import re

from . import aporte
from . import portfolio as pf
from . import portfolio_service as svc
from ..comum import database as db
from ..comum import market_data, runtime
from ..comum.core import (LOCK, SETUP, compute_indicators, day_of, evaluate_setup, fmt_num, fmt_pct_value,
                          price_lock_stats, price_locked, today_iso)
from ..configuracoes import strategy as st

log = logging.getLogger(__name__)

TABLE = "watchlist"
MAX_ITEMS = 40          # favoritos por usuário
MARKETS = ("b3", "us", "crypto")
NEAR = 0.06             # "Perto do recuo": fechamento até 6% acima da média mais próxima, em tendência de alta
MIN_CANDLES = 220       # como o setup (MM200 + inclinação)
TICKER = re.compile(r"^[A-Z0-9]{1,12}$")
STOCK_CODE = re.compile(r"^[A-Z]{4}[3-8]$")


# region Lista
def load():
    """[{ticker, market, added}] na ordem em que foram adicionados."""
    try:
        return [{"ticker": r["ticker"], "market": r.get("market") or "b3", "added": r.get("added")}
                for r in db.read_table(TABLE) if r.get("ticker")]
    except Exception:
        log.warning("não foi possível ler os favoritos", exc_info=True)
        return []


def _save(items):
    db.write_tables({TABLE: [{"ticker": i["ticker"], "market": i["market"], "added": i.get("added")} for i in items]})


def change(body):
    """POST /api/favoritos {op: add|remove, ticker, market} → a mensagem. ValueError com o motivo."""
    op = body.get("op")
    ticker = pf.normalize_ticker(body.get("ticker") or "")
    market = body.get("market") if body.get("market") in MARKETS else "b3"
    if not TICKER.match(ticker):
        raise ValueError("Informe um código de ativo válido (só letras e números).")
    items = load()
    found = next((i for i in items if i["ticker"] == ticker and i["market"] == market), None)
    if op == "remove":
        if not found:
            raise ValueError(f"{ticker} não está nos favoritos.")
        _save([i for i in items if i is not found])
        return f"{ticker} saiu dos favoritos."
    if op != "add":
        raise ValueError("Operação inválida.")
    if found:
        return f"{ticker} já está nos favoritos."
    if len(items) >= MAX_ITEMS:
        raise ValueError(f"A lista já tem {MAX_ITEMS} favoritos; tire algum antes de adicionar.")
    try:
        found_quotes = bool(market_data.history(ticker, market)["candles"])
    except market_data.UpstreamError:
        found_quotes = False
    if not found_quotes:
        raise ValueError(f"Sem cotações de {ticker} no Yahoo; confira o código e o mercado.")
    _save(items + [{"ticker": ticker, "market": market, "added": today_iso()}])
    return f"{ticker} entrou nos favoritos."
# endregion


# region Situação de cada favorito
def _status(key, label, reason):
    return {"key": key, "label": label, "reason": reason}


def _universe():
    try:
        return {u["ticker"]: u for u in db.read_table("universe")}
    except Exception:
        log.warning("lista de ativos indisponível para os favoritos", exc_info=True)
        return {}


def row(item, ranked, portfolio, universe):
    """Linha de um favorito: preço, distância às médias, setup, posição no ranking e a situação
    {key: comprar|aguardar|setup|perto|alta|fora|travado|curto|sem, label, reason}."""
    ticker, market = item["ticker"], item["market"]
    u = (universe.get(ticker) or {}) if market == "b3" else {}
    txs = [t for t in portfolio.get("transactions") or [] if t["ticker"] == ticker] if portfolio else []
    held = sum(t["quantity"] if t["type"] == "compra" else -t["quantity"] for t in txs)
    base = {"ticker": ticker, "market": market, "name": u.get("name") or ticker, "kind": u.get("kind"),
            "currency": pf.MARKETS[market]["currency"], "added": item.get("added"),
            "held": held > 1e-9, "traded": bool(txs), "rank": None, "score": None, "priority": False}
    try:
        candles = market_data.history(ticker, market)["candles"]
    except Exception as err:
        log.warning("favorito %s sem cotações", ticker, exc_info=True)
        return {**base, "status": _status("sem", "Sem cotações", f"Não foi possível carregar o histórico ({err}).")}
    closed = pf.closed_for(candles, market)
    if not closed:
        return {**base, "status": _status("sem", "Sem cotações", "O Yahoo não devolveu candles para este ativo.")}
    c = closed[-1]["c"]
    base.update(price=c, date=day_of(closed[-1]["date"]),
                change=(c / closed[-2]["c"] - 1) * 100 if len(closed) > 1 and closed[-2]["c"] else None)
    if len(closed) < MIN_CANDLES:
        return {**base, "status": _status("curto", "Histórico curto",
                                          f"Só {len(closed)} pregões: o setup precisa de {MIN_CANDLES} (MM200 e a inclinação dela).")}
    ind = compute_indicators(closed)
    i = len(closed) - 1
    setup = evaluate_setup(ind, i)
    base["mas"] = {k: (c / ind[k][i] - 1) * 100 if ind[k][i] else None for k in ("mm20", "mm50", "mm200")}
    base["setup"] = {"ok": bool(setup["ok"]), "ma": setup["ma"]}
    stock = market == "b3" and (u.get("kind") in LOCK["kinds"] or (not u and STOCK_CODE.match(ticker)))
    locked = price_locked(price_lock_stats(ind["closes"], i)) if stock else None
    if locked:
        return {**base, "status": _status("travado", "Preço travado", f"Com {locked}. Confira as notícias antes de comprar.")}
    hit = ranked.get(ticker) if market == "b3" else None
    if hit:
        base.update(rank=hit["rank"], score=hit["row"]["score"], priority=bool(hit["advice"].get("priority")))
        adv = hit["advice"]
        where = f"{hit['rank']}º do ranking de {hit['kindLabel']}"
        if adv["action"] == "comprar":
            return {**base, "status": _status("comprar", "COMPRAR", f"{where}.\n{adv['reason']}")}
        return {**base, "status": _status("aguardar", "AGUARDAR", f"{where}.\n{adv['reason']}")}
    if setup["ok"]:
        ma = setup["ma"]
        why = ("o Screener só analisa ativos da B3" if market != "b3" else
               "está fora do Top 20 do Screener de hoje (liquidez abaixo do piso ou Score menor que o dos 20 primeiros)")
        return {**base, "status": _status("setup", f"Em pullback ({ma['name']})",
                                          f"Em pullback na {ma['name']} (+{fmt_num(ma['dist'] * 100)}% acima da média), mas {why}.")}
    if setup["trend"]:
        above = {k: v for k, v in base["mas"].items() if v is not None and v >= 0}
        k, dist = min(above.items(), key=lambda kv: kv[1]) if above else (None, None)
        if dist is not None and dist <= NEAR * 100:
            return {**base, "status": _status("perto", "Perto do recuo",
                                              f"Em tendência de alta, a {fmt_num(dist)}% da {k.upper()}. O setup pede o fechamento "
                                              f"até {fmt_pct_value(SETUP['maxDistance'])}% acima de uma média, depois de um recuo de "
                                              f"pelo menos {fmt_pct_value(SETUP['minPullback'])}% desde o topo de 20 pregões.")}
        return {**base, "status": _status("alta", "Em alta, longe das médias",
                                          f"Em tendência de alta, mas a {fmt_num(dist or 0)}% da média mais próxima"
                                          f"{f' ({k.upper()})' if k else ''}: falta um recuo para virar compra.")}
    mm200 = base["mas"]["mm200"]
    why = (f"o fechamento está {fmt_num(-mm200)}% abaixo da MM200" if mm200 is not None and mm200 < 0 else
           "a MM200 não está subindo ou a MM20/MM50 está abaixo dela")
    return {**base, "status": _status("fora", "Fora da tendência de alta",
                                      f"Sem tendência de alta: {why}. A estratégia só compra recuos com a MM200 subindo e o "
                                      "preço e as médias acima dela.")}


STATUS_ORDER = ("comprar", "setup", "aguardar", "perto", "alta", "travado", "fora", "curto", "sem")


def payload(portfolio=None):
    """GET /api/favoritos: {items: [linha], max, scanDate, closed: [posições encerradas da carteira]}.
    A lista vem na ordem STATUS_ORDER (as compras primeiro), e dentro dela por código."""
    items = load()
    settings = st.load()
    rankings = aporte._rankings("fechamento") if any(i["market"] == "b3" for i in items) else []
    ranked = {}
    for x in aporte.screener_rows(portfolio or {"transactions": []}, rankings, settings) if rankings else []:
        ranked.setdefault(x["row"]["ticker"], {**x, "kindLabel": KIND_LABEL.get(x["kind"], x["kind"])})
    universe = _universe()
    rows = []
    for item, result, err in runtime.run_all(lambda i: row(i, ranked, portfolio, universe), items):
        if err is not None:
            log.warning("favorito %s indisponível", item["ticker"], exc_info=err)
            result = {"ticker": item["ticker"], "market": item["market"], "name": item["ticker"], "held": False,
                      "traded": False, "rank": None, "score": None, "priority": False, "added": item.get("added"),
                      "currency": pf.MARKETS[item["market"]]["currency"],
                      "status": _status("sem", "Sem cotações", f"Não foi possível analisar o ativo ({err}).")}
        rows.append(result)
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]["key"]), r["ticker"]))
    favorites = {(i["ticker"], i["market"]) for i in items}
    return {"items": rows, "max": MAX_ITEMS, "near": NEAR * 100,
            "closed": closed_positions(portfolio, favorites) if portfolio else []}


KIND_LABEL = {"acoes": "Ações", "fundos": "Fundos imobiliários", "etfs": "ETFs", "bdrs": "BDRs"}
# endregion


# region Ativos que já passaram pela carteira
def closed_positions(portfolio, favorites=frozenset()):
    """Ativos vendidos por inteiro na carteira (só com lançamentos, sem quantidade hoje): lucro,
    resultado e a data da última venda, do mais recente para o mais antigo; o gráfico deles abre com
    as compras e vendas feitas. Sem cotações, a lista vem vazia."""
    if not pf.has_entries(portfolio):
        return []
    try:
        positions = svc.full_analysis(portfolio, reuse=True)["positions"]
    except Exception:
        log.warning("posições encerradas indisponíveis nos favoritos", exc_info=True)
        return []
    txs = portfolio.get("transactions") or []
    out = []
    for p in positions:
        if p["quantity"] > 1e-9:
            continue
        mine = [t for t in txs if t["ticker"] == p["ticker"]]
        market = pf.market_of(portfolio, p["ticker"])
        out.append({"ticker": p["ticker"], "market": market, "profit": p["profit"], "returnPct": p["returnPct"],
                    "first": min(t["date"] for t in mine), "last": max(t["date"] for t in mine),
                    "operations": len(mine), "favorite": (p["ticker"], market) in favorites})
    return sorted(out, key=lambda x: x["last"], reverse=True)
# endregion
