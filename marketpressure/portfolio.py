"""Cálculos da aba Carteira: posições, proventos, câmbio, rentabilidade (TWR) contra CDI e
BOVA11, alocação, metas por ativo, a calculadora de aporte (método ARCA) e as validações.

Só funções puras (sem rede nem disco): recebem a carteira e os históricos já carregados.
Quem busca os dados e monta o que a página recebe é o portfolio_service.
"""

import math
import re
import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .core import (EXIT, add_up, closed_candles, compute_indicators, day_of, evaluate_setup, exit_signal,
                  fmt_date, fmt_num, fmt_qty, iso_day_in, js_round, today_iso)

# Classes sugeridas para uma carteira nova (método ARCA). O usuário cria, renomeia e remove.
DEFAULT_CLASSES = [
    {"name": "Ações", "target": 35},
    {"name": "FIIs", "target": 20},
    {"name": "Renda Fixa", "target": 10},
    {"name": "Ativos Internacionais", "target": 35},
]
# Ordem das classes fixas do formato antigo (portfolio.targets), usada na migração.
LEGACY_CLASSES = ["Ações", "ETFs", "FIIs", "BDRs", "Renda fixa"]
# Mercados de um ativo (assets[TICKER].market; ausente = "b3"). Fora da B3 o preço é em US$.
MARKETS = {
    "b3": {"label": "B3", "currency": "R$", "decimals": 0},
    "us": {"label": "EUA", "currency": "US$", "decimals": 4},
    "crypto": {"label": "Cripto", "currency": "US$", "decimals": 4},
}
FX = "USDBRL"  # câmbio do dia (Yahoo USDBRL=X), chave própria em histories
BENCHMARK = "BOVA11"
PERIODS = [("6M", 6), ("1A", 12), ("2A", 24), ("Tudo", None)]
FI_LABELS = {"aporte": "Aporte", "resgate": "Resgate", "valor_atual": "Valor atual"}
# Tipos de provento da lista da B3; "JRS CAP PROPRIO" (JCP) tem 15% de IR retido na fonte.
PROVENTO_TYPES = {"DIVIDENDO": "Dividendo", "JRS CAP PROPRIO": "JCP", "RENDIMENTO": "Rendimento"}
JCP_TAX = 0.15
APORTE_MODES = ("todos", "escolher", "pullback")
EPS = 1e-9


def targets_off(target_sum):
    """As metas das classes não somam 100% (tolerância de 0,01 p.p.)."""
    return abs(target_sum - 100) > 0.01

# ================================================================ Funções puras


def market_of(portfolio, ticker):
    return ((portfolio.get("assets") or {}).get(ticker) or {}).get("market") or "b3"


