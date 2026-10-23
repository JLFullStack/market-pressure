"""Montagem dos dados da aba Carteira para a página: carrega cotações, CDI e proventos
(market_data), chama os cálculos puros de portfolio.py e devolve os payloads das rotas
/api/carteira/{id}/... e /api/cotacao.
"""

import json
import logging
import threading
import time
from datetime import date

from . import database as db
from . import goals as gl
from . import market_data, runtime
from . import strategy as st
from .core import add_up, day_of, fmt_date, session_in_progress, today_iso
from .portfolio import (APORTE_MODES, BENCHMARK, CLASS_GROUP, FI_LABELS, FX, JCP_TAX, MARKETS, PERIODS, add_months,
                        analyze_portfolio, batch_rows, class_members, close_on_or_before, contribution_history,
                        contribution_items, contribution_path, contribution_plan, dividends_by_month, dividends_to_receive, fixed_income_class, has_entries, is_iso_date,
                        market_of, net_flows, normalize_ticker, open_lots, parse_number, patrimony_bars, period_start, receivables_by_month,
                        rebase_series,
                        suggest_class, targets_off, tx_flow)
from .portfolio import asset_chart as pf_asset_chart

log = logging.getLogger(__name__)


def structure(portfolio):
    """Partes da página que dependem só da carteira (sem cotações): classes e o que há nelas,
    histórico de lançamentos, opções dos seletores e as classes padrão dos formulários."""
    classes = portfolio["classes"]
    history = []
    for tx in portfolio["transactions"]:
        market = market_of(portfolio, tx["ticker"])
        history.append({"date": tx["date"], "kind": "tx", "id": tx["id"], "asset": tx["ticker"],
                        "type": "Compra" if tx["type"] == "compra" else "Venda", "currency": MARKETS[market]["currency"],
                        "quantity": tx["quantity"], "price": tx["price"], "fees": tx.get("fees") or 0,
                        "fx": tx.get("fx") or 1, "gross": tx["quantity"] * tx["price"], "flowBRL": abs(tx_flow(tx))})
    for f in portfolio["fixedIncome"]:
        for e in f["entries"]:
            history.append({"date": e["date"], "kind": "fi", "id": e["id"], "item": f["id"], "asset": f["name"],
                            "type": FI_LABELS.get(e["type"], e["type"]), "amount": e["amount"]})
    history.sort(key=lambda r: r["date"], reverse=True)
    assets = portfolio["assets"]
    settings = st.load()

    def rule_text(name):
        group = CLASS_GROUP.get(name)
        return st.STRATEGIES[st.for_group(settings, group)["key"]] if group else "Sem regra de venda (títulos)"
    return {
        "today": today_iso(),
        "hasEntries": has_entries(portfolio),
        "classes": [{"name": c["name"], "target": c["target"], "members": class_members(portfolio, c["name"]),
                     "rule": rule_text(c["name"])} for c in classes],
        "targetSum": add_up(c["target"] for c in classes),
        "targetsOff": bool(classes) and targets_off(add_up(c["target"] for c in classes)),
        "defaultClass": suggest_class("", "b3", classes),
        "fixedIncomeClass": fixed_income_class(classes),
        "fixedIncome": [{"id": f["id"], "name": f["name"]} for f in portfolio["fixedIncome"]],
        "moveOptions": [{"key": t, "label": f"{t} ({assets[t].get('class')})"} for t in sorted(assets)]
                       + [{"key": f"fi:{f['id']}", "label": f"{f['name']} ({f['class']})"} for f in portfolio["fixedIncome"]],
        "history": history,
    }


def asset_info(portfolio, ticker, market):
    """Ticker digitado no formulário "Nova operação": normalizado, se é novo na carteira, o
    mercado efetivo (o do ativo já cadastrado, ou o escolhido) e a classe sugerida."""
    ticker = normalize_ticker(ticker)
    known = portfolio["assets"].get(ticker)
    market = (known.get("market") or "b3") if known else (market if market in MARKETS else "b3")
    return {"ticker": ticker, "isNew": bool(ticker) and not known, "market": market,
            "currency": MARKETS[market]["currency"],
            "suggestedClass": (known.get("class") if known else None) or suggested_class(ticker, market)}


def suggested_class(ticker, market):
    """Classe de um ativo novo pelo tipo: o da última lista do screener (tabela universe; ETFs do
    exterior pelo nome) e, sem ela, o código (portfolio.suggest_class)."""
    u = {}
    if market == "b3":
        try:
            u = next((r for r in db.read_table("universe") if r["ticker"] == ticker), None) or {}
        except Exception:
            log.warning("não foi possível ler a lista de ativos salva; classe pelo código", exc_info=True)
    return suggest_class(ticker, market, kind=u.get("kind"), international=market_data.international_etf(u.get("name")))


