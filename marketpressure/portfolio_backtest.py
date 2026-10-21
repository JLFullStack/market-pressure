"""Backtest de carteira (aba Backtest, modo "Carteira"): a carteira que segue o ranking do screener
ao longo do período, com um único caixa, como na vida real.

Ranking do dia: em cada pregão, os ativos que o screener mostraria naquele dia — volume financeiro
médio dos 20 pregões até ali acima do piso do tipo (minAvgVolume; fundos minAvgVolumeByKind), ETFs
sem os de renda fixa (amplitude), no setup — ordenados pelo Score Composto do dia (pressão × 0,6 +
taxa de acerto × 0,4, calculados só com o histórico até ali), os topN (20) de cada tipo. Os
candidatos são todos os ativos da lista da B3 salva (tabela universe), não só os líquidos de hoje:
um ativo entra no ranking nos períodos em que era líquido. Empresas que saíram da bolsa não estão
no Yahoo, então ainda há algum viés de sobrevivência.

Cada ativo do ranking compra na abertura seguinte com uma fatia do patrimônio (position) enquanto
houver caixa, do maior Score para o menor. Recompras no mesmo ativo seguem a regra do screener (5%
longe das compras abertas) e há um limite de ativos diferentes. A venda parcial no alvo e a saída
pela regra B devolvem o dinheiro ao caixa, que paga as compras seguintes; o caixa parado rende CDI
(opcional); proventos entram no caixa na data-ex; cada compra e venda paga o custo informado;
aportes mensais (opcionais) entram no primeiro pregão de cada mês.

A mesma carteira roda com a regra escolhida e só com a regra B, e é comparada com o BOVA11 e o CDI
recebendo os mesmos aportes.
"""

import logging
import math
from datetime import date

from . import database as db
from . import market_data, runtime
from . import screener as sc
from . import strategy as st
from .core import (EXIT, entry_stats, entry_warnings, add_up, adjusted_candles, closed_candles, compute_indicators, day_of, evaluate_setup,
                   exit_signal, fmt_brl, fmt_date, fmt_num, fmt_sessions)

log = logging.getLogger(__name__)

UNIVERSES = {"acoes": "Ranking de ações", "fundos": "Ranking de fundos listados", "etfs": "Ranking de ETFs",
             "bdrs": "Ranking de BDRs", "todos": "Rankings de todos os tipos", "lista": "Lista de ativos"}
BENCHMARK = "BOVA11"
# Comparações: cada índice recebe os mesmos aportes, nas mesmas datas, sem vender.
INDEXES = {"bova": ("BOVA11", "BOVA11 (mesmos aportes)"), "ivvb": ("IVVB11", "IVVB11 (mesmos aportes)")}
# Com metas por classe, os índices na mesma proporção: a régua justa para uma carteira com internacionais.
MIX_INDEX = {"acoes": "BOVA11", "fundos": "BOVA11", "etfs": "BOVA11", "internacionais": "IVVB11"}
# Imposto de renda (opcional): 15% sobre o ganho líquido do mês em ações, ETFs e BDRs (ações isentas
# quando as vendas de ações do mês somam até R$ 20 mil), 20% em fundos imobiliários; prejuízos
# compensados nos meses seguintes (fundos à parte); pago no primeiro pregão do mês seguinte. O CDI
# do caixa rende líquido de cashRate (renda fixa de mais de 2 anos).
TAX = {"rate": 0.15, "fiiRate": 0.20, "stockExemption": 20_000, "cashRate": 0.15}
MAX_POSITION = 0.10    # nos testes de robustez, fatias acima de 10% renderam bem menos
MAX_TICKERS = 400
MIN_ORDER = 0.5        # compra só se houver caixa para pelo menos metade da fatia
LOT_MARGIN = 40        # pregões mostrados antes e depois de uma operação ao focá-la no gráfico
MARKER_LETTER = {"buy": "C", "partial": "P", "exit": "V"}
MARKER_ORDER = ("buy", "partial", "exit")
_signals_cache = {}    # (ticker, strict, pregões, último dia) → sinais (calculá-los é a parte cara)


# ---------------------------------------------------------------- Parâmetros


def _number(body, key, default, low, high, label):
    raw = body.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).replace(",", "."))
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} deve ficar entre {fmt_num(low, 0)} e {fmt_num(high, 0)}.")
    return value


def parse_params(body):
    """Campos do formulário → parâmetros validados (ValueError com a mensagem para a página)."""
    universe = body.get("universe") or "acoes"
    if universe not in UNIVERSES:
        raise ValueError("Escolha os ativos da carteira.")
    tickers = []
    if universe == "lista":
        for t in str(body.get("tickers") or "").replace(";", ",").replace("\n", ",").split(","):
            t = "".join(ch for ch in t.strip().upper().removesuffix(".SA") if ch.isalnum())
            if t and t not in tickers:
                tickers.append(t)
        if not tickers:
            raise ValueError("Informe os ativos da lista, separados por vírgula.")
        if len(tickers) > MAX_TICKERS:
            raise ValueError(f"Use no máximo {MAX_TICKERS} ativos.")
    start, end = body.get("from") or None, body.get("to") or None
    for value in (start, end):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("Informe as datas do período como AAAA-MM-DD.") from None
    if start and end and start >= end:
        raise ValueError("O início do período deve ser antes do fim.")
    params = {
        "universe": universe, "tickers": tickers, "start": start, "end": end,
        "initial": _number(body, "initial", 10_000, 100, 1e9, "O capital inicial"),
        "monthly": _number(body, "monthly", 0, 0, 1e8, "O aporte mensal"),
        "position": _number(body, "position", 10, 1, 100, "A fatia por compra (%)") / 100,
        "maxAssets": int(_number(body, "maxAssets", 10, 1, 100, "O máximo de ativos")),
        "topN": int(_number(body, "topN", sc.CONFIG["topN"], 1, 100, "O tamanho do ranking")),
        "cost": _number(body, "cost", 0.1, 0, 5, "O custo por operação (%)") / 100,
        "target": _number(body, "target", EXIT["partialTarget"] * 100, 0.5, 500, "O alvo da venda parcial (%)") / 100,
        "fraction": _number(body, "fraction", EXIT["partialFraction"] * 100, 0, 100, "A parte vendida no alvo (%)") / 100,
        "strict": bool(body.get("strict")),
        "cdi": body.get("cdi", True) is not False,
        "liquidity": _liquidity(body.get("liquidity")),
        "exit": _exit(body.get("saida")),
        # Seguir a Indicação do Screener: pula as compras que seriam AGUARDAR (tendência fraca ou
        # mais fraco que o BOVA11, fora dos internacionais) e compra uma fatia prioritySize × maior
        # no Top priorityRank do tipo ou com Score ≥ priorityScore.
        "advice": body.get("indicacao", True) is not False,
        "classes": _classes(body),
        "window": _window(body.get("janela")),   # só o teste de robustez usa
        "tax": body.get("ir") is True,
    }
    if params["exit"] == "config":
        # Saída das Configurações: a regra de cada grupo (reversão = sem venda parcial).
        settings = st.load()
        params["rules"] = {group: (rule["target"], rule["fraction"] if rule["key"] == "parcial" else 0.0)
                           for group in st.GROUPS for rule in [st.for_group(settings, group)]}
    return params


