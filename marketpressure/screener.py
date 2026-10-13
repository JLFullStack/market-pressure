"""Screener "Garrafa de Gás": varredura, pressão, backtest, ranking e KPI do BOVA11.

A varredura roda no servidor, numa thread em segundo plano (scan_job.status inicia e informa o
progresso); o resultado fica em memória e no banco (tabelas scan, scan_analyses e scan_failures)
por CONFIG["cacheTtlMinutes"]. No navegador (versão publicada) não há threads: scan_job.start é
trocado por browser.py, que conduz a varredura em etapas (finish/fail no fim).
O front-end só escolhe qual das visões prontas mostrar (base × candle de rejeição).
"""

import bisect
import logging
import math
import threading
import time

from . import database as db
from . import market_data, runtime
from .core import (EXIT, SETUP, add_up, adjusted_candles, avg, clamp01, closed_candles, compute_indicators, day_of,
                  evaluate_setup, exit_signal, fmt_date, fmt_num, in_session_hours, last_close, session_in_progress)

CONFIG = {
    # Universo: ativos da lista do Yahoo (market_data.load_universe) de quatro tipos, cada um com
    # o seu ranking (KINDS), com volume financeiro médio (fechamento × volume) dos últimos pregões
    # fechados acima do piso do tipo. A média evita que um ativo saia por causa de um único dia
    # fraco; a média de 3 meses da lista só descarta antes os que estão muito longe do piso.
    "minAvgVolume": 5_000_000,  # R$ por pregão
    "minAvgVolumeByKind": {"fundos": 3_000_000},  # fundos: book raso abaixo disso, mas R$ 3 mi bastam
    "liquidityWindow": 20,      # pregões da média
    "prefilterRatio": 0.6,      # candidato: média de 3 meses (lista do Yahoo) >= 60% do piso
    # Fundos (FII, Fiagro, FI-Infra) caem todo mês o valor do rendimento na data-ex: com o preço
    # bruto, a MM200 fica inclinada para baixo e a regra de saída dispara à toa (HGLG11: 34
    # vendas em 5 anos contra 15 no ajustado). Eles usam o preço ajustado por proventos.
    "adjustDividends": ("fundos",),
    # Pesos da pressão. BDRs sem o componente Volume: na B3 o volume é do formador de mercado e
    # da arbitragem, não descoberta de preço (redistribuição proporcional dos outros três).
    "pressureWeights": {"default": {"volume": 0.25, "momentum": 0.30, "trend": 0.25, "structure": 0.20},
                        "bdrs": {"volume": 0.0, "momentum": 0.40, "trend": 0.33, "structure": 0.27}},
    # ETFs: os de amplitude diária média (máxima − mínima) abaixo de 0,3% em 250 pregões são de
    # renda fixa (sem pullbacks) e saem; e um só por índice: o que tem retornos diários com
    # correlação >= 0,98 com um ETF mais líquido é marcado como duplicado (duplicateOf).
    "etfMinRange": 0.003,
    "etfRangeWindow": 250,
    "etfDuplicateCorrelation": 0.98,
    "etfCorrelationWindow": 120,
    "regimeMinAssets": 20,      # amplitude/regime do tipo só com pelo menos 20 ativos
    "batchSize": 8,           # requisições simultâneas ao Yahoo
    "cacheTtlMinutes": 60,
    "topN": 20,
    **SETUP,                  # parâmetros do setup (core.py): maxDistance, maxBelowDistance, minPullback, mm200SlopeLookback
    **EXIT,                   # saída parcial de cada lote (core.py): partialTarget, partialFraction
    # Backtest da estratégia: compra no pullback (entrada na abertura seguinte), recompra em
    # pullbacks novos; cada compra vende partialFraction ao atingir +partialTarget e o restante
    # de todas sai quando o fechamento fica abaixo de MM20, MM50 e MM200 com MM50 < MM200
    # (ver exit_signal).
    "backtestStart": 220,     # primeiro candle com MM200 e inclinação disponíveis
    "reentryDistance": 0.05,  # recompra só a 5%+ do preço de cada compra da posição aberta (outro pullback)
    "priorWinRate": 0.5,      # base neutra para suavizar taxas de acerto com poucas amostras
    "priorWeight": 4,
    "bovaLookback": 252,      # pregões da variação de 12 meses do BOVA11
    "minCandles": 261,
}

log = logging.getLogger(__name__)

SCAN_VERSION = 4  # mude se o formato das análises mudar (descarta a varredura salva no banco)
MODES = ("fechamento", "intradiario")
KINDS = ("acoes", "fundos", "etfs", "bdrs")  # tipos de ativo (market_data.asset_kind), um ranking cada


def min_avg_volume(kind):
    return CONFIG["minAvgVolumeByKind"].get(kind, CONFIG["minAvgVolume"])

# ---------------------------------------------------------------- Auxiliares de pressure()


def percentile_in_window(values, i, n):
    """Percentil (0-1) do valor em i dentro da janela [i-n+1, i]."""
    below = count = 0
    for j in range(max(0, i - n + 1), i + 1):
        if values[j] is None:
            continue
        count += 1
        if values[j] <= values[i]:
            below += 1
    return below / count if count else 0.5