def quote_history(ticker, market):
    """Histórico usado na carteira; erro se o Yahoo não tiver cotações."""
    hist = market_data.history(ticker, market)
    if not hist["candles"]:
        raise RuntimeError(f"sem cotações para {ticker}")
    return hist


def price_on(ticker, market, day):
    """Fechamento de um ativo no dia (ou no pregão anterior) e, fora da B3, o câmbio USDBRL do dia.
    Usado para preencher preço e câmbio do formulário (ambos editáveis)."""
    ticker = normalize_ticker(ticker)
    market = market if market in MARKETS else "b3"
    out = {"ticker": ticker, "market": market}
    valid = is_iso_date(day)
    try:
        out["close"] = close_on_or_before(quote_history(ticker, market)["candles"], day) if valid else None
    except Exception as err:
        out["error"] = str(err)
    if market != "b3":
        try:
            out["fx"] = close_on_or_before(quote_history(FX, "fx")["candles"], day) if valid else None
        except Exception as err:
            out["fxError"] = str(err)
    return out


def load_histories(portfolio):
    """Cotações da carteira (em paralelo), do BOVA11 e, se houver ativo fora da B3, do câmbio."""
    tickers = list(dict.fromkeys(t["ticker"] for t in portfolio["transactions"]))
    requests = [(BENCHMARK, "b3")] + [(t, market_of(portfolio, t)) for t in tickers]
    if any(market_of(portfolio, t) != "b3" for t in tickers):
        requests.append((FX, "fx"))
    histories, failed = {}, []
    for (t, _), hist, err in runtime.run_all(lambda r: quote_history(*r), requests):
        if err is None:
            histories[t] = hist
        else:
            failed.append(f"{t} ({err})")
    return histories, failed


def load_cdi(portfolio, histories):
    dates = sorted([t["date"] for t in portfolio["transactions"]]
                   + [e["date"] for f in portfolio["fixedIncome"] for e in f["entries"]])
    if not dates:
        return []
    bench = histories.get(BENCHMARK)
    start = day_of(bench["candles"][0]["date"]) if bench else dates[0]
    first = dates[0] if dates[0] > start else start
    return market_data.cdi(date.fromisoformat(add_months(first, -1)))["rates"]


# Última análise de cada carteira, reaproveitada pela calculadora de aporte enquanto a carteira
# não muda (a análise carrega cotações e calcula indicadores; o aporte é recalculado a cada tecla).
_analysis_cache: dict[str, tuple[str, dict]] = {}
_analysis_lock = threading.Lock()


def _signature(portfolio, strategy):
    """O que muda a análise: a carteira, menos as metas (editar uma meta não recarrega cotações),
    e a estratégia de venda (aba Configurações)."""
    return json.dumps({"portfolio": {k: v for k, v in portfolio.items() if k != "goals"}, "strategy": strategy},
                      sort_keys=True, ensure_ascii=False)


def full_analysis(portfolio, reuse=False):
    """Carrega os dados e analisa a carteira. reuse: devolve a última análise se a carteira
    não mudou (usado pelo aporte)."""
    strategy = st.load()
    signature = _signature(portfolio, strategy)
    if reuse:
        with _analysis_lock:
            hit = _analysis_cache.get(portfolio["id"])
        if hit and hit[0] == signature:
            return hit[1]
    histories, failed = load_histories(portfolio)
    try:
        rates = load_cdi(portfolio, histories)
    except Exception as err:
        rates = []
        failed.append(f"CDI ({err})")
    classes = {t: a.get("class") for t, a in portfolio["assets"].items()}
    analysis = analyze_portfolio(portfolio, histories, rates, strategy=st.asset_rules(strategy, classes))
    bench = histories.get(BENCHMARK)
    analysis["live"] = bool(bench and bench["candles"] and session_in_progress(bench["candles"]))
    analysis["failed"] = failed
    analysis["loadedAt"] = int(time.time() * 1000)
    with _analysis_lock:
        _analysis_cache[portfolio["id"]] = (signature, analysis)
    return analysis


def _chart(series):
    if not series or len(series["dates"]) < 2:
        return None
    periods = {}
    for label, months in PERIODS:
        start = period_start(series, months)
        periods[label] = {"labels": series["dates"][start:],
                          **{key: rebase_series(series[key], start) for key in ("portfolio", "cdi", "bova")}}
    return {"truncated": series["truncated"], "firstDay": series["dates"][0], "periods": periods}