def _classes(body):
    """Metas por classe (grupos de strategy.GROUPS, em % do patrimônio) quando a alocação é "classes";
    None = limite de ativos (maxAssets)."""
    if (body.get("alocacao") or "ativos") != "classes":
        return None
    raw = body.get("metas") or {}
    if not isinstance(raw, dict):
        raise ValueError("Informe a meta de cada classe.")
    out = {}
    for group, label in st.GROUPS.items():
        out[group] = _number(raw, group, 0, 0, 100, f"A meta de {label} (%)") / 100
    total = add_up(out.values())
    if total <= 0 or total > 1.0001:
        raise ValueError("As metas por classe devem somar entre 1% e 100%.")
    return out


def _exit(value):
    value = str(value or "config")
    if value not in ("config", "manual"):
        raise ValueError("Escolha a saída: a das Configurações ou a informada no formulário.")
    return value


def _liquidity(value):
    value = str(value or "padrao")
    if value not in sc.LIQUIDITY:
        raise ValueError("Escolha a liquidez mínima.")
    return value


# ---------------------------------------------------------------- Ativos e sinais


def universe_assets(params):
    """[{ticker, kind, name}] candidatos: todos os ativos do tipo na lista da B3 salva (tabela
    universe; sem ela, os do último screener), ou a lista informada."""
    saved = {}
    try:
        saved = {u["ticker"]: u for u in db.read_table("universe")}
    except Exception:
        log.warning("não foi possível ler a lista de ativos salva", exc_info=True)
    if params["universe"] == "lista":
        out = []
        for t in params["tickers"]:
            u = saved.get(t) or {}
            out.append({"ticker": t, "kind": u.get("kind") or market_data.asset_kind(t) or "acoes",
                        "name": u.get("name") or t})
        return out
    if saved:
        assets = [{"ticker": u["ticker"], "kind": u.get("kind") or "acoes", "name": u.get("name") or u["ticker"]}
                  for u in saved.values() if (u.get("kind") or "acoes") in sc.KINDS]
    else:
        scan = sc.scan_job.scan or {"analyses": []}
        assets = [{"ticker": a["ticker"], "kind": a.get("kind") or "acoes", "name": a.get("name") or a["ticker"]}
                  for a in scan["analyses"]]
    kind = params["universe"]
    return sorted((a for a in assets if kind == "todos" or a["kind"] == kind), key=lambda a: a["ticker"])


def _rolling_mean(values, window):
    """Média móvel simples (soma acumulada); None antes de completar a janela."""
    out, total = [None] * len(values), 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        if i >= window - 1:
            out[i] = total / window
    return out


def signals(asset, strict):
    """Sinais de um ativo em cada pregão fechado: se ele estaria no ranking do screener naquele
    dia (liquidez dos últimos pregões, ETF de renda fixa, setup), o Score do dia (sem olhar o
    futuro) e a venda pela regra B. Execução no preço bruto; fundos têm os sinais no preço ajustado,
    como no screener. None se o histórico não basta; {"illiquid": True} se nunca teve a liquidez."""
    raw = closed_candles(market_data.history(asset["ticker"])["candles"])
    if len(raw) < sc.CONFIG["minCandles"]:
        return None
    days = [day_of(c["date"]) for c in raw]
    key = (asset["ticker"], strict, len(raw), days[-1])
    if key in _signals_cache:
        return _signals_cache[key]
    n = len(raw)
    # Os sinais são calculados a partir do menor piso possível (scanFloor); o piso escolhido na
    # página é aplicado no ranking do dia (simulate), então mudar a liquidez não recalcula nada.
    traded = _rolling_mean([c["c"] * c["v"] for c in raw], sc.CONFIG["liquidityWindow"])
    floor = sc.CONFIG["scanFloor"]
    eligible = [i >= sc.CONFIG["backtestStart"] and traded[i] is not None and traded[i] >= floor for i in range(n)]
    if asset["kind"] == "etfs":  # ETF de renda fixa (amplitude diária média baixa) não entra no ranking
        ranges = _rolling_mean([(c["h"] - c["l"]) / c["c"] for c in raw], sc.CONFIG["etfRangeWindow"])
        eligible = [e and ranges[i] is not None and ranges[i] >= sc.CONFIG["etfMinRange"] for i, e in enumerate(eligible)]
    if not any(eligible):
        _signals_cache[key] = {"illiquid": True}
        return _signals_cache[key]
    candles = adjusted_candles(raw) if asset["kind"] in sc.CONFIG["adjustDividends"] else raw
    ind = compute_indicators(candles)
    weights = sc.CONFIG["pressureWeights"].get(asset["kind"], sc.CONFIG["pressureWeights"]["default"])
    # Taxa de acerto de cada dia: só as compras do backtest do screener já encerradas até ele.
    closed_at = {}
    for lot in sc.backtest_lots(ind, strict):
        if lot["exit"]:
            r = sc.lot_return(lot, sc.CONFIG["partialFraction"])
            closed_at.setdefault(lot["exit"]["i"], []).append(r > 0)
    ok, score, wins, trades = [False] * n, [None] * n, 0, 0
    entry = {}   # filtro de entrada da Indicação no dia do sinal: (entry_stats, média testada)
    for i in range(n):
        for won in closed_at.get(i, ()):
            wins += won
            trades += 1
        if not eligible[i]:
            continue
        setup = evaluate_setup(ind, i)
        if setup["okStrict"] if strict else setup["ok"]:
            ok[i] = True
            entry[i] = (entry_stats(ind["closes"], ind["mm200"], i), setup["ma"]["name"])
            win_rate = (wins + sc.CONFIG["priorWinRate"] * sc.CONFIG["priorWeight"]) / (trades + sc.CONFIG["priorWeight"]) * 100
            score[i] = sc.pressure(ind, i, setup, False, weights)["total"] * 0.6 + win_rate * 0.4
    divs = {}
    for d in market_data.history(asset["ticker"])["dividends"]:
        divs[day_of(d["date"])] = divs.get(day_of(d["date"]), 0) + d["amount"]
    out = {"ticker": asset["ticker"], "kind": asset["kind"], "name": asset.get("name"), "candles": raw, "days": days,
           "idx": {d: i for i, d in enumerate(days)}, "ok": ok, "exit": [exit_signal(ind, i) for i in range(n)],
           "score": score, "divs": divs, "traded": traded, "entry": entry}
    _signals_cache[key] = out
    return out