def _window(values, a, b):
    return values[max(0, a):b + 1]


def min_in(values, a, b):
    return min(_window(values, a, b), default=math.inf)


def max_in(values, a, b):
    return max(_window(values, a, b), default=-math.inf)


def mean_in(values, a, b):
    return avg(_window(values, a, b))


# ---------------------------------------------------------------- Pressão e backtest

def pressure(ind, i, setup, live=False, weights=None):
    """Pressão da "garrafa de gás": gás comprador acumulado no pullback desde o topo (topIdx).
    live: pregão em andamento — o volume parcial do dia é ignorado (volume usa e = i − 1).
    weights: pesos dos quatro componentes (CONFIG["pressureWeights"]; padrão 25/30/25/20)."""
    t = setup["topIdx"]
    e = i - 1 if live else i
    candles, closes = ind["candles"], ind["closes"]
    vols = [c["v"] for c in candles]
    lows = [c["l"] for c in candles]
    highs = [c["h"] for c in candles]

    # Volume (25%): A = OBV acima do preço (divergência), B = avanço com volume,
    # C = volume secando no pullback.
    obv_pct = percentile_in_window(ind["obv"], e, 60)
    close_pct = percentile_in_window(closes, e, 60)
    A = clamp01(0.5 + (obv_pct - close_pct))
    up_vols = [vols[j] for j in range(t - 19, t + 1) if closes[j] > closes[j - 1]]
    base_vol = mean_in(vols, t - 59, t)
    advance_ratio = avg(up_vols) / base_vol if up_vols and base_vol else None
    B = 0 if advance_ratio is None else clamp01((advance_ratio - 0.9) / 0.4)
    advance_vol = mean_in(vols, t - 19, t)
    dry_ratio = mean_in(vols, t + 1, e) / advance_vol if t + 1 <= e and advance_vol else None
    C = 0.5 if dry_ratio is None else clamp01((1.2 - dry_ratio) / 0.5)
    volume = 100 * (0.4 * A + 0.3 * B + 0.3 * C)

    # Momentum (30%): RSI preservando o regime de alta, reset do RSI desde o topo,
    # Estocástico que "zerou" e divergência oculta de alta.
    r, k = ind["rsi"][i], ind["stoch"][i]
    rsi_min = min_in(ind["rsi"], t, i)
    stoch_min = min_in(ind["stoch"], t, i)
    regime = clamp01((rsi_min - 30) / 10)
    reset = clamp01((ind["rsi"][t] - r) / 15)
    stoch_reset = clamp01((50 - stoch_min) / 30)
    hidden_divergence = min_in(lows, t, i) > min_in(lows, t - 30, t - 1) and rsi_min < min_in(ind["rsi"], t - 30, t - 1)
    momentum = min(100, 100 * (0.4 * regime + 0.3 * reset + 0.3 * stoch_reset) + (15 if hidden_divergence else 0))

    # Tendência (25%): histograma recuperando a força vendedora do pullback, MACD perto
    # ou acima de zero (normalizado pelo ATR) e preço escorado na média.
    atr_now = ind["atr14"][i]
    min_hist = min_in(ind["hist"], t, i)
    hist = 1 if min_hist >= 0 else clamp01((ind["hist"][i] - min_hist) / -min_hist)
    macd_atr = ind["macd"][i] / atr_now if atr_now else 0
    zero = 1 if macd_atr >= 0 else clamp01(1 + macd_atr / 0.5)
    support = clamp01(1 - setup["ma"]["dist"] / CONFIG["maxDistance"]) if setup["ma"] else 0
    trend = 100 * (0.35 * hist + 0.35 * zero + 0.30 * support)

    # Estrutura (20%): squeeze das Bandas de Bollinger + compressão das últimas 5 barras.
    bbw_pct = percentile_in_window(ind["bbw"], i, 120)
    range5_atr = (max_in(highs, i - 4, i) - min_in(lows, i - 4, i)) / atr_now if atr_now else 4
    structure = 100 * (0.6 * (1 - bbw_pct) + 0.4 * clamp01((4 - range5_atr) / 2))

    w = weights or CONFIG["pressureWeights"]["default"]
    total = w["volume"] * volume + w["momentum"] * momentum + w["trend"] * trend + w["structure"] * structure
    return {
        "total": total, "volume": volume, "momentum": momentum, "trend": trend, "structure": structure,
        "details": {
            "A": A, "B": B, "C": C, "obvPct": obv_pct, "closePct": close_pct,
            "advanceRatio": advance_ratio, "dryRatio": dry_ratio,
            "regime": regime, "reset": reset, "stochReset": stoch_reset, "rsiMin": rsi_min,
            "stochMin": stoch_min, "hiddenDivergence": hidden_divergence,
            "hist": hist, "zero": zero, "macdAtr": macd_atr, "support": support, "bbwPct": bbw_pct,
            "range5Atr": range5_atr, "rsi": r, "stoch": k,
        },
    }