def _asset_target_groups(portfolio, analysis):
    items, warnings = contribution_items(portfolio, analysis, "fechamento")
    total = analysis["kpis"]["total"]
    groups = []
    for c in portfolio["classes"]:
        members = sorted((i for i in items if i["cls"] == c["name"]), key=lambda i: -i["value"])
        if members:
            groups.append({"cls": c["name"], "target": c["target"], "members": [
                _asset_target_row(i, total) for i in members]})
    return {"groups": groups, "warnings": warnings}


ABOVE_TARGET_TOLERANCE = 0.005  # p.p.: abaixo disso o ativo está na meta


def _asset_target_row(i, total):
    """Uma linha da Meta por ativo: % atual, meta (fixada ou automática) e se o ativo já passou
    dela (above: a barra da página fica laranja; não é sinal de venda, só não recebe aporte)."""
    pct = i["value"] / total * 100 if total else 0
    return {"key": i["key"], "label": i["label"], "pct": pct, "target": i["target"],
            "autoTarget": i["autoTarget"], "above": pct > i["target"] + ABOVE_TARGET_TOLERANCE}


def analysis_payload(portfolio):
    """O que a Visão geral, os Proventos recebidos e a Meta por ativo mostram."""
    a = full_analysis(portfolio)
    series = a["series"]
    return {
        "open": [{k: v for k, v in p.items() if k != "setup"} for p in a["open"]],
        "fixed": [{k: v for k, v in f.items() if k != "entries"} for f in a["fixed"]],
        "dividends": a["dividends"],
        "dividendTotals": sorted(a["divByTicker"].items(), key=lambda kv: -kv[1]),
        "dividendMonths": (months := dividends_by_month(a["dividends"], today_iso())),
        "dividendYear": add_up(m["total"] for m in months),
        "allocation": a["allocation"], "targetSum": a["targetSum"], "targetsOff": targets_off(a["targetSum"]),
        "kpis": a["kpis"], "hasForeign": a["hasForeign"],
        "chart": _chart(series), "patrimony": patrimony_bars(series), "lastDay": series["dates"][-1] if series else None,
        "live": a["live"], "failed": a["failed"], "loadedAt": a["loadedAt"],
        "assetTargets": _asset_target_groups(portfolio, a),
    }


def contribution(portfolio, amount, mode, base, selected, options=None, candidates=(), priority=()):
    """Plano de aporte para os campos do painel, com as linhas prontas para "Registrar compras",
    o plano de vários aportes (por classe) e o histórico de aportes dos últimos 12 meses.
    options: contribution_options; candidates: ativos novos do Screener escolhidos (itens da
    calculadora); priority: tickers com COMPRAR com prioridade no Screener (aporte.py monta os dois)."""
    a = full_analysis(portfolio, reuse=True)
    amount = max(0, parse_number(amount, 0) or 0)
    mode = mode if mode in APORTE_MODES else "todos"
    items, warnings = contribution_items(portfolio, a, base, candidates, priority)
    plan = contribution_plan(portfolio["classes"], items, a["kpis"]["total"], amount, mode, selected or [], options)
    can_register = not plan["waiting"] and any(r["kind"] == "quote" and r["eligible"] and (r["shares"] or 0) > 0
                                               for r in plan["rows"])
    for r in plan["rows"]:
        r.pop("setup", None)
    return {**plan, "warnings": warnings + plan["emptyClasses"], "canRegister": can_register, "targetsOff": targets_off(plan["targetSum"]),
            "classDiff": add_up(c["diff"] for c in plan["classRows"]),
            "sells": [r["label"] for r in plan["rows"] if r["status"]["key"] == "sell"],
            "batchRows": batch_rows(plan, a["open"], today_iso()) if can_register else [],
            "path": contribution_path(plan["classRows"], amount),
            "history": contribution_history(a["series"], portfolio["classes"])}


def receivables(portfolio):
    """Proventos anunciados e ainda não pagos dos ativos da B3, por data de pagamento."""
    tickers = [t for t in dict.fromkeys(tx["ticker"] for tx in portfolio["transactions"])
               if market_of(portfolio, t) == "b3"]
    rows, failed = [], []
    for t, data, err in runtime.run_all(market_data.proventos, tickers):
        try:
            if err is not None:
                raise err
            rows.extend(dividends_to_receive(portfolio["transactions"], t, data["events"]))
        except Exception as err:
            failed.append(f"{t} ({err})")
    rows.sort(key=lambda r: (r["paymentDate"] or "9999-99-99", r["ticker"]))
    return {"rows": rows, "failed": failed, "months": receivables_by_month(rows), "total": add_up(r["total"] for r in rows),
            "net": add_up(r["net"] for r in rows), "expected": add_up(r["total"] for r in rows if not r["confirmed"]),
            "jcpTax": JCP_TAX}