def daily_ranking(candidates, top_n):
    """Ranking do dia: por tipo, do maior Score para o menor, os top_n. candidates: [(ticker, score, kind)].
    Devolve [(ticker, score, posição no ranking do tipo)], do maior Score para o menor."""
    by_kind = {}
    for t, score, kind in candidates:
        by_kind.setdefault(kind, []).append((t, score))
    ranked = []
    for items in by_kind.values():
        items.sort(key=lambda x: -x[1])
        ranked += [(t, score, pos) for pos, (t, score) in enumerate(items[:top_n], 1)]
    return sorted(ranked, key=lambda x: -x[1])


# ---------------------------------------------------------------- Simulação


def _first_sessions(cal):
    """Primeiro pregão de cada mês (dia dos aportes mensais)."""
    seen, out = set(), set()
    for d in cal:
        if d[:7] not in seen:
            seen.add(d[:7])
            out.add(d)
    return out


def _cash_yield(rate, params):
    """Rendimento do caixa no dia: o CDI, líquido do imposto de renda quando ele é descontado."""
    return rate * (1 - TAX["cashRate"]) if params.get("tax") else rate


def _avg_cash(curve):
    """Parte média do patrimônio parada em caixa (%)."""
    return add_up(p["cash"] / p["equity"] for p in curve if p["equity"] > 0) / len(curve) * 100


def simulate(data, cal, params, fraction, cdi):
    """Uma carteira do primeiro ao último dia de cal. Devolve a curva diária (patrimônio, aportado,
    caixa), os aportes e as operações (uma por compra). fraction None = a regra de cada tipo
    (params["rules"]: {grupo: (alvo, parte vendida)}, grupo pelo tipo e nome do ativo,
    screener.rule_group); um número = a mesma parte para todos."""
    target, cost, reentry = params["target"], params["cost"], sc.CONFIG["reentryDistance"]
    rules = params.get("rules") if fraction is None else None
    classes, bench6m = params.get("classes"), params.get("bench6m") or {}
    params.setdefault("skipped", 0)

    groups = {}

    def group_of(t):
        if t not in groups:
            groups[t] = sc.rule_group(data[t])
        return groups[t]

    def rule_of(t):
        if not rules:
            return target, fraction
        return rules.get(group_of(t), (target, 0.0))
    months = _first_sessions(cal)
    # Imposto de renda: ganho de cada venda (valor líquido − custo da parte vendida) no mês, por grupo.
    carry = {"comum": 0.0, "fii": 0.0}           # prejuízo a compensar
    month = {"comum": 0.0, "acoes": 0.0, "fii": 0.0, "stockSales": 0.0}
    tax_paid = 0.0

    def book(t, value, basis):
        if not params.get("tax"):
            return
        kind = data[t]["kind"]
        if kind == "fundos":
            month["fii"] += value - basis
        elif kind == "acoes":
            month["acoes"] += value - basis
            month["stockSales"] += value
        else:
            month["comum"] += value - basis

    def settle():
        """Imposto do mês fechado (e zera o mês): ações isentas com vendas até o limite."""
        stock = month["acoes"]
        if stock > 0 and month["stockSales"] <= TAX["stockExemption"]:
            stock = 0.0
        tax = 0.0
        for bucket, net, rate in (("comum", month["comum"] + stock, TAX["rate"]), ("fii", month["fii"], TAX["fiiRate"])):
            net -= carry[bucket]
            if net > 0:
                tax += net * rate
                carry[bucket] = 0.0
            else:
                carry[bucket] = -net
        month.update(comum=0.0, acoes=0.0, fii=0.0, stockSales=0.0)
        return tax

    cash = contributed = params["initial"]
    flows = [(cal[0], params["initial"])]
    hold, trades, last_px = {}, [], {}
    pending_sell, pending_buy = set(), []
    curve = []
    last_day, previous = cal[-1], None
    for d in cal:
        cash *= 1 + _cash_yield(cdi.get(previous, 0.0), params)  # a taxa de um dia rende no pregão seguinte
        previous = d
        today = [(t, a["idx"][d]) for t, a in data.items() if d in a["idx"]]
        for t, i in today:
            a = data[t]
            if t in hold and d in a["divs"]:
                for lot in hold[t]:
                    value = lot["qty"] * a["divs"][d]
                    cash += value
                    trades[lot["trade"]]["dividends"] += value
        if params.get("tax") and d in months and d != cal[0]:
            tax = settle()   # DARF do mês anterior
            cash -= tax
            tax_paid += tax
        if d in months and d != cal[0] and params["monthly"] > 0:
            cash += params["monthly"]
            contributed += params["monthly"]
            flows.append((d, params["monthly"]))
        # Vendas pela regra B (sinal no fechamento anterior), na abertura.
        for t, i in today:
            if t in pending_sell and t in hold:
                price = data[t]["candles"][i]["o"]
                for lot in hold.pop(t):
                    value = lot["qty"] * price * (1 - cost)
                    cash += value
                    book(t, value, lot["basis"])
                    trade = trades[lot["trade"]]
                    trade.update(exit={"date": d, "price": price, "i": i}, proceeds=trade["proceeds"] + value)
        pending_sell.clear()
        equity = cash + add_up(lot["qty"] * last_px[t] for t, lots in hold.items() for lot in lots)
        value = {t: add_up(lot["qty"] * last_px[t] for lot in lots) for t, lots in hold.items()}
        # Compras do ranking do dia anterior, na abertura, do maior Score para o menor.
        for t, score, rank, mult in pending_buy:
            a = data[t]
            if d not in a["idx"]:
                continue
            i = a["idx"][d]
            price = a["candles"][i]["o"]
            lots = hold.get(t)
            if lots and any(abs(price / lot["price"] - 1) < reentry for lot in lots):
                continue
            if not classes and not lots and len(hold) >= params["maxAssets"]:
                continue
            slot = equity * params["position"] * mult
            amount = min(slot, cash)
            if classes:
                g = group_of(t)
                room = classes.get(g, 0.0) * equity - add_up(v for x, v in value.items() if group_of(x) == g)
                amount = min(amount, room)
            if amount < slot * MIN_ORDER or amount <= 0:
                continue
            cash -= amount
            value[t] = value.get(t, 0.0) + amount
            trades.append({"ticker": t, "entry": {"date": d, "price": price, "i": i}, "invested": amount,
                           "partial": None, "exit": None, "proceeds": 0.0, "dividends": 0.0, "score": score, "rank": rank,
                           "peakClose": price, "peakHigh": price})
            hold.setdefault(t, []).append({"qty": amount * (1 - cost) / price, "price": price, "i": i, "hit": False,
                                           "trade": len(trades) - 1, "basis": amount})
        pending_buy = []
        # Venda parcial no alvo (máxima de um pregão depois do da compra) e sinais no fechamento.
        candidates = []
        for t, i in today:
            a = data[t]
            c = a["candles"][i]
            last_px[t] = c["c"]
            tg, fr = rule_of(t)
            for lot in hold.get(t, ()):
                trade = trades[lot["trade"]]
                trade["peakClose"] = max(trade["peakClose"], c["c"])
                trade["peakHigh"] = max(trade["peakHigh"], c["h"])
                goal = lot["price"] * (1 + tg)
                if fr > 0 and not lot["hit"] and i > lot["i"] and c["h"] >= goal:
                    price = max(goal, c["o"])
                    qty = lot["qty"] * fr
                    value = qty * price * (1 - cost)
                    cash += value
                    book(t, value, lot["basis"] * fr)
                    lot["basis"] -= lot["basis"] * fr
                    lot["qty"] -= qty
                    lot["hit"] = True
                    trade.update(partial={"date": d, "price": price, "i": i}, proceeds=trade["proceeds"] + value)
            if d == last_day or i + 1 >= len(a["candles"]):
                continue
            if t in hold and a["exit"][i]:
                pending_sell.add(t)
            elif a["ok"][i] and a["traded"][i] >= sc.liquidity_floor(a["kind"], params["liquidity"]):
                candidates.append((t, a["score"][i], a["kind"]))
        pending_buy = []
        for t, score, rank in daily_ranking(candidates, params["topN"]):
            mult = 1.0
            if params.get("advice"):
                a = data[t]
                i = a["idx"][d]
                if group_of(t) not in sc.CONFIG["adviceIgnoreKinds"] and entry_warnings(*a["entry"][i], bench6m.get(d)):
                    params["skipped"] += 1
                    continue
                if rank <= sc.CONFIG["priorityRank"] or (score or 0) >= sc.CONFIG["priorityScore"]:
                    mult = sc.CONFIG["prioritySize"]
            pending_buy.append((t, score, rank, mult))
        invested_now = add_up(lot["qty"] * last_px[t] for t, lots in hold.items() for lot in lots)
        curve.append({"date": d, "equity": cash + invested_now, "contributed": contributed, "cash": cash})
    for t, lots in hold.items():  # compras ainda abertas: pelo último fechamento
        for lot in lots:
            trade = trades[lot["trade"]]
            trade["openValue"] = trade.get("openValue", 0.0) + lot["qty"] * last_px[t]
            book(t, lot["qty"] * last_px[t] * (1 - cost), lot["basis"])   # como se vendesse no fim
    # Imposto pago no período e o que ainda seria devido vendendo tudo no último dia (o mês em
    # aberto + as compras abertas), para comparar com os índices, que só pagam quando vendem.
    params["taxPaid"], params["taxDue"] = (tax_paid, settle()) if params.get("tax") else (None, None)
    for trade in trades:
        a = data[trade["ticker"]]
        end = trade["exit"]["i"] if trade["exit"] else a["idx"].get(last_day, len(a["candles"]) - 1)
        trade["profit"] = trade["proceeds"] + trade.get("openValue", 0.0) + trade["dividends"] - trade["invested"]
        trade["return"] = trade["profit"] / trade["invested"] * 100
        trade["open"] = trade["exit"] is None
        trade["days"] = end - trade["entry"]["i"]
        trade["maxGain"] = (trade["peakHigh"] / trade["entry"]["price"] - 1) * 100
        trade["giveback"] = (1 - trade["exit"]["price"] / trade["peakClose"]) * 100 if trade["exit"] else None
        trade["range"] = {"from": a["days"][max(0, trade["entry"]["i"] - LOT_MARGIN)],
                          "to": a["days"][min(len(a["days"]) - 1, end + LOT_MARGIN)]}
    return curve, flows, trades