def backtest_lots(ind, strict, target=None, fraction=None, start=None):
    """Repete a estratégia no histórico do ativo, lote a lote: compra na abertura seguinte ao sinal
    do setup e recompra a cada pullback novo enquanto a posição está aberta; o mesmo pullback não
    gera nova compra (preço a menos de reentryDistance de alguma compra da posição). Cada compra
    vende `fraction` ao atingir +`target` (máxima de um candle depois do da compra; preenchida no
    alvo, ou na abertura se ela já abriu acima). Quando o fechamento dispara exit_signal, o
    restante de todas as compras é vendido na abertura seguinte.
    Lote: {"entry": {"i", "price"}, "partial": {"i", "price"} | None, "exit": {"i", "price"} | None}
    (i = índice do candle da execução). fraction 0 = só a regra B. start: primeiro candle em que
    um sinal pode comprar (nunca antes de backtestStart, quando a MM200 e a inclinação existem)."""
    target = CONFIG["partialTarget"] if target is None else target
    fraction = CONFIG["partialFraction"] if fraction is None else fraction
    candles = ind["candles"]
    last = len(candles) - 1
    lots, held = [], []
    for i in range(max(CONFIG["backtestStart"], start or 0), last + 1):
        c = candles[i]
        if fraction > 0:
            for lot in held:
                goal = lot["entry"]["price"] * (1 + target)
                if lot["partial"] is None and i > lot["entry"]["i"] and c["h"] >= goal:
                    lot["partial"] = {"i": i, "price": max(goal, c["o"])}
        if i == last:
            break
        if held and exit_signal(ind, i):
            for lot in held:
                lot["exit"] = {"i": i + 1, "price": candles[i + 1]["o"]}
            held = []
            continue
        setup = evaluate_setup(ind, i)
        if not (setup["okStrict"] if strict else setup["ok"]):
            continue
        price = candles[i + 1]["o"]
        if all(abs(price / lot["entry"]["price"] - 1) >= CONFIG["reentryDistance"] for lot in held):
            lot = {"entry": {"i": i + 1, "price": price}, "partial": None, "exit": None}
            held.append(lot)
            lots.append(lot)
    return lots


def lot_return(lot, fraction, mark=None):
    """Retorno do lote (fração): parte vendida no alvo + restante na saída. Lote ainda aberto:
    None, ou, com mark, o restante marcado a esse preço."""
    entry = lot["entry"]["price"]
    end = lot["exit"]["price"] if lot["exit"] else mark
    if end is None:
        return None
    if lot["partial"]:
        return fraction * (lot["partial"]["price"] / entry - 1) + (1 - fraction) * (end / entry - 1)
    return end / entry - 1


def backtest(ind, strict):
    """Resumo de backtest_lots: cada compra encerrada é uma operação; acerto = retorno do lote
    (parcial + saída) positivo. Compras ainda abertas no fim não contam."""
    fraction = CONFIG["partialFraction"]
    returns = [r for r in (lot_return(lot, fraction) for lot in backtest_lots(ind, strict)) if r is not None]
    trades = len(returns)
    wins = sum(1 for r in returns if r > 0)
    smoothed = (wins + CONFIG["priorWinRate"] * CONFIG["priorWeight"]) / (trades + CONFIG["priorWeight"]) * 100
    return {"wins": wins, "trades": trades, "winRate": smoothed,
            "avgReturn": add_up(returns) / trades * 100 if trades else None}


def analyze_asset(candles, confirm_previous=False, kind="acoes"):
    weights = CONFIG["pressureWeights"].get(kind, CONFIG["pressureWeights"]["default"])
    ind = compute_indicators(candles)
    i = len(candles) - 1
    setup = evaluate_setup(ind, i, confirm_previous)
    base = {
        "date": candles[i]["date"],
        "price": candles[i]["c"],
        "change": (candles[i]["c"] / candles[i - 1]["c"] - 1) * 100,
        "aboveRisingMM200": setup["trend"],
        "setup": {k: setup[k] for k in ("ok", "okStrict", "pullback", "ma", "pattern")},
    }
    if not setup["ok"]:
        return base
    return {
        **base,
        "pressure": pressure(ind, i, setup, confirm_previous, weights),
        "backtest": {"loose": backtest(ind, False), "strict": backtest(ind, True)},
    }


def avg_traded_value(candles):
    """Volume financeiro médio (R$) dos últimos liquidityWindow candles."""
    window = candles[-CONFIG["liquidityWindow"]:]
    return add_up(c["c"] * c["v"] for c in window) / len(window) if window else 0.0


def avg_range(candles):
    """Amplitude diária média ((máxima − mínima) / fechamento) dos últimos etfRangeWindow candles."""
    window = candles[-CONFIG["etfRangeWindow"]:]
    return add_up((c["h"] - c["l"]) / c["c"] for c in window) / len(window) if window else 0.0