def is_iso_date(s):
    if not isinstance(s, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def add_months(iso, months):
    """Como setUTCMonth do JavaScript: um dia que não existe no mês de destino transborda para
    o mês seguinte (31/03 − 1 mês = 03/03)."""
    d = date.fromisoformat(iso)
    index = d.year * 12 + d.month - 1 + months
    return (date(index // 12, index % 12 + 1, 1) + timedelta(days=d.day - 1)).isoformat()


def lower_bound(days, iso):
    """Primeiro índice de uma lista ordenada de datas ISO com valor >= iso."""
    lo, hi = 0, len(days)
    while lo < hi:
        mid = (lo + hi) >> 1
        if days[mid] < iso:
            lo = mid + 1
        else:
            hi = mid
    return lo


def to_fixed(x, digits):
    """Number.prototype.toFixed: arredonda o valor binário exato, meio para cima."""
    return float(Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def parse_number(value, empty=None):
    """Número digitado num campo (aceita vírgula decimal). Vazio = empty; inválido = None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value if value is not None else "").strip().replace(",", ".")
    if text == "":
        return empty
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def normalize_ticker(t):
    t = re.sub(r"\.SA$", "", str(t or "").upper().strip())
    return re.sub(r"[^A-Z0-9]", "", t)


def find_class(classes, pattern):
    """Primeira classe cujo nome casa com a expressão (ou None)."""
    return next((c["name"] for c in classes if re.search(pattern, c["name"], re.I)), None)


def suggest_class(ticker, market, classes):
    """Classe sugerida para um ticker novo: EUA/cripto vão para a classe internacional; na B3,
    final 32/33/34/35/39 = BDR, 11 = FII (pode ser ETF); o resto, ações."""
    first = classes[0]["name"] if classes else None
    intl = find_class(classes, r"internac|exterior|global")
    acoes = find_class(classes, r"^a[cç][oõ]es$")
    if market and market != "b3":
        return intl or first
    if re.search(r"(32|33|34|35|39)$", ticker):
        return find_class(classes, r"^bdr") or intl or acoes or first
    if ticker.endswith("11"):
        return find_class(classes, r"^fii") or first
    return acoes or first


def fixed_income_class(classes):
    """Classe padrão dos títulos de renda fixa: a que se chama "Renda fixa" (qualquer caixa)."""
    return find_class(classes, r"^renda fixa$") or "Renda Fixa"


def _number(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0
    return v if math.isfinite(v) else 0


def normalize_portfolio(p):
    """Migração transparente para o formato com classes configuráveis. Carteiras antigas têm
    targets: { Ações, ETFs, FIIs, BDRs, Renda fixa }; viram classes com as mesmas metas, sem
    perder nada. Toda classe citada por um ativo ou título passa a existir (meta 0 se nova).
    Idempotente: uma carteira já normalizada volta igual."""
    if isinstance(p.get("classes"), list):
        classes = [{"name": str(c.get("name")), "target": _number(c.get("target"))} for c in p["classes"]]
    elif isinstance(p.get("targets"), dict):
        t = p["targets"]
        names = [n for n in LEGACY_CLASSES if n in t] + [n for n in t if n not in LEGACY_CLASSES]
        classes = [{"name": n, "target": _number(t[n])} for n in names]
    else:
        classes = [dict(c) for c in DEFAULT_CLASSES]
    fi_class = fixed_income_class(classes)
    fixed_income = [{**f, "class": f.get("class") or fi_class, "entries": f.get("entries") or []}
                    for f in p.get("fixedIncome") or []]
    assets = dict(p.get("assets") or {})
    used = [a.get("class") for a in assets.values()] + [f["class"] for f in fixed_income]
    for name in used:
        if name and not any(c["name"] == name for c in classes):
            classes.append({"name": name, "target": 0})
    rest = {k: v for k, v in p.items() if k != "targets"}
    return {**rest, "classes": classes, "assets": assets, "transactions": p.get("transactions") or [],
            "fixedIncome": fixed_income,
            "goals": [{**g, "created": g.get("created") or today_iso()} for g in p.get("goals") or []]}


def normalize_doc(doc):
    return {**doc, "version": max(2, doc.get("version") or 1),
            "portfolios": [normalize_portfolio(p) for p in doc.get("portfolios") or []]}


# Ordem cronológica estável. No mesmo dia, entradas vêm antes de saídas (compra antes de
# venda; aporte e resgate antes de "valor atual", que já inclui o movimento do dia).
SAME_DAY_ORDER = {"compra": 0, "venda": 1, "aporte": 0, "resgate": 0, "valor_atual": 1}


def by_date(items):
    return sorted(items, key=lambda x: (x["date"], SAME_DAY_ORDER.get(x.get("type"), 0)))


def signed_qty(tx):
    return tx["quantity"] if tx["type"] == "compra" else -tx["quantity"]


def tx_flow(tx):
    """Fluxo de caixa de uma operação em reais: compra = custo (positivo), venda = valor líquido
    recebido (negativo). Preço e taxas estão na moeda do ativo; fx é o câmbio da operação
    (R$ por US$; 1 ou ausente na B3)."""
    gross = tx["quantity"] * tx["price"]
    fees = tx.get("fees") or 0
    native = gross + fees if tx["type"] == "compra" else -(gross - fees)
    return native * (tx.get("fx") or 1)


def build_positions(transactions):
    """Posição por ativo (valores em reais). Compra soma quantidade e custo; venda reduz ao preço
    médio e registra lucro realizado = valor líquido − preço médio × qtd."""
    positions = {}
    for tx in by_date(transactions):
        p = positions.setdefault(tx["ticker"], {"ticker": tx["ticker"], "quantity": 0, "cost": 0,
                                                "bought": 0, "soldProceeds": 0, "realized": 0})
        if tx["type"] == "compra":
            cost = tx_flow(tx)
            p["quantity"] += tx["quantity"]
            p["cost"] += cost
            p["bought"] += cost
        else:
            avg_price = p["cost"] / p["quantity"] if p["quantity"] else 0
            proceeds = -tx_flow(tx)
            p["realized"] += proceeds - avg_price * tx["quantity"]
            p["soldProceeds"] += proceeds
            p["quantity"] -= tx["quantity"]
            p["cost"] -= avg_price * tx["quantity"]
            if p["quantity"] < EPS:
                p["quantity"] = p["cost"] = 0
    for p in positions.values():
        p["avgPrice"] = p["cost"] / p["quantity"] if p["quantity"] else 0
    return positions


def check_sequence(transactions):
    """Nenhuma venda pode exceder a quantidade possuída na data dela. Retorna o erro ou None."""
    qty = {}
    for tx in by_date(transactions):
        qty[tx["ticker"]] = qty.get(tx["ticker"], 0) + signed_qty(tx)
        if qty[tx["ticker"]] < -EPS:
            held = qty[tx["ticker"]] + tx["quantity"]
            return (f"A venda de {fmt_qty(tx['quantity'])} {tx['ticker']} em {fmt_date(tx['date'])} "
                    f"excede a quantidade possuída nessa data ({fmt_qty(held)}).")
    return None


def quantity_at(transactions, ticker, iso):
    """Quantidade possuída ao fim do dia iso (operações com data <= iso)."""
    q = 0
    for tx in transactions:
        if tx["ticker"] == ticker and tx["date"] <= iso:
            q += signed_qty(tx)
    return q


def close_on_or_before(candles, iso):
    """Fechamento (sem ajuste) do dia iso ou do pregão anterior mais próximo."""
    days = [day_of(c["date"]) for c in candles]
    k = lower_bound(days, iso)
    idx = k if k < len(days) and days[k] == iso else k - 1
    return {"price": candles[idx]["c"], "date": days[idx]} if idx >= 0 else None


def closed_for(candles, market, now=None):
    """Candles só de pregões fechados, por mercado. Cripto negocia todos os dias e o candle do
    dia (UTC) só fecha à meia-noite UTC; os demais seguem a regra da B3 (closed_candles)."""
    if market != "crypto":
        return closed_candles(candles, now)
    now = time.time() if now is None else now
    if candles and day_of(candles[-1]["date"]) >= iso_day_in(now, "UTC"):
        return candles[:-1]
    return candles


def convert_history(hist, fx_candles):
    """Converte um histórico em US$ para reais pelo câmbio USDBRL de cada dia (último disponível
    até a data). Proventos: valor por cota × câmbio da data-ex. Sem câmbio, devolve None."""
    if not fx_candles:
        return None
    fx_days = [day_of(c["date"]) for c in fx_candles]

    def rate_at(iso):
        k = lower_bound(fx_days, iso)
        idx = k if k < len(fx_days) and fx_days[k] == iso else k - 1
        return fx_candles[max(0, idx)]["c"]

    candles = []
    for c in hist["candles"]:
        r = rate_at(day_of(c["date"]))
        candles.append({**c, "o": c["o"] * r, "h": c["h"] * r, "l": c["l"] * r, "c": c["c"] * r,
                        "adj": c["adj"] * r, "fx": r})
    dividends = [{**d, "amount": d["amount"] * rate_at(day_of(d["date"])), "amountNative": d["amount"]}
                 for d in hist["dividends"]]
    return {"candles": candles, "dividends": dividends}


def dividends_received(transactions, ticker, hist):
    """Proventos recebidos de um ticker. A data do Yahoo é a data-ex; tem direito quem possuía o
    ativo ao fim do pregão anterior a ela (a data-com), ou seja, operações com data < data-ex."""
    txs = [t for t in transactions if t["ticker"] == ticker]
    if not txs or not hist:
        return []
    days = [day_of(c["date"]) for c in hist["candles"]]
    out = []
    for ev in hist["dividends"]:
        ex_date = day_of(ev["date"])
        k = lower_bound(days, ex_date)
        com_date = days[k - 1] if k > 0 else ex_date
        quantity = 0
        for tx in txs:
            if tx["date"] < ex_date:
                quantity += signed_qty(tx)
        if quantity > EPS:
            out.append({"ticker": ticker, "exDate": ex_date, "comDate": com_date, "amount": ev["amount"],
                        "quantity": quantity, "total": ev["amount"] * quantity})
    return out


def dividends_to_receive(transactions, ticker, events, today=None):
    """Proventos a receber de um ticker da B3 (events de /api/proventos: rate por cota, comDate,
    paymentDate nulo = a definir). Entram os ainda não pagos (pagamento hoje ou depois, ou a
    definir). Data-com já passada: quantidade possuída nela (confirmado). Data-com hoje ou
    futura: quantidade de hoje (previsto — só vale se o ativo for mantido até a data-com)."""
    today = today or today_iso()
    out = []
    for ev in events or []:
        if ev.get("paymentDate") and ev["paymentDate"] < today:
            continue
        confirmed = ev["comDate"] < today
        quantity = quantity_at(transactions, ticker, ev["comDate"] if confirmed else today)
        if quantity <= EPS:
            continue
        kind = ev.get("type") or ""
        total = ev["rate"] * quantity
        out.append({
            "ticker": ticker, "type": PROVENTO_TYPES.get(kind) or kind[:1] + kind[1:].lower(),
            "relatedTo": ev.get("relatedTo") or "", "comDate": ev["comDate"],
            "paymentDate": ev.get("paymentDate") or None, "amount": ev["rate"], "quantity": quantity,
            "total": total, "net": total * (1 - JCP_TAX) if kind == "JRS CAP PROPRIO" else total,
            "confirmed": confirmed,
        })
    return out


def open_lots(transactions, ticker, candles, target=EXIT["partialTarget"], fraction=EXIT["partialFraction"]):
    """Compras (lotes) ainda abertas de um ticker e quanto falta vender de cada uma pela saída
    parcial. Um lote atinge o alvo no primeiro pregão depois da compra cuja máxima chega a
    +target sobre o preço dela (candles e preço na moeda do ativo). Cada venda abate primeiro a
    parcial pendente dos lotes que já tinham atingido o alvo na data da venda (até `fraction` do
    lote); o que sobra sai das compras mais antigas. pending = quantidade a vender agora."""
    days = [day_of(c["date"]) for c in candles]
    lots = []
    for tx in by_date(t for t in transactions if t["ticker"] == ticker):
        if tx["type"] == "compra":
            goal = tx["price"] * (1 + target)
            k = lower_bound(days, tx["date"])
            k += k < len(days) and days[k] == tx["date"]  # a partir do pregão seguinte ao da compra
            hit = next((days[j] for j in range(k, len(days)) if candles[j]["h"] >= goal), None)
            lots.append({"date": tx["date"], "price": tx["price"], "quantity": tx["quantity"],
                         "remaining": tx["quantity"], "realized": 0.0, "hitDate": hit})
            continue
        left = tx["quantity"]
        for lot in lots:
            if left > EPS and lot["hitDate"] and lot["hitDate"] <= tx["date"]:
                take = min(left, lot["remaining"], max(0.0, lot["quantity"] * fraction - lot["realized"]))
                lot["realized"] += take
                lot["remaining"] -= take
                left -= take
        for lot in lots:
            take = min(left, lot["remaining"])
            lot["remaining"] -= take
            left -= take
    out = []
    for lot in lots:
        if lot["remaining"] <= EPS:
            continue
        due = lot["quantity"] * fraction - lot["realized"] if lot["hitDate"] else 0.0
        out.append({**lot, "pending": min(lot["remaining"], max(0.0, due))})
    return out


def partial_to_sell(lots, market):
    """Quantidade a vender pela saída parcial (lotes que atingiram o alvo e ainda não venderam a
    parte deles); inteira na B3. None quando não há."""
    pending = add_up(lot["pending"] for lot in lots if lot["pending"] > EPS)
    if market == "b3":
        pending = math.floor(pending + EPS)
    if pending <= EPS:
        return None
    return {"quantity": pending, "lots": sum(1 for lot in lots if lot["pending"] > EPS),
            "target": EXIT["partialTarget"] * 100, "fraction": EXIT["partialFraction"] * 100}


PARTIAL_STATUS = f"Realizar {fmt_num(EXIT['partialFraction'] * 100, 0)}%"


def position_status(ind, i, partial=None):
    """Situação da posição pelas regras de saída: "Vender" (exit_signal); "Realizar 50%" quando
    alguma compra atingiu o alvo da saída parcial e a parte dela ainda não foi vendida
    (partial_to_sell); "Atenção" se o fechamento perdeu as três médias (sem MM50 < MM200 ainda)
    ou só a MM200; senão "Manter"."""
    c = ind["closes"][i]
    if exit_signal(ind, i):
        return "Vender"
    if partial:
        return PARTIAL_STATUS
    # Abaixo das três médias implica abaixo da MM200: as duas condições se resumem a c < MM200.
    if c < ind["mm200"][i]:
        return "Atenção"
    return "Manter"


def setup_now(candles, market, mode, now=None, memo=None):
    """O ativo está no setup de pullback agora? Mesma regra e mesma base do screener: na base
    "fechamento" descarta o candle do pregão em andamento; no "intradiario" usa o candle do dia
    e exige que o último pregão fechado também esteja acima da média (confirm_previous).
    candles na moeda do ativo (o setup é lido no gráfico dele, não convertido).
    memo: cache opcional de indicadores por quantidade de candles."""
    closed = closed_for(candles, market, now)
    live = len(closed) < len(candles)
    use = candles if mode == "intradiario" else closed
    if len(use) < 220:
        return {"enough": False, "ok": False, "ma": None}
    ind = indicators_memo(use, memo)
    s = evaluate_setup(ind, len(use) - 1, mode == "intradiario" and live)
    return {"enough": True, "ok": bool(s["ok"]), "ma": s["ma"], "close": use[-1]["c"]}


def indicators_memo(candles, memo):
    if memo is None:
        return compute_indicators(candles)
    if len(candles) not in memo:
        memo[len(candles)] = compute_indicators(candles)
    return memo[len(candles)]


def fixed_income_value_at(entries, iso):
    """Valor de um título de renda fixa ao fim do dia iso: último "valor atual" informado até a
    data, mais aportes e menos resgates posteriores a ele; antes do primeiro, o líquido aportado."""
    value = 0
    for e in entries:
        if e["date"] > iso:
            break
        if e["type"] == "valor_atual":
            value = e["amount"]
        else:
            value += e["amount"] if e["type"] == "aporte" else -e["amount"]
    return value


def fixed_income_summary(item, iso):
    entries = by_date(item.get("entries") or [])

    def total(kind):
        return add_up(e["amount"] for e in entries if e["type"] == kind)

    contributed, redeemed = total("aporte"), total("resgate")
    value = fixed_income_value_at(entries, iso)
    updates = [e for e in entries if e["type"] == "valor_atual"]
    profit = value + redeemed - contributed
    return {
        "id": item["id"], "name": item["name"], "cls": item.get("class"), "fixedTarget": item.get("target"),
        "entries": entries, "contributed": contributed, "redeemed": redeemed,
        "invested": contributed - redeemed, "value": value, "profit": profit,
        "returnPct": profit / contributed * 100 if contributed else None,
        "lastUpdate": updates[-1]["date"] if updates else None,
    }


def align_to_calendar(candles, cal, field):
    """Valores de um campo dos candles alinhados ao calendário (último conhecido até cada dia)."""
    out = [None] * len(cal)
    j, last = 0, None
    days = [day_of(c["date"]) for c in candles]
    for k, day in enumerate(cal):
        while j < len(candles) and days[j] <= day:
            last = candles[j][field]
            j += 1
        out[k] = last
    return out


def build_series(portfolio, histories, cdi_rates, dividend_events):
    """Série diária desde a primeira operação. Calendário: pregões do BOVA11 unidos aos dos ativos
    da B3 na carteira (o Yahoo às vezes omite um candle do BOVA11; nesses dias ele repete o
    anterior). Ativos dos EUA e cripto entram pelo último preço em reais até cada pregão.
    histories: { TICKER: { candles, dividends } } já em reais (inclui o BOVA11); cdi_rates: [{date, rate}].
    Retorna as datas e, por dia: patrimônio (value), proventos (div), aportes líquidos (flow),
    retorno diário (daily) e o acumulado (%) da carteira, do CDI e do BOVA11."""
    bench = histories.get(BENCHMARK)
    fixed_items = [{**f, "entries": by_date(f.get("entries") or [])} for f in portfolio.get("fixedIncome") or []]
    fi_entries = [e for f in fixed_items for e in f["entries"]]
    txs = by_date(portfolio.get("transactions") or [])
    dates = sorted(e["date"] for e in txs + fi_entries)
    if not bench or not bench["candles"] or not dates:
        return None

    tickers = list(dict.fromkeys(t["ticker"] for t in txs))
    bench_days = [day_of(c["date"]) for c in bench["candles"]]
    days = set(bench_days)
    for t in tickers:
        if market_of(portfolio, t) != "b3":
            continue  # cripto e EUA não criam pregões
        days.update(day_of(c["date"]) for c in (histories.get(t) or {}).get("candles") or [])
    cal = sorted(d for d in days if d >= bench_days[0])

    # Dia-base (acumulado = 0%): o pregão da primeira operação; CDI e BOVA11 partem do
    # fechamento dele. Operações anteriores ao histórico disponível entram no primeiro pregão,
    # já valorizadas, sem contar como aporte (a série começa ali).
    first_date = dates[0]
    base = lower_bound(cal, first_date)
    if base >= len(cal):
        return None
    truncated = first_date < cal[0]
    n = len(cal)

    closes = {t: align_to_calendar((histories.get(t) or {}).get("candles") or [], cal, "c") for t in tickers}
    bench_adj = align_to_calendar(bench["candles"], cal, "adj")

    # Eventos (operações, aportes, proventos) entram no primeiro pregão >= a data deles.
    flow, div = [0.0] * n, [0.0] * n
    for tx in txs:
        k = lower_bound(cal, tx["date"])
        if k < n:
            flow[k] += tx_flow(tx)
    for e in fi_entries:
        if e["type"] == "valor_atual":
            continue
        k = lower_bound(cal, e["date"])
        if k < n:
            flow[k] += e["amount"] if e["type"] == "aporte" else -e["amount"]
    for d in dividend_events:
        k = lower_bound(cal, d["exDate"])
        if k < n:
            div[k] += d["total"]

    value = [0.0] * n
    qty, last_price = {}, {}
    p = 0
    for k in range(base, n):
        while p < len(txs) and txs[p]["date"] <= cal[k]:
            tx = txs[p]
            p += 1
            qty[tx["ticker"]] = qty.get(tx["ticker"], 0) + signed_qty(tx)
            last_price[tx["ticker"]] = tx["price"] * (tx.get("fx") or 1)  # só antes da 1ª cotação
        v = 0.0
        for t in tickers:
            if qty.get(t):
                close = closes[t][k]
                v += qty[t] * (close if close is not None else last_price[t])
        for f in fixed_items:
            v += fixed_income_value_at(f["entries"], cal[k])
        value[k] = v

    # TWR: r_t = (V_t + D_t − F_t) / V_{t−1} − 1. Sem patrimônio na véspera (primeiro dia ou
    # recomeço após zerar), mede do valor aportado até o fechamento: (V_t + D_t) / F_t − 1.
    rates = sorted(cdi_rates, key=lambda r: r["date"])
    ri = lower_bound([r["date"] for r in rates], cal[base])
    out = {"dates": [], "value": [], "div": [], "flow": [], "daily": [], "portfolio": [], "cdi": [], "bova": []}
    acc_p = acc_c = 1.0
    for k in range(base, n):
        prev = value[k - 1] if k > base else 0
        counted = not (truncated and k == base)  # no início truncado, o 1º dia é só a base
        r = 0
        if counted and prev > EPS:
            r = (value[k] + div[k] - flow[k]) / prev - 1
        elif counted and flow[k] > EPS:
            r = (value[k] + div[k]) / flow[k] - 1
        # A taxa do CDI de um dia rende até o pregão seguinte.
        if k > base:
            while ri < len(rates) and rates[ri]["date"] < cal[k]:
                acc_c *= 1 + rates[ri]["rate"] / 100
                ri += 1
        acc_p *= 1 + r
        out["dates"].append(cal[k])
        out["value"].append(value[k])
        out["div"].append(div[k] if counted else 0)
        out["flow"].append(flow[k] if counted else 0)
        out["daily"].append(r)
        out["portfolio"].append((acc_p - 1) * 100)
        out["cdi"].append((acc_c - 1) * 100)
        out["bova"].append((bench_adj[k] / bench_adj[base] - 1) * 100 if bench_adj[k] and bench_adj[base] else 0)
    out["truncated"] = truncated
    out["firstDate"] = first_date
    return out


def rebase_series(values, start):
    """Série acumulada (%) reiniciada em 0% no índice start. Em start = 0 nada muda: o acumulado
    do primeiro dia (do preço pago até o fechamento) faz parte da rentabilidade desde o início."""
    base = 1 + values[start] / 100 if start > 0 else 1
    return [((1 + v / 100) / base - 1) * 100 for v in values[start:]]


def window_returns(series, start):
    """Retorno (%) das três séries do índice start até o fim."""
    def last(key):
        return rebase_series(series[key], start)[-1]
    return {"from": series["dates"][start], "portfolio": last("portfolio"), "cdi": last("cdi"), "bova": last("bova")}


def period_start(series, months):
    """Índice do primeiro pregão do período (meses para trás a partir do último dia); None = tudo."""
    if not months:
        return 0
    dates = series["dates"]
    return min(lower_bound(dates, add_months(dates[-1], -months)), len(dates) - 1)


def class_allocation(classes, holdings, total):
    """Valor e % de cada classe (na ordem das classes da carteira) e a diferença para a meta.
    holdings: [{ cls, value }] (posições abertas e títulos de renda fixa)."""
    out = []
    for c in classes:
        value = add_up(h["value"] for h in holdings if h["cls"] == c["name"])
        pct = value / total * 100 if total else 0
        target = _number(c.get("target"))
        out.append({"cls": c["name"], "value": value, "pct": pct, "target": target, "diff": pct - target,
                    "toTarget": target / 100 * total - value})
    return out


def asset_targets(classes, items):
    """Meta de cada ativo (% do patrimônio). Por padrão a meta da classe é dividida igualmente
    entre os ativos da classe com posição; um ativo com meta fixada (fixedTarget) fica com ela
    e os demais dividem o restante. items: [{ key, cls, fixedTarget|None }].
    Retorna (targets: { key: { target, auto } }, warnings: [texto])."""
    targets, warnings = {}, []
    for c in classes:
        members = [i for i in items if i["cls"] == c["name"]]
        fixed = [i for i in members if i.get("fixedTarget") is not None]
        free = [i for i in members if i.get("fixedTarget") is None]
        fixed_sum = add_up(float(i["fixedTarget"]) for i in fixed)
        rest = max(0, c["target"] - fixed_sum)
        for i in fixed:
            targets[i["key"]] = {"target": float(i["fixedTarget"]), "auto": False}
        for i in free:
            targets[i["key"]] = {"target": rest / len(free), "auto": True}
        if fixed_sum > c["target"] + EPS:
            warnings.append(f"As metas fixadas em {c['name']} somam {fmt_num(fixed_sum, 2)}%, acima da meta da classe "
                            f"({fmt_num(c['target'], 2)}%)"
                            + ("; os demais ativos da classe ficam com meta 0%" if free else "") + ".")
        elif members and not free and fixed_sum < c["target"] - EPS:
            warnings.append(f"As metas fixadas em {c['name']} somam {fmt_num(fixed_sum, 2)}%, abaixo da meta da classe "
                            f"({fmt_num(c['target'], 2)}%).")
    return targets, warnings


def split_cents(amount, weights):
    """Divide amount (R$) proporcionalmente aos pesos, em centavos, pelo método do maior resto:
    a soma das partes é exatamente amount. Pesos todos zero = partes iguais."""
    cents = js_round(amount * 100)
    if not weights:
        return []
    w = [max(0, x) for x in weights]
    total = add_up(w)
    if total <= 0:
        w, total = [1] * len(weights), len(weights)
    raw = [cents * x / total for x in w]
    parts = [math.floor(x) for x in raw]
    left = cents - sum(parts)
    order = sorted(range(len(raw)), key=lambda k: -(raw[k] - parts[k]))
    for n in range(left):
        parts[order[n % len(order)]] += 1
    return [c / 100 for c in parts]


def distribute(amount, rows):
    """Sugestão de aporte (só compra) entre as linhas elegíveis: proporcional ao que falta para
    cada uma chegar ao valor ideal; se todas já estão no ideal ou acima, proporcional às metas."""
    gaps = [max(0, r["diff"]) for r in rows]
    by_gap = add_up(gaps) > 0.005
    parts = split_cents(amount, gaps if by_gap else [r["target"] for r in rows])
    return {"basis": "falta" if by_gap else "meta", "parts": {r["key"]: parts[k] for k, r in enumerate(rows)}}


def contribution_plan(classes, items, total, amount, mode, selected=()):
    """Calculadora de aporte (método ARCA). Entrada:
      classes: [{ name, target }]; total: patrimônio atual (R$); amount: valor do aporte (R$);
      mode: 'todos' | 'escolher' | 'pullback'; selected: chaves escolhidas (modo 'escolher');
      items: [{ key, label, cls, value, target (% do patrimônio), kind: 'quote'|'fixed',
               market, priceBRL, sell (exit_signal), setup: { ok, ma, enough } | None }].
    Valor ideal = (patrimônio + aporte) × meta; diferença = ideal − saldo (pode ser negativa).
    Elegíveis: sem sinal de venda e, conforme o modo, escolhidos ou em pullback (renda fixa
    segue elegível pela meta). A parte que caberia a quem ficou de fora (comparado ao modo
    "todos") vai para os elegíveis e é informada em redirected."""
    estimated = total + amount
    chosen = set(selected)
    rows = []
    for i in items:
        ideal = estimated * i["target"] / 100
        setup = i.get("setup") or {}
        if i["sell"]:
            status = {"key": "sell", "label": "Vender"}
        elif i["kind"] == "fixed":
            status = {"key": "fixed", "label": "Renda fixa"}
        elif setup.get("ok"):
            status = {"key": "setup", "label": f"Em pullback ({setup['ma']['name']})"}
        else:
            status = {"key": "wait", "label": "Aguardar pullback"}
        excluded = None
        if status["key"] == "sell":
            excluded = "Vender: fechamento abaixo da MM20, MM50 e MM200 com MM50 < MM200 — não recebe aporte"
        elif mode == "escolher" and i["key"] not in chosen:
            excluded = "Não selecionado"
        elif mode == "pullback" and status["key"] == "wait":
            excluded = ("Aguardar pullback (histórico insuficiente para a MM200)" if setup.get("enough") is False
                        else "Aguardar pullback")
        rows.append({**i, "pct": i["value"] / total * 100 if total else 0, "ideal": ideal, "diff": ideal - i["value"],
                     "status": status, "excluded": excluded, "eligible": not excluded})

    eligible = [r for r in rows if r["eligible"]]
    reference = distribute(amount, [r for r in rows if r["status"]["key"] != "sell"])["parts"]  # modo "todos"
    waiting = amount > 0 and not eligible
    plan = {"basis": None, "parts": {}} if waiting else distribute(amount, eligible)
    for r in rows:
        r["suggestion"] = plan["parts"].get(r["key"], 0) if r["eligible"] else 0
        r["wouldReceive"] = reference.get(r["key"], 0) if r["excluded"] and r["status"]["key"] != "sell" else 0
        r["shares"], r["effective"], r["leftover"] = None, r["suggestion"], 0
        if r["kind"] == "quote" and r["priceBRL"] > 0 and r["suggestion"] > 0:
            scale = 10 ** MARKETS.get(r["market"], MARKETS["b3"])["decimals"]
            r["shares"] = math.floor(r["suggestion"] / r["priceBRL"] * scale + EPS) / scale
            r["effective"] = js_round(r["shares"] * r["priceBRL"] * 100) / 100
            r["leftover"] = js_round((r["suggestion"] - r["effective"]) * 100) / 100
        elif r["kind"] == "quote":
            r["shares"] = None if r["suggestion"] > 0 else 0

    class_rows = []
    for c in classes:
        members = [r for r in rows if r["cls"] == c["name"]]
        value = add_up(r["value"] for r in members)
        suggestion = add_up(r["suggestion"] for r in members)
        target = _number(c.get("target"))
        ideal = estimated * target / 100
        class_rows.append({
            "cls": c["name"], "members": sorted(members, key=lambda r: -r["value"]), "value": value,
            "pct": value / total * 100 if total else 0, "target": target, "ideal": ideal, "diff": ideal - value,
            "suggestion": suggestion, "effective": add_up(r["effective"] for r in members),
            "leftover": add_up(r["leftover"] for r in members),
            "pctAfter": (value + suggestion) / estimated * 100 if estimated else 0,
        })

    # Redirecionado: o que iria para ativos fora do modo escolhido (sem contar "Vender").
    out = [r for r in rows if r["excluded"] and r["status"]["key"] != "sell" and r["wouldReceive"] > 0]
    by_class = {}
    for r in out:
        by_class[r["cls"]] = by_class.get(r["cls"], 0) + r["wouldReceive"]
    return {
        "mode": mode, "total": total, "amount": amount, "estimated": estimated, "rows": rows,
        "classRows": class_rows, "waiting": waiting, "basis": plan["basis"],
        "suggested": add_up(r["suggestion"] for r in rows), "effective": add_up(r["effective"] for r in rows),
        "leftover": add_up(r["leftover"] for r in rows),
        "redirected": None if waiting else {"total": add_up(r["wouldReceive"] for r in out),
                                            "byClass": [{"cls": k, "value": v} for k, v in by_class.items()]},
        "targetSum": add_up(_number(c.get("target")) for c in classes),
    }


def contribution_items(portfolio, analysis, mode):
    """Linhas da calculadora a partir da análise da carteira: ativos com posição (quantidade > 0)
    e títulos com valor > 0, cada um com a meta calculada por asset_targets."""
    assets = portfolio.get("assets") or {}
    items = []
    for p in analysis["open"]:
        setup = p["setup"]["intradiario" if mode == "intradiario" else "fechamento"] if p["setup"] else \
            {"enough": False, "ok": False, "ma": None}
        items.append({"key": p["ticker"], "label": p["ticker"], "cls": p["cls"], "value": p["value"], "kind": "quote",
                      "market": p["market"], "priceBRL": p["price"] if p["hasQuote"] else 0,
                      "sell": p["status"] == "Vender", "setup": setup,
                      "fixedTarget": (assets.get(p["ticker"]) or {}).get("target")})
    for f in analysis["fixed"]:
        if f["value"] > 0:
            items.append({"key": f"fi:{f['id']}", "label": f["name"], "cls": f["cls"], "value": f["value"],
                          "kind": "fixed", "market": "b3", "priceBRL": 0, "sell": False, "setup": None,
                          "fixedTarget": f["fixedTarget"]})
    targets, warnings = asset_targets(portfolio["classes"], items)
    return [{**i, "target": targets[i["key"]]["target"], "autoTarget": targets[i["key"]]["auto"]} for i in items], warnings


def analyze_portfolio(portfolio, histories, cdi_rates, as_of=None, now=None):
    """Consolida a carteira: posições, renda fixa, proventos, alocação, KPIs e série de rentabilidade.
    histories: { TICKER: histórico na moeda do ativo, USDBRL: câmbio, BOVA11 }. Ativos fora da
    B3 são convertidos para reais (convert_history); o setup e a situação usam o histórico original."""
    as_of = as_of or today_iso(now)
    txs = portfolio.get("transactions") or []
    positions_map = build_positions(txs)
    brl = {BENCHMARK: histories.get(BENCHMARK)}
    for t in positions_map:
        if not histories.get(t):
            continue
        if market_of(portfolio, t) == "b3":
            h = histories[t]
        else:
            h = convert_history(histories[t], (histories.get(FX) or {}).get("candles"))
        if h:
            brl[t] = h
    dividends = [d for t in positions_map for d in dividends_received(txs, t, brl.get(t))]
    dividends.sort(key=lambda d: (d["exDate"], d["ticker"]), reverse=True)
    div_by_ticker = {}
    for d in dividends:
        div_by_ticker[d["ticker"]] = div_by_ticker.get(d["ticker"], 0) + d["total"]

    positions = []
    for p in positions_map.values():
        market = market_of(portfolio, p["ticker"])
        candles = (brl.get(p["ticker"]) or {}).get("candles") or []
        raw = (histories.get(p["ticker"]) or {}).get("candles") or []
        last_tx = by_date([t for t in txs if t["ticker"] == p["ticker"]])[-1]
        n = len(candles)
        price = candles[-1]["c"] if n else last_tx["price"] * (last_tx.get("fx") or 1)
        day_change = (candles[-1]["c"] / candles[-2]["c"] - 1) * 100 if n > 1 else None
        value = p["quantity"] * price
        dividends_total = div_by_ticker.get(p["ticker"], 0)
        profit = value + p["soldProceeds"] + dividends_total - p["bought"]
        status = setup = partial = None
        if n:
            memo = {}
            closed = closed_for(raw, market, now)
            if p["quantity"] > 0:
                partial = partial_to_sell(open_lots(txs, p["ticker"], raw), market)
            if len(closed) >= 200:
                status = position_status(indicators_memo(closed, memo), len(closed) - 1, partial)
            setup = {mode: setup_now(raw, market, mode, now, memo) for mode in ("fechamento", "intradiario")}
        positions.append({
            **p, "market": market, "currency": MARKETS[market]["currency"],
            "cls": (portfolio.get("assets", {}).get(p["ticker"]) or {}).get("class")
                   or suggest_class(p["ticker"], market, portfolio["classes"]),
            "price": price, "priceNative": raw[-1]["c"] if n else last_tx["price"],
            "fx": (candles[-1].get("fx") or 1) if n else (last_tx.get("fx") or 1),
            "priceDate": day_of(candles[-1]["date"]) if n else last_tx["date"], "hasQuote": n > 0,
            "dayChange": day_change,
            "dayValue": 0 if day_change is None else p["quantity"] * (candles[-1]["c"] - candles[-2]["c"]),
            "value": value, "dividends": dividends_total, "profit": profit,
            "returnPct": profit / p["bought"] * 100 if p["bought"] else None, "status": status, "setup": setup,
            "partial": partial if status == PARTIAL_STATUS else None,
        })
    open_ = sorted((p for p in positions if p["quantity"] > 0), key=lambda p: -p["value"])
    fixed = [s for s in (fixed_income_summary(f, as_of) for f in portfolio.get("fixedIncome") or []) if s["entries"]]

    total = add_up(p["value"] for p in open_) + add_up(f["value"] for f in fixed)
    invested = add_up(p["bought"] - p["soldProceeds"] for p in positions) + add_up(f["invested"] for f in fixed)
    dividends_sum = add_up(d["total"] for d in dividends)
    realized = add_up(p["realized"] for p in positions)
    unrealized = add_up(p["value"] - p["cost"] for p in open_) + add_up(f["value"] - f["invested"] for f in fixed)
    profit = total - invested + dividends_sum  # = valorização + proventos + lucro realizado
    for h in open_ + fixed:
        h["weight"] = h["value"] / total * 100 if total else 0

    allocation = class_allocation(portfolio.get("classes") or [], open_ + fixed, total)
    series = build_series(portfolio, brl, cdi_rates, dividends)
    day = since_start = last12 = None
    if series and len(series["dates"]) > 1:
        e = len(series["dates"]) - 1
        day = {"date": series["dates"][e],
               "value": series["value"][e] + series["div"][e] - series["flow"][e] - series["value"][e - 1],
               "pct": series["daily"][e] * 100}
        since_start = window_returns(series, 0)
        last12 = window_returns(series, period_start(series, 12))

    return {
        "positions": positions, "open": open_, "fixed": fixed, "dividends": dividends, "divByTicker": div_by_ticker,
        "allocation": allocation, "targetSum": add_up(a["target"] for a in allocation), "series": series,
        "hasForeign": any(p["market"] != "b3" for p in positions),
        "kpis": {"total": total, "invested": invested, "profit": profit,
                 "profitPct": profit / invested * 100 if invested > 0 else None, "dividendsTotal": dividends_sum,
                 "realized": realized, "unrealized": unrealized, "day": day, "sinceStart": since_start,
                 "last12": last12},
    }


def validate_transaction(portfolio, tx, today=None):
    """Validação de uma nova operação. Retorna a mensagem de erro ou None."""
    today = today or today_iso()
    if not tx.get("ticker"):
        return "Informe o ativo."
    if not is_iso_date(tx.get("date")):
        return "Informe uma data válida."
    if tx["date"] > today:
        return "A data não pode ser futura."
    if not (tx.get("quantity") is not None and tx["quantity"] > 0):
        return "A quantidade deve ser maior que zero."
    if not (tx.get("price") is not None and tx["price"] > 0):
        return "O preço deve ser maior que zero."
    if not (tx.get("fees") is not None and tx["fees"] >= 0):
        return "As taxas não podem ser negativas."
    if not (tx.get("fx") is not None and tx["fx"] > 0):
        return "Informe o câmbio (R$ por US$) da operação."
    return check_sequence([*(portfolio.get("transactions") or []), tx])


def validate_fixed_income_entry(entry, today=None):
    """Validação de um lançamento de renda fixa ({ date, amount }). Retorna o erro ou None."""
    today = today or today_iso()
    if not is_iso_date(entry.get("date")):
        return "Informe uma data válida."
    if entry["date"] > today:
        return "A data não pode ser futura."
    if not (entry.get("amount") is not None and entry["amount"] > 0):
        return "O valor deve ser maior que zero."
    return None


def batch_rows(plan, positions, day):
    """Linhas da lista "Registrar compras sugeridas", a partir do plano de aporte: ativos cotados
    elegíveis com cotas > 0 (compra pelo preço atual na moeda do ativo e câmbio do dia) e títulos
    de renda fixa com sugestão > 0 (aporte = sugestão). positions: analysis["open"]."""
    by_ticker = {p["ticker"]: p for p in positions}
    rows = []
    for r in plan["rows"]:
        if not (r["eligible"] and r["suggestion"] > 0 and (r["kind"] == "fixed" or (r["shares"] or 0) > 0)):
            continue
        if r["kind"] == "fixed":
            rows.append({"kind": "fixed", "key": r["key"], "id": r["key"][3:], "label": r["label"], "include": True,
                         "date": day, "amount": r["suggestion"]})
            continue
        p = by_ticker[r["key"]]
        foreign = p["market"] != "b3"
        rows.append({"kind": "quote", "key": r["key"], "label": r["label"], "market": p["market"], "cls": p["cls"],
                     "include": True, "date": day, "qty": r["shares"],
                     "price": to_fixed(p["priceNative"], 4 if foreign else 2),
                     "fx": to_fixed(p["fx"], 4) if foreign else 1, "fees": 0})
    return rows


def parse_batch_row(r):
    """Linha da lista como chega do formulário (números podem vir como texto digitado)."""
    if r.get("kind") == "fixed":
        return {**r, "amount": parse_number(r.get("amount"))}
    return {**r, "qty": parse_number(r.get("qty")), "price": parse_number(r.get("price")),
            "fees": parse_number(r.get("fees"), 0), "fx": 1 if r.get("market") == "b3" else parse_number(r.get("fx"))}


def batch_tx(r, tx_id=None):
    """Operação de compra de uma linha da lista (mesmo formato do formulário "Nova operação")."""
    return {"id": tx_id, "date": r.get("date"), "ticker": r.get("key"), "type": "compra", "quantity": r["qty"],
            "price": r["price"], "fees": r["fees"], "fx": 1 if r.get("market") == "b3" else r["fx"]}


def batch_row_total(r):
    """Total em reais de uma linha (custo da compra com taxas, ou valor do aporte); None se incompleta."""
    if r["kind"] == "fixed":
        return r["amount"]
    if None in (r["qty"], r["price"], r["fees"], r["fx"]):
        return None
    return tx_flow(batch_tx(r))


def batch_row_error(portfolio, r, today=None):
    """Erro de uma linha da lista pelas mesmas regras dos formulários avulsos, ou None."""
    if r["kind"] == "fixed":
        return validate_fixed_income_entry({"date": r.get("date"), "amount": r["amount"]}, today)
    return validate_transaction(portfolio, batch_tx(r), today)


def batch_totals(rows, amount):
    """Totais da lista em conferência (atualizados enquanto o usuário digita)."""
    parsed = [parse_batch_row(r) for r in rows]
    totals = [batch_row_total(r) for r in parsed]
    total, complete = 0.0, True
    for r, v in zip(parsed, totals):
        if not r.get("include"):
            continue
        if v is None:
            complete = False
        else:
            total += v
    return {"rows": totals, "total": total, "complete": complete,
            "included": sum(1 for r in parsed if r.get("include")), "diff": amount - total}


def validate_class_name(classes, name, previous=None):
    """Validação do nome de uma classe (nova ou renomeada). Retorna a mensagem de erro ou None."""
    if not name:
        return "Informe o nome da classe."
    if len(name) > 40:
        return "Use um nome com até 40 caracteres."
    if any(c["name"] != previous and c["name"].lower() == name.lower() for c in classes):
        return f'Já existe a classe "{name}".'
    return None


def class_members(portfolio, name):
    """Ativos e títulos que impedem a remoção de uma classe (é preciso movê-los antes)."""
    return ([t for t, a in (portfolio.get("assets") or {}).items() if a.get("class") == name]
            + [f["name"] for f in portfolio.get("fixedIncome") or [] if f.get("class") == name])


def net_flows(portfolio):
    """Aportes líquidos em reais por lançamento: compras e aportes de renda fixa positivos, vendas
    e resgates negativos ("valor atual" não é fluxo). [(data, valor)]."""
    flows = [(tx["date"], tx_flow(tx)) for tx in portfolio.get("transactions") or []]
    for f in portfolio.get("fixedIncome") or []:
        for e in f.get("entries") or []:
            if e["type"] in ("aporte", "resgate"):
                flows.append((e["date"], e["amount"] if e["type"] == "aporte" else -e["amount"]))
    return flows


def has_entries(portfolio):
    return bool(portfolio.get("transactions")) or any(f.get("entries") for f in portfolio.get("fixedIncome") or [])