def _portfolio_data(portfolio, today):
    """O que as metas que partem da carteira usam: patrimônio atual, série diária, aporte médio
    real e rentabilidade real anualizada. Sem lançamentos, tudo vazio; cotações indisponíveis =
    patrimônio 0 com aviso."""
    data = {"value": 0.0, "series": None, "contribution": None, "realReturn": None, "warning": None}
    if not has_entries(portfolio):
        return data
    data["contribution"] = gl.average_contribution(net_flows(portfolio), today)
    try:
        a = full_analysis(portfolio, reuse=True)
    except Exception as err:
        log.warning("patrimônio da carteira indisponível para as metas", exc_info=True)
        data["warning"] = f"Patrimônio atual indisponível ({err}); as metas que partem dele usam R$ 0,00."
        return data
    data["value"], data["series"] = a["kpis"]["total"], a["series"]
    last12 = a["kpis"].get("last12")
    if a["series"] and last12:
        end = a["series"]["dates"][-1]
        rate = gl.annualized_return(last12["portfolio"], last12["from"], end)
        if rate is not None:
            full = add_months(end, -12) >= a["series"]["dates"][0]
            note = ("rentabilidade da carteira nos últimos 12 meses" if full
                    else f"rentabilidade da carteira desde {fmt_date(last12['from'])}, anualizada")
            data["realReturn"] = {"rate": rate, "note": note}
    return data


def _goal_view(goal, data, today):
    uses = goal["usePortfolio"]
    current = data["value"] if uses else goal["initial"]
    result = gl.project(goal, current, today)
    if uses:
        result["projections"] = gl.projections(goal, current, today, data["contribution"], data["realReturn"])
        if goal.get("created"):
            result["tracking"] = gl.tracking(goal, data["series"], current, today)
    return {**goal, "kindLabel": gl.KINDS[goal["kind"]], "result": result}


def goals_payload(portfolio):
    """Sub-aba Metas: cada meta com a projeção e, nas que partem da carteira, o realizado ×
    planejado e as previsões pelo aporte e pela rentabilidade reais."""
    goals = portfolio.get("goals") or []
    today = today_iso()
    data = _portfolio_data(portfolio, today) if any(g["usePortfolio"] for g in goals) else _portfolio_data({}, today)
    return {"today": today, "portfolioValue": data["value"], "warning": data["warning"],
            "goals": [_goal_view(g, data, today) for g in goals]}


def goal_preview(portfolio, goal):
    """Projeção de uma meta ainda não salva (o formulário já validado por goals.goal_from_form)."""
    today = today_iso()
    data = _portfolio_data(portfolio, today) if goal["usePortfolio"] else _portfolio_data({}, today)
    return {"warning": data["warning"], "portfolioValue": data["value"], "goal": _goal_view(goal, data, today)}


def asset_chart(portfolio, ticker):
    """Card "Operações no gráfico" de uma posição: o gráfico do ativo com os lançamentos e os
    alvos das compras abertas pela regra da classe dele (portfolio.asset_chart). LookupError se o
    ativo não tem lançamentos na carteira."""
    ticker = normalize_ticker(ticker)
    if not any(t["ticker"] == ticker for t in portfolio["transactions"]):
        raise LookupError(f"{ticker} não tem lançamentos nesta carteira")
    market = market_of(portfolio, ticker)
    classes = {t: a.get("class") for t, a in portfolio["assets"].items()}
    rule = st.asset_rules(st.load(), classes)(ticker, market)
    out = pf_asset_chart(portfolio["transactions"], ticker, quote_history(ticker, market)["candles"], market, rule)
    name = ticker
    if market == "b3":
        try:
            name = next((u.get("name") for u in db.read_table("universe") if u["ticker"] == ticker), None) or ticker
        except Exception:
            log.warning("não foi possível ler a lista de ativos salva; gráfico sem o nome do ativo", exc_info=True)
    return {**out, "cls": classes.get(ticker), "name": name, "ruleLabel": st.STRATEGIES[rule["key"]]}


def held_lots(portfolio, tickers):
    """Compras ainda abertas (preço, data e quantidade restante) dos ativos da B3 da carteira que
    estão em `tickers` (o ranking do Screener), para a indicação de recompra. Sem cotações: só os
    lançamentos contam."""
    txs = portfolio.get("transactions") or []
    out = {}
    for t in dict.fromkeys(tx["ticker"] for tx in txs):
        if t in tickers and market_of(portfolio, t) == "b3":
            lots = open_lots(txs, t, [])
            if lots:
                out[t] = lots
    return out