def analyze_ticker(asset, now=None):
    """Análise de um ativo nas duas bases. Na base "fechamento", descarta o candle do pregão
    em andamento; "intradiario" o usa (e exige o último pregão fechado acima da média).
    None quando o volume médio dos pregões fechados fica abaixo do piso do tipo (fora do
    universo) ou, num ETF, quando a amplitude é de renda fixa. Fundos usam o preço ajustado
    por proventos (adjusted_candles); a liquidez é sempre medida no preço bruto."""
    kind = asset.get("kind") or "acoes"
    candles = market_data.history(asset["ticker"])["candles"]
    if not candles:
        raise ValueError("histórico insuficiente")
    live = session_in_progress(candles, now)
    traded = avg_traded_value(candles[:-1] if live else candles)
    if traded < min_avg_volume(kind):
        return None
    if len(candles) < CONFIG["minCandles"]:
        raise ValueError("histórico insuficiente")
    if kind == "etfs" and avg_range(candles) < CONFIG["etfMinRange"]:
        return None
    if kind in CONFIG["adjustDividends"]:
        candles = adjusted_candles(candles)
    intradiario = analyze_asset(candles, live, kind)
    fechamento = analyze_asset(candles[:-1], False, kind) if live else intradiario
    return {**asset, "kind": kind, "traded": traded, "fechamento": fechamento, "intradiario": intradiario}


def _returns(ticker):
    """{data: retorno diário} dos últimos etfCorrelationWindow pregões (histórico em cache)."""
    candles = market_data.history(ticker)["candles"][-(CONFIG["etfCorrelationWindow"] + 1):]
    return {day_of(b["date"]): b["c"] / a["c"] - 1 for a, b in zip(candles, candles[1:])}


def correlation(x: dict, y: dict):
    days = sorted(set(x) & set(y))
    if len(days) < 20:
        return None
    a, b = [x[d] for d in days], [y[d] for d in days]
    ma, mb = add_up(a) / len(a), add_up(b) / len(b)
    cov = add_up((p - ma) * (q - mb) for p, q in zip(a, b))
    var = math.sqrt(add_up((p - ma) ** 2 for p in a) * add_up((q - mb) ** 2 for q in b))
    return cov / var if var else None


def mark_duplicates(analyses):
    """Um ETF por índice: do mais negociado ao menos, o ETF cujos retornos diários têm
    correlação >= etfDuplicateCorrelation com um já mantido ganha duplicateOf (sai do ranking)."""
    kept = []
    for a in sorted((a for a in analyses if a.get("kind") == "etfs"), key=lambda a: -(a.get("traded") or 0)):
        try:
            returns = _returns(a["ticker"])
        except Exception:
            log.warning("sem retornos para comparar %s com os outros ETFs", a["ticker"], exc_info=True)
            continue
        for ticker, other in kept:
            r = correlation(returns, other)
            if r is not None and r >= CONFIG["etfDuplicateCorrelation"]:
                a["duplicateOf"] = ticker
                break
        else:
            kept.append((a["ticker"], returns))


# ---------------------------------------------------------------- Varredura

def load_universe():
    """Candidatos ao universo: ativos dos tipos de KINDS com volume financeiro médio de 3 meses
    (fechamento × avg_volume da lista; sem ela, o volume do dia) acima de prefilterRatio do piso
    do tipo, do mais negociado ao menos. O piso pela média de liquidityWindow pregões fechados é
    aplicado em analyze_ticker."""
    def traded(s):
        return (s.get("close") or 0) * (s.get("avg_volume") or s.get("volume") or 0)

    assets = [s for s in market_data.universe() if (s.get("kind") or "acoes") in KINDS]
    return [{"ticker": s["ticker"], "name": s["name"], "sector": s["sector"], "kind": s.get("kind") or "acoes"}
            for s in sorted(assets, key=traded, reverse=True)
            if traded(s) >= min_avg_volume(s.get("kind") or "acoes") * CONFIG["prefilterRatio"]]


def run_scan(progress=lambda done, total: None):
    universe = load_universe()
    analyses, failures = [], []
    progress(0, len(universe))
    for done, (asset, analysis, err) in enumerate(runtime.run_all(analyze_ticker, universe, CONFIG["batchSize"]), 1):
        if err is not None:
            failures.append({"ticker": asset["ticker"], "reason": str(err)})
        elif analysis is not None:
            analyses.append(analysis)
        progress(done, len(universe))
    return build_scan(analyses, failures)


def build_scan(analyses, failures):
    """universeSize = ativos acima do piso de liquidez (analisados + falhas; os abaixo do piso
    não contam)."""
    if not analyses:
        raise RuntimeError(failures[0]["reason"] if failures else "nenhum ativo retornou dados")
    mark_duplicates(analyses)
    return {"version": SCAN_VERSION, "timestamp": int(time.time() * 1000), "universeSize": len(analyses) + len(failures),
            "analyses": analyses, "failures": failures}


def build_ranking(analyses, strict):
    ranking = []
    for a in analyses:
        if not (a["setup"]["okStrict"] if strict else a["setup"]["ok"]):
            continue
        bt = a["backtest"]["strict" if strict else "loose"]
        ranking.append({**a, "bt": bt, "score": a["pressure"]["total"] * 0.6 + bt["winRate"] * 0.4})
    ranking.sort(key=lambda a: -a["score"])
    return ranking