def _irr(flows, final, end):
    """Taxa interna de retorno ao ano (aportes como entradas, patrimônio final como saída)."""
    t_end = date.fromisoformat(end)

    def npv(r):
        return add_up(v * (1 + r) ** ((t_end - date.fromisoformat(d)).days / 365.25) for d, v in flows) - final
    lo, hi = -0.99, 10.0
    if npv(lo) > 0 or npv(hi) < 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            hi = mid
        else:
            lo = mid
    return mid * 100


def _max_drawdown(curve, flows):
    """Maior queda do patrimônio descontando os aportes (pela cota, como a rentabilidade da carteira)."""
    flow = dict(flows)
    units = peak = worst = 0.0
    for p in curve:
        f = flow.get(p["date"], 0.0)
        quota = (p["equity"] - f) / units if units else 1.0
        units += f / quota if quota else 0
        q = p["equity"] / units if units else 1.0
        peak = max(peak, q)
        worst = min(worst, q / peak - 1) if peak else worst
    return worst * 100


def _yearly(curve, flows):
    """Resultado de cada ano civil: patrimônio no início (fim do ano anterior; 0 no primeiro),
    aportes do ano, patrimônio no fim, lucro em reais (fim − início − aportes) e rentabilidade (%)
    pela cota, que desconta os aportes (como a rentabilidade da carteira; a cota começa em 1). O
    primeiro e o último ano podem ser parciais (from/to)."""
    flow = dict(flows)
    units, years = 0.0, {}
    for p in curve:
        f = flow.get(p["date"], 0.0)
        quota = (p["equity"] - f) / units if units else 1.0
        if f and quota:
            units += f / quota
        y = years.setdefault(p["date"][:4], {"from": p["date"], "flows": 0.0})
        y["flows"] += f
        y.update(to=p["date"], end=p["equity"], quota=p["equity"] / units if units else 1.0)
    out, previous, previous_quota = [], 0.0, 1.0
    for year in sorted(years):
        y = years[year]
        out.append({"year": year, "from": y["from"], "to": y["to"], "start": previous, "contributed": y["flows"],
                    "end": y["end"], "profit": y["end"] - previous - y["flows"],
                    "return": (y["quota"] / previous_quota - 1) * 100})
        previous, previous_quota = y["end"], y["quota"]
    return out


