"""Montagem dos dados da aba Carteira para a página: carrega cotações, CDI e proventos
(market_data), chama os cálculos puros de portfolio.py e devolve os payloads das rotas
/api/carteira/{id}/... e /api/cotacao.
"""

import json
import threading
import time
from datetime import date

from . import market_data, runtime
from .core import add_up, day_of, session_in_progress, today_iso
from .portfolio import (APORTE_MODES, BENCHMARK, FI_LABELS, FX, JCP_TAX, MARKETS, PERIODS, add_months,
                        analyze_portfolio, batch_rows, class_members, close_on_or_before, contribution_items,
                        contribution_plan, dividends_to_receive, fixed_income_class, has_entries, is_iso_date,
                        market_of, normalize_ticker, parse_number, period_start, rebase_series, suggest_class,
                        targets_off, tx_flow)
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
    return {
        "today": today_iso(),
        "hasEntries": has_entries(portfolio),
        "classes": [{"name": c["name"], "target": c["target"], "members": class_members(portfolio, c["name"])}
                    for c in classes],
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
            "currency": MARKETS[market]["currency"], "suggestedClass": suggest_class(ticker, market, portfolio["classes"])}


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


def _signature(portfolio):
    return json.dumps(portfolio, sort_keys=True, ensure_ascii=False)


def full_analysis(portfolio, reuse=False):
    """Carrega os dados e analisa a carteira. reuse: devolve a última análise se a carteira
    não mudou (usado pelo aporte)."""
    signature = _signature(portfolio)
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
    analysis = analyze_portfolio(portfolio, histories, rates)
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
                {"key": i["key"], "label": i["label"], "pct": i["value"] / total * 100 if total else 0,
                 "target": i["target"], "autoTarget": i["autoTarget"]} for i in members]})
    return {"groups": groups, "warnings": warnings}


def analysis_payload(portfolio):
    """O que a Visão geral, os Proventos recebidos e a Meta por ativo mostram."""
    a = full_analysis(portfolio)
    series = a["series"]
    return {
        "open": [{k: v for k, v in p.items() if k != "setup"} for p in a["open"]],
        "fixed": [{k: v for k, v in f.items() if k != "entries"} for f in a["fixed"]],
        "dividends": a["dividends"],
        "dividendTotals": sorted(a["divByTicker"].items(), key=lambda kv: -kv[1]),
        "allocation": a["allocation"], "targetSum": a["targetSum"], "targetsOff": targets_off(a["targetSum"]),
        "kpis": a["kpis"], "hasForeign": a["hasForeign"],
        "chart": _chart(series), "lastDay": series["dates"][-1] if series else None,
        "live": a["live"], "failed": a["failed"], "loadedAt": a["loadedAt"],
        "assetTargets": _asset_target_groups(portfolio, a),
    }


def contribution(portfolio, amount, mode, base, selected):
    """Plano de aporte para os campos do painel, com as linhas prontas para "Registrar compras"."""
    a = full_analysis(portfolio, reuse=True)
    amount = max(0, parse_number(amount, 0) or 0)
    mode = mode if mode in APORTE_MODES else "todos"
    items, warnings = contribution_items(portfolio, a, base)
    plan = contribution_plan(portfolio["classes"], items, a["kpis"]["total"], amount, mode, selected or [])
    can_register = not plan["waiting"] and any(r["kind"] == "quote" and r["eligible"] and (r["shares"] or 0) > 0
                                               for r in plan["rows"])
    for r in plan["rows"]:
        r.pop("setup", None)
    return {**plan, "warnings": warnings, "canRegister": can_register, "targetsOff": targets_off(plan["targetSum"]),
            "classDiff": add_up(c["diff"] for c in plan["classRows"]),
            "sells": [r["label"] for r in plan["rows"] if r["status"]["key"] == "sell"],
            "batchRows": batch_rows(plan, a["open"], today_iso()) if can_register else []}


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
    return {"rows": rows, "failed": failed, "total": add_up(r["total"] for r in rows),
            "net": add_up(r["net"] for r in rows), "expected": add_up(r["total"] for r in rows if not r["confirmed"]),
            "jcpTax": JCP_TAX}