def bottle_state(p):
    """Estado da garrafa pela pressão: "cheia" (>= 75), "quase" (>= 60) ou "enchendo"."""
    return "cheia" if p >= 75 else "quase" if p >= 60 else "enchendo"


def regime(breadth):
    """Regime do mercado pela parcela do universo em tendência primária de alta."""
    return "alta" if breadth >= 60 else "misto" if breadth >= 40 else "defensivo"


def justification(a):
    """Texto da coluna "Justificativa Quantitativa" a partir dos detalhes da pressão."""
    d, setup = a["pressure"]["details"], a["setup"]
    pattern = setup["pattern"][0].upper() + setup["pattern"][1:] if setup["pattern"] else "Candle"
    parts = [f"{pattern} na {setup['ma']['name']} (+{fmt_num(setup['ma']['dist'] * 100)}% acima), "
             f"pullback de {fmt_num(setup['pullback'] * 100)}%."]
    vol = []
    if a.get("kind") == "bdrs":
        vol.append("Volume fora da pressão (BDR: volume do formador de mercado)")
    elif d["dryRatio"] is not None:
        vol.append(f"Volume {'secou' if d['dryRatio'] <= 1 else 'cresceu'} no pullback ({fmt_num(d['dryRatio'], 1)}x do avanço)")
    if a.get("kind") != "bdrs":
        vol.append(f"OBV {'acima' if d['obvPct'] >= d['closePct'] else 'abaixo'} do preço")
        if d["advanceRatio"] is not None and d["advanceRatio"] >= 1:
            vol.append(f"avanço com volume ({fmt_num(d['advanceRatio'], 1)}x)")
    parts.append("; ".join(vol) + ".")
    mom = [f"RSI respeitou 40 (mín. {fmt_num(d['rsiMin'], 0)})" if d["rsiMin"] >= 40 else f"RSI caiu a {fmt_num(d['rsiMin'], 0)}",
           f"Estoc. tocou {fmt_num(d['stochMin'], 0)} (agora {fmt_num(d['stoch'], 0)})"]
    if d["hiddenDivergence"]:
        mom.append("divergência oculta de alta")
    parts.append("; ".join(mom) + ".")
    hist = "Histograma sem força vendedora" if d["hist"] >= 1 else f"Histograma recuperou {fmt_num(d['hist'] * 100, 0)}% da força vendedora"
    parts.append(f"{hist}; MACD {'acima' if d['macdAtr'] >= 0 else 'abaixo'} de zero; "
                 f"compressão de {fmt_num(d['range5Atr'], 1)} ATR em 5 pregões.")
    return " ".join(parts)


def analyses_for(scan, mode):
    """Análises de cada ativo na base escolhida (último fechamento ou intradiário)."""
    out = []
    for a in scan["analyses"]:
        asset = {k: v for k, v in a.items() if k not in MODES}
        out.append({**asset, **a[mode]})
    return out


def ranking_view(analyses, strict):
    """Top topN de um tipo (sem os ETFs duplicados), com as médias do top."""
    ranking = build_ranking([a for a in analyses if not a.get("duplicateOf")], strict)
    top = [{k: a[k] for k in ("ticker", "name", "sector", "price", "change", "setup", "bt", "score")}
           | {"pressure": a["pressure"]["total"], "bottle": bottle_state(a["pressure"]["total"]),
              "justification": justification(a)} for a in ranking[:CONFIG["topN"]]]
    return {
        "qualified": len(ranking),
        "top": top,
        "avgPressure": avg(a["pressure"] for a in top) if top else None,
        "avgWinRate": avg(a["bt"]["winRate"] for a in top) if top else None,
    }


def views(scan):
    """Tudo o que a página mostra, nas quatro combinações de base × candle de rejeição: um
    ranking por tipo de ativo (KINDS), cada um com a sua amplitude e regime (só com pelo menos
    regimeMinAssets ativos no tipo; abaixo disso, None = amostra pequena)."""
    result = {}
    for mode in MODES:
        analyses = analyses_for(scan, mode)
        view = {"refDate": day_of(max(a["date"] for a in analyses)), "scanned": len(analyses), "rankings": []}
        for kind in KINDS:
            group = [a for a in analyses if (a.get("kind") or "acoes") == kind]
            breadth = (sum(1 for a in group if a["aboveRisingMM200"]) / len(group) * 100
                       if len(group) >= CONFIG["regimeMinAssets"] else None)
            view["rankings"].append({
                "kind": kind, "scanned": len(group), "minAvgVolume": min_avg_volume(kind),
                "duplicates": sum(1 for a in group if a.get("duplicateOf")),
                "breadth": breadth, "regime": regime(breadth) if breadth is not None else None,
                "loose": ranking_view(group, False), "strict": ranking_view(group, True),
            })
        result[mode] = view
    return result


