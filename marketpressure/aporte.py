"""Calculadora de aporte com o Screener: os candidatos de compra do ranking de hoje para as classes
abaixo da meta (ativos novos que a carteira ainda não tem) e a prioridade da Indicação, somados ao
plano de portfolio_service.contribution. Fica acima de portfolio_service porque usa o screener.
"""

import logging

from . import portfolio as pf
from . import portfolio_service as svc
from . import screener
from . import strategy as st

log = logging.getLogger(__name__)

MAX_CANDIDATES = 5  # candidatos do Screener mostrados por classe abaixo da meta


def _rankings(base):
    """Rankings do Screener por tipo na base pedida (liquidez padrão, sem candle de rejeição), ou
    [] sem varredura. A salva só é relida quando a da memória venceu (o aporte é recalculado a
    cada pausa na digitação)."""
    job = screener.scan_job
    try:
        if not job._fresh():
            job._reload()
        payload = job.payload("padrao", st.screener_kinds(st.load()))
        return payload["views"][base]["rankings"] if payload else []
    except Exception:
        log.warning("ranking do Screener indisponível para a calculadora de aporte", exc_info=True)
        return []


def screener_rows(portfolio, rankings, settings):
    """Cada linha dos rankings com a classe da carteira em que cairia (pelo grupo da regra de venda:
    BDRs e ETFs do exterior em Internacionais), a posição no ranking do tipo e a Indicação
    (screener.advice, com as compras abertas da carteira)."""
    class_of = {g: c for c, g in pf.CLASS_GROUP.items() if g}
    rows = [(rank, r["kind"], row) for r in rankings for rank, row in enumerate(r["loose"]["top"], 1)]
    lots = svc.held_lots(portfolio, {row["ticker"] for _, _, row in rows})
    out = []
    for rank, kind, row in rows:
        group = screener.rule_group({**row, "kind": kind})
        out.append({"cls": class_of.get(group), "rank": rank, "kind": kind, "row": row,
                    "advice": screener.advice(row, lots.get(row["ticker"]), st.for_group(settings, group), group, rank)})
    return out


def candidate_item(x):
    """Item da calculadora para um ativo novo do Screener (sem posição: valor 0, preço de hoje)."""
    row = x["row"]
    return {"key": row["ticker"], "label": row["ticker"], "name": row.get("name"), "cls": x["cls"], "value": 0,
            "kind": "quote", "market": "b3", "priceBRL": row["price"], "priceNative": row["price"], "fx": 1,
            "sell": False, "setup": {"ok": True, "enough": True, "ma": row["setup"]["ma"], "wait": []},
            "fixedTarget": None, "candidate": True, "priority": bool(x["advice"].get("priority"))}


def plan(portfolio, body):
    """POST /api/carteira/{id}/aporte: o plano (portfolio_service.contribution) com as opções da
    página, os candidatos escolhidos (body.candidates: tickers) e, em candidates, os COMPRAR do
    Screener de hoje para cada classe abaixo da meta: [{cls, gap, empty, rows: [{ticker, name,
    kindLabel, rank, score, price, priority, reason, chosen}]}]."""
    settings = st.load()
    base = body.get("base") if body.get("base") in screener.MODES else "fechamento"
    opts = pf.contribution_options(body, screener.CONFIG["prioritySize"])
    held = {p["ticker"] for p in svc.full_analysis(portfolio, reuse=True)["open"]}
    rankings = _rankings(base)
    ranked = screener_rows(portfolio, rankings, settings)
    buys = [x for x in ranked if x["cls"] and x["advice"]["action"] == "comprar" and x["row"]["ticker"] not in held]
    chosen = {str(t).upper() for t in body.get("candidates") or []}
    items = [candidate_item(x) for x in buys if x["row"]["ticker"] in chosen]
    priority = {x["row"]["ticker"] for x in ranked if x["advice"].get("priority")} if opts["advice"] else set()
    result = svc.contribution(portfolio, body.get("amount"), body.get("mode"), base, body.get("selected"),
                              opts, items, priority)
    groups = []
    for c in result["classRows"]:
        if not (c["diff"] > 0.005 and c["target"] > 0 and pf.CLASS_GROUP.get(c["cls"])):
            continue
        options = sorted((x for x in buys if x["cls"] == c["cls"]), key=lambda x: -x["row"]["score"])
        shown = [x for k, x in enumerate(options) if k < MAX_CANDIDATES or x["row"]["ticker"] in chosen]
        groups.append({
            "cls": c["cls"], "gap": c["diff"], "empty": not any(not m.get("candidate") for m in c["members"]),
            "rows": [{"ticker": x["row"]["ticker"], "name": x["row"].get("name"),
                      "kindLabel": screener.KIND_LABEL.get(x["kind"], x["kind"]), "rank": x["rank"],
                      "score": x["row"]["score"], "price": x["row"]["price"], "sector": x["row"].get("sector"),
                      "priority": bool(x["advice"].get("priority")), "reason": x["advice"]["reason"],
                      "chosen": x["row"]["ticker"] in chosen} for x in shown]})
    result["candidates"] = groups
    result["screenerReady"] = bool(rankings)
    return result