def _metrics(curve, flows, end, trades=None, tax=None):
    """tax: None (sem imposto) ou (pago no período, devido vendendo tudo no fim); com ele, também o
    lucro e o retorno ao ano líquidos (profitNet, irrNet)."""
    final, contributed = curve[-1]["equity"], curve[-1]["contributed"]
    out = {"final": final, "contributed": contributed, "profit": final - contributed,
           "profitPct": (final - contributed) / contributed * 100, "irr": _irr(flows, final, end),
           "maxDrawdown": _max_drawdown(curve, flows)}
    if tax is not None:
        paid, due = tax
        out.update(taxPaid=paid, taxDue=due, profitNet=final - due - contributed, irrNet=_irr(flows, final - due, end))
    if trades is not None:
        closed = [t for t in trades if not t["open"]]
        returns = sorted(t["return"] for t in closed)
        wins = sum(1 for r in returns if r > 0)
        avg = lambda values: add_up(values) / len(values) if values else None
        avg_days = avg([t["days"] for t in closed])
        out.update({
            "trades": len(trades), "closed": len(closed), "open": len(trades) - len(closed), "wins": wins,
            "winRate": wins / len(closed) * 100 if closed else None,
            "avgReturn": avg(returns), "medianReturn": returns[len(returns) // 2] if returns else None,
            "best": returns[-1] if returns else None, "worst": returns[0] if returns else None,
            "closedProfit": add_up(t["profit"] for t in closed),
            "openProfit": add_up(t["profit"] for t in trades if t["open"]),
            "assets": len({t["ticker"] for t in trades}),
            "avgCash": _avg_cash(curve),
            "partials": sum(1 for t in trades if t["partial"]),
            "exits": len({(t["ticker"], t["exit"]["date"]) for t in closed}),
            "dividends": add_up(t["dividends"] for t in trades),
            "avgDays": avg_days, "avgDuration": fmt_sessions(avg_days) if avg_days is not None else None,
            "avgGiveback": avg([t["giveback"] for t in closed]),
            "avgMaxGain": avg([t["maxGain"] for t in trades]),
        })
    return out


def _benchmark(cal, flows, prices):
    """Mesmos aportes num único ativo (prices: {dia: preço}, último conhecido nos dias sem preço)."""
    flow = dict(flows)
    units, price, curve, contributed = 0.0, None, [], 0.0
    for d in cal:
        price = prices.get(d, price)
        f = flow.get(d, 0.0)
        contributed += f
        if f and price:
            units += f / price
        curve.append({"date": d, "equity": units * (price or 0), "contributed": contributed, "cash": 0.0})
    return curve


def _mix_curve(cal, flows, weights, prices):
    """Mesmos aportes divididos entre índices (weights: {ticker: fração}, prices: {ticker: {dia: preço}}),
    sem rebalancear: cada aporte compra cada índice na sua proporção."""
    flow = dict(flows)
    units = {t: 0.0 for t in weights}
    last = {t: None for t in weights}
    curve, contributed = [], 0.0
    for d in cal:
        for t in weights:
            last[t] = prices[t].get(d, last[t])
        f = flow.get(d, 0.0)
        contributed += f
        for t, w in weights.items():
            if f and last[t]:
                units[t] += f * w / last[t]
        curve.append({"date": d, "equity": add_up(units[t] * (last[t] or 0) for t in weights), "contributed": contributed, "cash": 0.0})
    return curve


def _index_prices(bench, warnings):
    """Preço ajustado por proventos dos índices de comparação ({ticker: {dia: preço}}); um índice
    sem histórico fica de fora com um aviso."""
    out = {BENCHMARK: {day_of(c["date"]): c["adj"] for c in bench}}
    for ticker, _label in INDEXES.values():
        if ticker in out:
            continue
        try:
            out[ticker] = {day_of(c["date"]): c["adj"] for c in closed_candles(market_data.history(ticker)["candles"])}
        except Exception as err:
            log.warning("histórico de %s indisponível para o backtest de carteira", ticker, exc_info=True)
            warnings.append(f"{ticker} indisponível ({err}): a comparação com ele ficou de fora.")
    return out


def _mix_weights(classes):
    """Metas por classe → {índice: fração} (fundos e ETFs Brasil no BOVA11, internacionais no IVVB11)."""
    total = add_up(classes.values())
    weights = {}
    for group, w in classes.items():
        if w > 0:
            weights[MIX_INDEX.get(group, BENCHMARK)] = weights.get(MIX_INDEX.get(group, BENCHMARK), 0.0) + w / total
    return weights


def _comparisons(cal, flows, params, prices, cdi):
    """As linhas de comparação com os mesmos aportes: BOVA11, IVVB11, os índices na proporção das
    metas (com metas por classe) e o CDI. → [(chave, rótulo, curva)]."""
    out = [(key, label, _benchmark(cal, flows, prices[ticker])) for key, (ticker, label) in INDEXES.items() if ticker in prices]
    weights = _mix_weights(params["classes"]) if params.get("classes") else {}
    if weights and all(t in prices for t in weights):
        label = " + ".join(f"{fmt_num(w * 100, 0)}% {t}" for t, w in sorted(weights.items(), key=lambda x: -x[1]))
        out.append(("mix", f"Índices nas metas ({label})", _mix_curve(cal, flows, weights, prices)))
    if cdi:
        out.append(("cdi", "CDI (mesmos aportes)", _cdi_curve(cal, flows, cdi)))
    return out


def _comparison_tax(params, curve):
    """Imposto dos índices e do CDI, só quando vendidos no fim: 15% sobre o ganho (sem nada pago antes)."""
    if not params.get("tax"):
        return None
    return 0.0, max(0.0, curve[-1]["equity"] - curve[-1]["contributed"]) * TAX["rate"]


def _position_warning(params):
    if params["position"] > MAX_POSITION:
        pct = round(params["position"] * 100, 4)
        return (f"Fatia de {fmt_num(pct, 0 if float(pct).is_integer() else 1)}% por compra: nos testes de robustez, fatias acima de "
                f"{fmt_num(MAX_POSITION * 100, 0)}% renderam bem menos (com poucas posições, e 1,5x nas compras com prioridade, "
                "um ou dois ativos passam a decidir o resultado).")
    return None


def _cdi_curve(cal, flows, cdi):
    flow = dict(flows)
    value, curve, contributed, previous = 0.0, [], 0.0, None
    for d in cal:
        value *= 1 + cdi.get(previous, 0.0)
        previous = d
        f = flow.get(d, 0.0)
        value += f
        contributed += f
        curve.append({"date": d, "equity": value, "contributed": contributed, "cash": value})
    return curve


def _profit_series(curve):
    return [{"time": p["date"], "value": p["equity"] - p["contributed"]} for p in curve]


def _lot_numbers(numbers):
    if len(numbers) == 1:
        return str(numbers[0])
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"{numbers[0]}–{numbers[-1]}"
    return ", ".join(map(str, numbers))


def _markers(rows):
    """Marcadores do gráfico por ativo: C (compra), P (venda parcial), V (saída), com o número da operação."""
    by_ticker = {}
    for r in rows:
        groups = by_ticker.setdefault(r["ticker"], {})
        for kind, point in (("buy", r["entry"]), ("partial", r["partial"]), ("exit", r["exit"])):
            if point:
                groups.setdefault((point["date"], kind), {"price": point["price"], "lots": []})["lots"].append(r["n"])
    return {t: [{"time": d, "kind": k, "price": m["price"], "text": f"{MARKER_LETTER[k]}{_lot_numbers(m['lots'])}"}
                for (d, k), m in sorted(groups.items(), key=lambda kv: (kv[0][0], MARKER_ORDER.index(kv[0][1])))]
            for t, groups in by_ticker.items()}


def _load(params):
    """Sinais dos candidatos e o calendário do BOVA11: o que run e robustness compartilham."""
    assets = universe_assets(params)
    if not assets:
        raise ValueError("Nenhum ativo para simular. Rode o screener ou informe uma lista de ativos.")
    warnings = []
    data, failed, illiquid = {}, [], 0
    for asset, result, err in runtime.run_all(lambda a: signals(a, params["strict"]), assets, sc.CONFIG["batchSize"]):
        if err is not None:
            failed.append(f"{asset['ticker']} ({err})")
        elif result is None:
            failed.append(f"{asset['ticker']} (histórico insuficiente)")
        elif result.get("illiquid"):
            illiquid += 1
        else:
            data[asset["ticker"]] = result
    # Ativos que nunca alcançaram o piso escolhido no período não entram no ranking em dia nenhum.
    for t in [t for t, a in data.items() if not any(
            v is not None and v >= sc.liquidity_floor(a["kind"], params["liquidity"]) for v in a["traded"][sc.CONFIG["backtestStart"]:])]:
        del data[t]
        illiquid += 1
    if not data:
        raise ValueError("Nenhum ativo teve liquidez e histórico suficientes para entrar no ranking.")
    if failed:
        warnings.append(f"{len(failed)} ativo(s) ficaram de fora por falta de histórico (listados há pouco tempo ou sem dados no Yahoo): "
                        f"{', '.join(failed[:10])}{'...' if len(failed) > 10 else ''}.")

    if (msg := _position_warning(params)):
        warnings.append(msg)
    bench = closed_candles(market_data.history(BENCHMARK)["candles"])
    bench_days = [day_of(c["date"]) for c in bench]
    return {"assets": assets, "data": data, "illiquid": illiquid, "warnings": warnings, "bench": bench,
            "prices": _index_prices(bench, warnings),
            "bench_days": bench_days, "first_day": bench_days[sc.CONFIG["backtestStart"]], "last_day": bench_days[-1]}


def _cdi_rates(start, warnings):
    """CDI diário (fração) desde start; vazio com um aviso quando o Banco Central falha."""
    try:
        return {r["date"]: r["rate"] / 100 for r in market_data.cdi(date.fromisoformat(start))["rates"]}
    except Exception as err:
        log.warning("CDI indisponível para o backtest de carteira", exc_info=True)
        warnings.append(f"CDI indisponível ({err}): o caixa parado não rendeu juros e a linha do CDI ficou de fora.")
        return {}


def _rule_runs(params, data):
    """Simulações a rodar: a regra escolhida e, quando ela tem venda parcial, só a regra B.
    → (runs [(chave, rótulo, parte vendida ou None = a de cada grupo)], rótulo da regra, tem parcial)."""
    pct = lambda v: fmt_num(v * 100, 0 if float(round(v * 100, 4)).is_integer() else 1)
    if params.get("rules"):
        fraction = None
        used = {sc.rule_group(data[t]) for t in data}
        has_partial = any(params["rules"].get(k, (0, 0.0))[1] > 0 for k in used)
        rule_label = "Saída das Configurações (por tipo)"
    else:
        fraction = params["fraction"]
        has_partial = fraction > 0
        rule_label = f"{pct(fraction)}% em +{pct(params['target'])}% + regra B" if fraction > 0 else "Só a regra B"
    runs = [("rule", rule_label, fraction)]
    if has_partial:
        runs.append(("onlyB", "Só a regra B", 0.0))
    return runs, rule_label, has_partial


def _params_out(params):
    """Os parâmetros como a página e o prompt de análise os mostram (percentuais em %)."""
    return ({k: params[k] for k in ("universe", "tickers", "initial", "monthly", "maxAssets", "topN", "strict", "cdi", "liquidity", "exit",
                                    "advice", "tax")}
            | {"classes": {g: v * 100 for g, v in params["classes"].items()} if params.get("classes") else None}
            | {"position": params["position"] * 100, "cost": params["cost"] * 100,
               "target": params["target"] * 100, "fraction": params["fraction"] * 100,
               "rules": {k: {"target": v[0] * 100, "fraction": v[1] * 100}
                         for k, v in (params.get("rules") or {}).items()}})


def run(params):
    """Tudo o que o modo Carteira da aba Backtest mostra. ValueError com a mensagem para a página."""
    loaded = _load(params)
    assets, data, illiquid, warnings = (loaded[k] for k in ("assets", "data", "illiquid", "warnings"))
    first_day, last_day = loaded["first_day"], loaded["last_day"]
    start = max(params["start"] or first_day, first_day)
    end = min(params["end"] or last_day, last_day)
    cal = [d for d in loaded["bench_days"] if start <= d <= end]
    if len(cal) < 2:
        raise ValueError("O período não tem pregões suficientes; escolha um início anterior ao fim.")
    note = None
    if params["start"] and params["start"] < first_day:
        note = (f"Início ajustado para {fmt_date(cal[0])}: o histórico do Yahoo tem 10 anos e as médias precisam de "
                f"{sc.CONFIG['backtestStart']} pregões antes da primeira compra.")

    cdi = _cdi_rates(cal[0], warnings) if params["cdi"] else {}
    runs, rule_label, has_partial = _rule_runs(params, data)
    strategies, trades_main, flows = [], [], None
    params["bench6m"] = sc.benchmark_6m() if params.get("advice") else {}
    skipped = 0
    for key, label, f in runs:
        params["skipped"] = 0
        curve, flows, trades = simulate(data, cal, params, f, cdi)
        tax = (params["taxPaid"], params["taxDue"]) if params.get("tax") else None
        strategies.append({"key": key, "label": label, "metrics": _metrics(curve, flows, cal[-1], trades, tax),
                           "curve": _profit_series(curve), "years": _yearly(curve, flows)})
        if key == "rule":
            trades_main, skipped = trades, params["skipped"]
    for key, label, curve in _comparisons(cal, flows, params, loaded["prices"], cdi):
        strategies.append({"key": key, "label": label, "metrics": _metrics(curve, flows, cal[-1], tax=_comparison_tax(params, curve)),
                           "curve": _profit_series(curve), "years": _yearly(curve, flows)})

    compare = None
    if has_partial:
        net = "profitNet" if params.get("tax") else "profit"   # com imposto, o lucro vendendo tudo no fim
        a, b = strategies[0]["metrics"][net], strategies[1]["metrics"][net]
        partial, only_b = f"a estratégia com venda parcial ({rule_label})", "só a regra B"
        if abs(a - b) < 0.005:
            compare = "Com o dinheiro girando entre os ativos do ranking, as duas estratégias deram o mesmo lucro."
        else:
            winner, loser = (partial, only_b) if a > b else (only_b, partial)
            compare = (f"Com o dinheiro girando entre os ativos do ranking, {winner} ganhou {fmt_brl(abs(a - b))} a mais "
                       f"que {loser} no período ({fmt_brl(max(a, b))} contra {fmt_brl(min(a, b))}).")

    rows = []
    for n, t in enumerate(trades_main, 1):
        point = lambda p: p and {"date": p["date"], "price": p["price"], "return": (p["price"] / t["entry"]["price"] - 1) * 100}
        rows.append({
            "n": n, "ticker": t["ticker"], "name": data[t["ticker"]].get("name") or t["ticker"],
            "entry": {"date": t["entry"]["date"], "price": t["entry"]["price"]}, "partial": point(t["partial"]),
            "exit": point(t["exit"]), "rank": t["rank"], "score": t["score"], "invested": t["invested"],
            "profit": t["profit"], "return": t["return"], "dividends": t["dividends"], "open": t["open"],
            "days": t["days"], "duration": fmt_sessions(t["days"]), "maxGain": t["maxGain"], "giveback": t["giveback"],
            "range": t["range"], "status": "Aberta" if t["open"] else "Encerrada",
        })
    traded = {}
    for r in rows:
        traded.setdefault(r["ticker"], {"ticker": r["ticker"], "name": r["name"], "trades": 0, "profit": 0.0})
        traded[r["ticker"]]["trades"] += 1
        traded[r["ticker"]]["profit"] += r["profit"]
    sessions = len(cal) - 1
    return {
        "period": f"{fmt_date(cal[0])} a {fmt_date(cal[-1])}", "sessions": sessions, "duration": fmt_sessions(sessions),
        "bounds": {"min": first_day, "max": last_day, "from": cal[0], "to": cal[-1]}, "note": note,
        "universe": {"label": UNIVERSES[params["universe"]], "candidates": len(assets), "ranked": len(data),
                     "illiquid": illiquid},
        "params": _params_out(params),
        "adviceSkipped": skipped if params.get("advice") else None,
        "strategies": strategies, "compare": compare, "trades": rows, "markers": _markers(rows),
        "assets": sorted(traded.values(), key=lambda x: (-x["trades"], x["ticker"])), "warnings": warnings,
    }


# ---------------------------------------------------------------- Robustez

ROBUST_MIN_SESSIONS = 504     # até o fim: cada início precisa de ~2 anos até o fim do período
ROBUST_WINDOWS = (2, 3, 5)    # janelas de tamanho fixo, em anos
ROBUST_NAMES = {"onlyB": "da regra B sozinha", "bova": "do BOVA11", "ivvb": "do IVVB11", "mix": "dos índices nas metas", "cdi": "do CDI"}
ROBUST_ORDER = ("mix", "bova", "ivvb", "cdi", "onlyB")     # ordem do placar e do resumo
ROBUST_REF = {"mix": "os índices na proporção das metas", "bova": "o BOVA11"}
ROBUST_REF_SHORT = {"mix": "Índices nas metas", "bova": "BOVA11"}


def _window(value):
    """Campo "janela": "fim" (ou vazio) = cada início até o fim do período → 0; senão anos da janela."""
    if value in (None, "", "fim"):
        return 0
    try:
        years = int(value)
    except (TypeError, ValueError):
        years = None
    if years not in ROBUST_WINDOWS:
        raise ValueError("Escolha a janela do teste de robustez.")
    return years


def _robust_periods(days, end, window):
    """Períodos testados. Inícios: o primeiro pregão possível e o primeiro pregão de cada ano seguinte.
    window 0: cada início vai até o fim, enquanto sobrarem ROBUST_MIN_SESSIONS pregões. window N: cada
    início vai até o último pregão antes de completar N anos, e só entram as janelas inteiras."""
    cal = [d for d in days if d <= end]
    starts = [0] + [i for i in range(1, len(cal)) if cal[i][:4] != cal[i - 1][:4]]
    if not window:
        return [cal[i:] for i in starts if len(cal) - 1 - i >= ROBUST_MIN_SESSIONS]
    out = []
    for i in starts:
        first = date.fromisoformat(cal[i])
        limit = first.replace(year=first.year + window, day=min(first.day, 28)).isoformat()
        if cal[-1] < limit:
            break
        out.append([d for d in cal[i:] if d < limit])
    return out


def _period_label(cal, first, window):
    """Rótulo curto do período: o ano de início (MM/AAAA quando não começa em janeiro) e, nas
    janelas, o último ano coberto inteiro ou em parte (2018–20 = 2018, 2019 e 2020)."""
    start = cal[0][:4] if not first or cal[0][5:7] == "01" else f"{cal[0][5:7]}/{cal[0][:4]}"
    if not window:
        return start
    last = date.fromisoformat(cal[0]).year + window - (1 if cal[0][5:7] == "01" else 0)
    return f"{start}–{str(last)[2:]}"


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def robustness(params):
    """A mesma carteira em vários períodos (o "De" do formulário é ignorado): a partir de cada ano de
    início até o mesmo fim, ou em janelas de params["window"] anos. Como poucos ganhos grandes decidem
    o lucro, uma regra só é confiável se ganhar na maioria dos períodos, não só no período inteiro.
    ValueError com a mensagem para a página."""
    window = params.get("window", 0)
    loaded = _load(params)
    data, warnings = loaded["data"], loaded["warnings"]
    end = min(params["end"] or loaded["last_day"], loaded["last_day"])
    periods = _robust_periods(loaded["bench_days"][sc.CONFIG["backtestStart"]:], end, window)
    if window and len(periods) < 2:
        raise ValueError(f"O período é curto demais para comparar janelas de {window} anos; escolha janelas menores.")
    if not periods:
        raise ValueError("O período é curto demais: o teste de robustez precisa de pelo menos 2 anos entre o início possível e o fim.")
    cdi = _cdi_rates(periods[0][0], warnings) if params["cdi"] else {}
    runs, rule_label, has_partial = _rule_runs(params, data)
    params["bench6m"] = sc.benchmark_6m() if params.get("advice") else {}
    tax_on = params.get("tax")

    def keep(m):
        """O que a tabela mostra de cada linha; com imposto, o lucro e o retorno vendendo tudo no fim."""
        out = {k: m[k] for k in ("profit", "profitPct", "irr", "maxDrawdown", "contributed")}
        if tax_on:
            out.update(profit=m["profitNet"], irr=m["irrNet"], profitPct=m["profitNet"] / m["contributed"] * 100,
                       taxPaid=m["taxPaid"], taxDue=m["taxDue"])
        return out

    rows, labels = [], {key: label for key, label, _f in runs}
    for k, cal in enumerate(periods):
        row = {"from": cal[0], "to": cal[-1], "duration": fmt_sessions(len(cal) - 1),
               "label": _period_label(cal, k == 0, window)}
        flows = None
        for key, _label, f in runs:
            params["skipped"] = 0
            curve, flows, trades = simulate(data, cal, params, f, cdi)
            tax = (params["taxPaid"], params["taxDue"]) if tax_on else None
            row[key] = keep(_metrics(curve, flows, cal[-1], tax=tax)) | {"trades": len(trades), "avgCash": _avg_cash(curve)}
        for key, label, curve in _comparisons(cal, flows, params, loaded["prices"], cdi):
            row[key] = keep(_metrics(curve, flows, cal[-1], tax=_comparison_tax(params, curve)))
            labels[key] = label
        rows.append(row)

    # Régua principal: os índices na proporção das metas (com metas por classe), senão o BOVA11.
    ref = "mix" if all("mix" in r for r in rows) else "bova"
    for row in rows:
        rule = row["rule"]
        row["beats"] = {key: rule["profit"] > row[key]["profit"] for key in ROBUST_NAMES if row.get(key)}
        # Diferença do retorno ao ano para a régua (pontos percentuais) e do lucro para só a regra B (R$).
        row["excess"] = rule["irr"] - row[ref]["irr"] if rule["irr"] is not None and row[ref]["irr"] is not None else None
        row["diffOnlyB"] = rule["profit"] - row["onlyB"]["profit"] if row.get("onlyB") else None

    n = len(rows)
    unit = "janelas" if window else "inícios"
    the_unit, all_unit = ("das janelas", "todas as janelas testadas") if window else ("dos inícios", "todos os inícios testados")
    wins = {key: sum(1 for r in rows if r["beats"].get(key)) for key in ROBUST_NAMES}
    compared = [key for key in ROBUST_ORDER if all(key in r["beats"] for r in rows)]
    irrs = [r["rule"]["irr"] for r in rows if r["rule"]["irr"] is not None]
    excess = [r for r in rows if r["excess"] is not None]
    worst_dd = min(rows, key=lambda r: r["rule"]["maxDrawdown"])
    worst_ex = min(excess, key=lambda r: r["excess"]) if excess else None
    ref_name = ROBUST_REF[ref]
    cards = {
        "irr": {"median": _median(irrs), "min": min(irrs) if irrs else None, "max": max(irrs) if irrs else None},
        "excess": {"label": ROBUST_REF_SHORT[ref], "median": _median(r["excess"] for r in excess),
                   "worst": worst_ex and worst_ex["excess"], "worstLabel": worst_ex and worst_ex["label"],
                   "positive": sum(1 for r in excess if r["excess"] > 0)},
        "drawdown": {"worst": worst_dd["rule"]["maxDrawdown"], "worstLabel": worst_dd["label"],
                     "median": _median(r["rule"]["maxDrawdown"] for r in rows)},
        "profitPct": {"median": _median(r["rule"]["profitPct"] for r in rows)},
        "wins": [{"key": key, "label": ROBUST_NAMES[key], "wins": wins[key]} for key in compared],
    }
    lines = [f"Ganhou {ROBUST_NAMES[key]} em {wins[key]} de {n} {unit}." for key in compared]
    if irrs:
        lines.append(f"Retorno ao ano: mediana de {fmt_num(cards['irr']['median'], 1)}%, "
                     f"pior {fmt_num(cards['irr']['min'], 1)}% e melhor {fmt_num(cards['irr']['max'], 1)}%.")
    if worst_ex:
        lines.append(f"Contra {ref_name}: mediana de {fmt_num(cards['excess']['median'], 1)} p.p. ao ano a mais; "
                     f"o pior período ({worst_ex['label']}) ficou {fmt_num(worst_ex['excess'], 1)} p.p.")
    lines.append(f"Maior queda: {fmt_num(worst_dd['rule']['maxDrawdown'], 1)}% ({worst_dd['label']}).")
    if tax_on:
        lines.append("Com imposto de renda: o lucro e o retorno de cada linha são os líquidos, vendendo tudo no último dia "
                     "(os índices e o CDI pagam 15% sobre o ganho só nessa venda).")
    if wins[ref] == n:
        verdict = f"Consistente: ganhou {ROBUST_NAMES[ref]} em {all_unit}."
    elif wins[ref] * 3 >= n * 2:
        verdict = f"Razoavelmente consistente: ganhou {ROBUST_NAMES[ref]} na maioria {the_unit}, mas não em todos os períodos."
    else:
        verdict = f"Pouco consistente contra {ref_name}: o resultado depende muito do período; não confie no período inteiro sozinho."
    columns = [{"key": key, "label": labels[key]} for key in ("rule", "onlyB", "bova", "ivvb", "mix", "cdi")
               if all(key in r for r in rows) and all(r[key] for r in rows)]
    return {
        "rule": rule_label, "hasPartial": has_partial, "hasCdi": bool(cdi), "end": end, "window": window, "tax": bool(tax_on),
        "ref": {"key": ref, "label": labels[ref], "short": ROBUST_REF_SHORT[ref]}, "columns": columns,
        "params": _params_out(params), "universe": {"label": UNIVERSES[params["universe"]], "candidates": len(loaded["assets"]),
                                                    "ranked": len(data), "illiquid": loaded["illiquid"]},
        "rows": rows, "wins": wins, "periods": n, "cards": cards, "summary": lines, "verdict": verdict, "warnings": warnings,
    }