def scan_payload(scan):
    failures = scan["failures"]
    return {
        "timestamp": scan["timestamp"],
        "universeSize": scan["universeSize"],
        "topN": CONFIG["topN"],
        "failures": len(failures),
        "apiErrors": sum(1 for f in failures if f["reason"] != "histórico insuficiente"),
        "liveSession": any(a["fechamento"]["date"] != a["intradiario"]["date"] for a in scan["analyses"]),
        "views": views(scan),
    }


ASSET_COLUMNS = (("ticker", "ticker"), ("name", "name"), ("sector", "sector"), ("kind", "kind"),
                 ("duplicate_of", "duplicateOf"))


def write_scan(scan):
    """Grava a varredura nas tabelas scan (1 linha), scan_failures e scan_analyses (ativo × base)."""
    analyses = []
    for a in scan["analyses"]:
        asset = {col: a.get(key) for col, key in ASSET_COLUMNS}
        analyses += [{**asset, "mode": mode, **db.flatten_analysis(a[mode])} for mode in MODES]
    db.write_tables({
        "scan": [{"version": scan["version"], "timestamp": scan["timestamp"], "universe_size": scan["universeSize"]}],
        "scan_failures": scan["failures"],
        "scan_analyses": analyses,
    })


def read_scan():
    """Última varredura salva no banco, ou None (sem varredura ou de outra SCAN_VERSION)."""
    meta = db.read_table("scan")
    if not meta or meta[0]["version"] != SCAN_VERSION:
        return None
    analyses = {}
    for r in db.read_table("scan_analyses"):
        a = analyses.get(r["ticker"])
        if a is None:
            a = analyses[r["ticker"]] = {key: r.get(col) for col, key in ASSET_COLUMNS}
            if a["duplicateOf"] is None:
                del a["duplicateOf"]
        a[r["mode"]] = db.unflatten_analysis(r)
    return {"version": meta[0]["version"], "timestamp": meta[0]["timestamp"], "universeSize": meta[0]["universe_size"],
            "analyses": list(analyses.values()), "failures": db.read_table("scan_failures")}


class ScanJob:
    """Uma varredura por vez, em segundo plano. status() devolve o resultado pronto (se ainda
    válido) ou inicia/acompanha a varredura. start() dispara o trabalho (uma thread com
    run_scan; no navegador, browser.py), que termina com finish(scan) ou fail(err)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.done = self.total = 0
        self.error = None
        self.scan = self._read_file()
        self.payload = scan_payload(self.scan) if self.scan else None
        self.start = lambda: threading.Thread(target=self._work, daemon=True).start()

    @staticmethod
    def _read_file():
        try:
            return read_scan()
        except Exception:
            log.warning("não foi possível ler a varredura salva no banco", exc_info=True)
            return None

    def _fresh(self, now=None):
        """Válida por cacheTtlMinutes; a feita depois do último fechamento (a da atualização
        diária, update.py) vale até a abertura do próximo pregão, pois nada muda até lá."""
        if not self.scan:
            return False
        now = time.time() if now is None else now
        made = self.scan["timestamp"] / 1000
        return now - made < CONFIG["cacheTtlMinutes"] * 60 or (made >= last_close(now) and not in_session_hours(now))

    def _progress(self, done, total):
        self.done, self.total = done, total

    def _work(self):
        try:
            self.finish(run_scan(self._progress))
        except Exception as err:
            self.fail(err)

    def finish(self, scan):
        """Varredura concluída: guarda em memória e no banco."""
        payload = scan_payload(scan)
        try:
            write_scan(scan)
        except Exception:
            log.warning("não foi possível salvar a varredura no banco", exc_info=True)
        with self.lock:
            self.scan, self.payload, self.error, self.running = scan, payload, None, False

    def fail(self, err):
        log.warning("varredura falhou: %s", err)
        with self.lock:
            self.error, self.running = str(err), False

    def status(self, force=False):
        with self.lock:
            if not self.running and (force or not self._fresh()) and not (self.error and not force):
                self.running, self.done, self.total, self.error = True, 0, 0, None
                self.start()
            if self.running:
                return {"state": "running", "done": self.done, "total": self.total}
            if self.error:
                # Falhou: mostra a última varredura salva, se houver. A próxima chamada tenta de novo.
                error, self.error = self.error, None
                return {"state": "error", "message": error, "scan": self.payload}
            return {"state": "done", "scan": self.payload}


scan_job = ScanJob()


# ---------------------------------------------------------------- KPI do BOVA11

# ---------------------------------------------------------------- Aba Backtest

BACKTEST_MARKETS = {"b3": "R$", "us": "US$"}
KIND_LABEL = {"acoes": "Ação", "fundos": "Fundo listado (FII, Fiagro, FI-Infra)", "etfs": "ETF", "bdrs": "BDR",
              "us": "Ativo dos EUA"}
MARKER_LETTER = {"buy": "C", "partial": "P", "exit": "V"}
MARKER_ORDER = ("buy", "partial", "exit")
LOT_MARGIN = 40  # pregões mostrados antes e depois de um lote ao focá-lo no gráfico


def _lot_numbers(numbers):
    """Rótulo dos lotes de um marcador: "3", "1–4" (seguidos) ou "2, 5"."""
    if len(numbers) == 1:
        return str(numbers[0])
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"{numbers[0]}–{numbers[-1]}"
    return ", ".join(map(str, numbers))


def _pct_label(v):
    """0,1 → "10"; 0,125 → "12,5"."""
    v = round(v * 100, 4)
    return fmt_num(v, 0 if float(v).is_integer() else 1)


def _asset_info(ticker, market):
    """Nome e tipo do ativo pela última lista do screener (tabela universe); sem ela, pelo código."""
    if market != "b3":
        return ticker, "us"
    try:
        row = next((u for u in db.read_table("universe") if u["ticker"] == ticker), None)
    except Exception:
        log.warning("não foi possível ler a lista de ativos salva", exc_info=True)
        row = None
    if row:
        return row.get("name") or ticker, row.get("kind") or "acoes"
    return ticker, market_data.asset_kind(ticker) or "acoes"


def _backtest_summary(lots, fraction, mark):
    """Resumo dos lotes: encerrados (acerto, médio, mediana, melhor, pior) e a soma de todos,
    com os abertos marcados pelo último fechamento (mark)."""
    closed = sorted(r for r in (lot_return(lot, fraction) for lot in lots) if r is not None)
    wins = sum(1 for r in closed if r > 0)
    return {
        "lots": len(lots), "closed": len(closed), "open": len(lots) - len(closed), "wins": wins,
        "winRate": wins / len(closed) * 100 if closed else None,
        "winRateSmoothed": (wins + CONFIG["priorWinRate"] * CONFIG["priorWeight"])
                           / (len(closed) + CONFIG["priorWeight"]) * 100,
        "avgReturn": add_up(closed) / len(closed) * 100 if closed else None,
        "medianReturn": closed[len(closed) // 2] * 100 if closed else None,
        "best": closed[-1] * 100 if closed else None,
        "worst": closed[0] * 100 if closed else None,
        "sumReturn": add_up(lot_return(lot, fraction, mark) for lot in lots) * 100,
    }


def backtest_view(ticker, market="b3", target=None, fraction=None, strict=False, start=None, end=None):
    """Tudo o que a aba Backtest mostra de um ativo: candles e médias (pregões fechados; fundos no
    preço ajustado por proventos, como no screener), cada lote com compra, venda parcial e saída,
    marcadores do gráfico, resultado acumulado e o resumo, também com só a regra B para comparar.
    target e fraction em fração (0,10 = 10%); None = os do CONFIG. start/end (AAAA-MM-DD, opcionais):
    período do backtest. As médias usam o histórico anterior a start; compras só a partir de start
    (ajustado para o primeiro pregão com MM200, se for antes) e o que estiver aberto em end é
    marcado pelo fechamento desse dia. ValueError se o histórico ou o período não bastam."""
    target = CONFIG["partialTarget"] if target is None else target
    fraction = CONFIG["partialFraction"] if fraction is None else fraction
    name, kind = _asset_info(ticker, market)
    candles = closed_candles(market_data.history(ticker, market)["candles"])
    if len(candles) < CONFIG["minCandles"]:
        raise ValueError(f"{ticker}: histórico insuficiente ({len(candles)} pregões; "
                         f"são necessários {CONFIG['minCandles']}).")
    adjusted = kind in CONFIG["adjustDividends"]
    if adjusted:
        candles = adjusted_candles(candles)
    days = [day_of(c["date"]) for c in candles]
    first_day, last_day = days[CONFIG["backtestStart"]], days[-1]
    if end:
        cut = bisect.bisect_right(days, end)
        if cut <= CONFIG["backtestStart"] + 1:
            raise ValueError(f"O fim do período deve ser depois de {fmt_date(first_day)}: as médias precisam de "
                             f"{CONFIG['backtestStart']} pregões de histórico antes da primeira compra.")
        candles, days = candles[:cut], days[:cut]
    ind = compute_indicators(candles)
    last = len(candles) - 1
    closes = ind["closes"]
    first = max(CONFIG["backtestStart"], bisect.bisect_left(days, start) if start else 0)
    if first >= last:
        raise ValueError("O período não tem pregões suficientes; escolha um início anterior ao fim.")
    note = None
    if start and days[first] > start and start < first_day:
        note = (f"Início ajustado para {fmt_date(days[first])}: o histórico do Yahoo começa em {fmt_date(days[0])} "
                f"e as médias precisam de {CONFIG['backtestStart']} pregões antes da primeira compra.")
    lots = backtest_lots(ind, strict, target, fraction, first)

    rows, markers = [], {}
    for n, lot in enumerate(lots, 1):
        e, p, x = lot["entry"], lot["partial"], lot["exit"]
        end = x["i"] if x else last
        peak_close = max(closes[e["i"]:end + 1])
        peak_high = max(c["h"] for c in candles[e["i"]:end + 1])
        if x is None:
            status = "Aberto, parcial realizada" if p else "Aberto"
        else:
            status = "Encerrado"
        rows.append({
            "n": n,
            "entry": {"date": days[e["i"]], "price": e["price"]},
            "partial": p and {"date": days[p["i"]], "price": p["price"], "return": (p["price"] / e["price"] - 1) * 100},
            "exit": x and {"date": days[x["i"]], "price": x["price"], "return": (x["price"] / e["price"] - 1) * 100},
            "open": x is None,
            "return": lot_return(lot, fraction, closes[last]) * 100,
            "days": end - e["i"],
            "maxGain": (peak_high / e["price"] - 1) * 100,
            "giveback": (1 - x["price"] / peak_close) * 100 if x else None,
            "status": status,
            "range": {"from": days[max(first, e["i"] - LOT_MARGIN)], "to": days[min(last, end + LOT_MARGIN)]},
        })
        for key, point in (("buy", e), ("partial", p), ("exit", x)):
            if point:
                markers.setdefault((point["i"], key), {"price": point["price"], "lots": []})["lots"].append(n)
    marker_list = [{"time": days[i], "kind": key, "price": m["price"],
                    "text": f"{MARKER_LETTER[key]}{_lot_numbers(m['lots'])}"}
                   for (i, key), m in sorted(markers.items(), key=lambda kv: (kv[0][0], MARKER_ORDER.index(kv[0][1])))]

    # Resultado acumulado: soma do retorno de cada lote (em % de um lote) dia a dia; a parte ainda
    # aberta de cada lote é marcada pelo fechamento do dia.
    equity = []
    for i in range(first, len(candles)):
        total = 0.0
        for lot in lots:
            e, p, x = lot["entry"], lot["partial"], lot["exit"]
            if e["i"] > i:
                continue
            rest = 1.0
            if p and p["i"] <= i:
                total += fraction * (p["price"] / e["price"] - 1)
                rest -= fraction
            end_price = x["price"] if x and x["i"] <= i else closes[i]
            total += rest * (end_price / e["price"] - 1)
        equity.append({"time": days[i], "value": total * 100})

    closed_rows = [r for r in rows if not r["open"]]
    stats = _backtest_summary(lots, fraction, closes[last])
    stats.update({
        "partialHits": sum(1 for r in rows if r["partial"]),
        "avgDays": add_up(r["days"] for r in closed_rows) / len(closed_rows) if closed_rows else None,
        "avgGiveback": add_up(r["giveback"] for r in closed_rows) / len(closed_rows) if closed_rows else None,
        "avgMaxGain": add_up(r["maxGain"] for r in rows) / len(rows) if rows else None,
        "exits": len({lot["exit"]["i"] for lot in lots if lot["exit"]}),
        "buyHold": (closes[last] / lots[0]["entry"]["price"] - 1) * 100 if lots else None,
        "buyHoldFrom": days[lots[0]["entry"]["i"]] if lots else None,
    })
    only_b = _backtest_summary(backtest_lots(ind, strict, target, 0, first), 0, closes[last]) if fraction > 0 else None

    rule_b = "quando o fechamento fica abaixo de MM20, MM50 e MM200 com MM50 < MM200 (regra B)"
    if fraction > 0:
        rule = f"Vende {_pct_label(fraction)}% de cada compra em +{_pct_label(target)}%; o restante {rule_b}."
    else:
        rule = f"Vende tudo {rule_b}."
    return {
        "ticker": ticker, "market": market, "name": name, "kind": kind, "kindLabel": KIND_LABEL.get(kind, kind),
        "currency": BACKTEST_MARKETS[market], "adjusted": adjusted, "strict": strict,
        "rule": {"target": target * 100, "fraction": fraction * 100, "text": rule},
        "period": f"{fmt_date(days[first])} a {fmt_date(days[last])}", "lastDate": days[last], "lastClose": closes[last],
        "bounds": {"min": first_day, "max": last_day, "from": days[first], "to": days[last]}, "note": note,
        "candles": [{"time": days[i], "open": c["o"], "high": c["h"], "low": c["l"], "close": c["c"]}
                    for i, c in enumerate(candles) if i >= first],
        "ma": {k: [{"time": days[i], "value": v} for i, v in enumerate(ind[k]) if v is not None and i >= first]
               for k in ("mm20", "mm50", "mm200")},
        "markers": marker_list, "equity": equity, "lots": rows, "stats": stats, "onlyB": only_b,
    }


def bova_kpi(mode):
    """Variação do BOVA11 nos últimos bovaLookback pregões e posição do fechamento frente às
    três médias, na base escolhida (na base "fechamento", sem o candle do pregão em andamento)."""
    candles = market_data.history("BOVA11")["candles"]
    if mode == "fechamento":
        candles = closed_candles(candles)
    if len(candles) < 200:
        raise market_data.UpstreamError("histórico insuficiente para a MM200")
    ind = compute_indicators(candles)
    i = len(candles) - 1
    c = ind["closes"][i]
    past = ind["closes"][max(0, i - CONFIG["bovaLookback"])]
    mas = ("MM20", "MM50", "MM200")
    return {
        "close": c,
        "change": (c / past - 1) * 100,
        "above": [n for n in mas if c > ind[n.lower()][i]],
        "below": [n for n in mas if c <= ind[n.lower()][i]],
    }
