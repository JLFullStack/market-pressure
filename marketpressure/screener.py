"""Screener "Garrafa de Gás": varredura, pressão, backtest, ranking e KPI do BOVA11.

A varredura roda no servidor, numa thread em segundo plano (scan_status inicia e informa o
progresso); o resultado fica em memória e no banco (tabelas scan, scan_analyses e scan_failures em data/)
por CONFIG["cacheTtlMinutes"].
O front-end só escolhe qual das visões prontas mostrar (base × candle de rejeição).
"""

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import database as db
from . import market_data
from .core import (SETUP, add_up, avg, clamp01, closed_candles, compute_indicators, day_of,
                  evaluate_setup, exit_signal, fmt_num, session_in_progress)

CONFIG = {
    "universeSize": 150,      # ações mais líquidas analisadas
    "batchSize": 8,           # requisições simultâneas ao Yahoo
    "cacheTtlMinutes": 60,
    "topN": 20,
    **SETUP,                  # parâmetros do setup (core.py): maxDistance, maxBelowDistance, minPullback, mm200SlopeLookback
    # Backtest da estratégia: compra no pullback (entrada na abertura seguinte) e venda só
    # quando o fechamento fica abaixo de MM20, MM50 e MM200 com MM50 < MM200 (ver exit_signal).
    "backtestStart": 220,     # primeiro candle com MM200 e inclinação disponíveis
    "priorWinRate": 0.5,      # base neutra para suavizar taxas de acerto com poucas amostras
    "priorWeight": 4,
    "bovaLookback": 252,      # pregões da variação de 12 meses do BOVA11
    "minCandles": 261,
}

log = logging.getLogger(__name__)

SCAN_VERSION = 1  # mude se o formato das análises mudar (descarta a varredura salva no banco)
MODES = ("fechamento", "intradiario")

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

def pressure(ind, i, setup, live=False):
    """Pressão da "garrafa de gás": gás comprador acumulado no pullback desde o topo (topIdx).
    live: pregão em andamento — o volume parcial do dia é ignorado (volume usa e = i − 1)."""
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

    total = 0.25 * volume + 0.30 * momentum + 0.25 * trend + 0.20 * structure
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


def backtest(ind, strict):
    """Repete a estratégia no histórico do ativo: compra na abertura seguinte ao sinal do setup e
    mantém a posição até o fechamento disparar exit_signal; a venda é na abertura seguinte.
    Acerto = venda acima da compra. Operação ainda aberta no fim do histórico não conta."""
    wins = trades = 0
    returns = []
    candles = ind["candles"]
    last = len(candles) - 1
    i = CONFIG["backtestStart"]
    while i < last:
        setup = evaluate_setup(ind, i)
        if not (setup["okStrict"] if strict else setup["ok"]):
            i += 1
            continue
        entry = candles[i + 1]["o"]
        j = i + 1
        while j < last and not exit_signal(ind, j):
            j += 1
        if j >= last:
            break  # sem saída (ou saída sem candle seguinte): operação em aberto
        ret = candles[j + 1]["o"] / entry - 1
        trades += 1
        returns.append(ret)
        if ret > 0:
            wins += 1
        i = j + 1  # sem sobreposição: volta a procurar sinais depois do candle de saída
    smoothed = (wins + CONFIG["priorWinRate"] * CONFIG["priorWeight"]) / (trades + CONFIG["priorWeight"]) * 100
    return {"wins": wins, "trades": trades, "winRate": smoothed,
            "avgReturn": add_up(returns) / trades * 100 if trades else None}


def analyze_asset(candles, confirm_previous=False):
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
        "pressure": pressure(ind, i, setup, confirm_previous),
        "backtest": {"loose": backtest(ind, False), "strict": backtest(ind, True)},
    }


def analyze_ticker(asset, now=None):
    """Análise de um ativo nas duas bases. Na base "fechamento", descarta o candle do pregão
    em andamento; "intradiario" o usa (e exige o último pregão fechado acima da média)."""
    candles = market_data.history(asset["ticker"])["candles"]
    if len(candles) < CONFIG["minCandles"]:
        raise ValueError("histórico insuficiente")
    live = session_in_progress(candles, now)
    intradiario = analyze_asset(candles, live)
    fechamento = analyze_asset(candles[:-1]) if live else intradiario
    return {**asset, "fechamento": fechamento, "intradiario": intradiario}


# ---------------------------------------------------------------- Varredura

def load_universe():
    stocks = sorted(market_data.universe(), key=lambda s: s["close"] * s["volume"], reverse=True)
    return [{"ticker": s["ticker"], "name": s["name"], "sector": s["sector"]}
            for s in stocks[:CONFIG["universeSize"]]]


def run_scan(progress=lambda done, total: None):
    universe = load_universe()
    analyses, failures = [], []
    done = 0
    progress(0, len(universe))
    with ThreadPoolExecutor(max_workers=CONFIG["batchSize"]) as pool:
        futures = [(asset, pool.submit(analyze_ticker, asset)) for asset in universe]
        for asset, future in futures:
            try:
                analyses.append(future.result())
            except Exception as err:
                failures.append({"ticker": asset["ticker"], "reason": str(err)})
            done += 1
            progress(done, len(universe))
    if not analyses:
        raise RuntimeError(failures[0]["reason"] if failures else "nenhum ativo retornou dados")
    return {"version": SCAN_VERSION, "timestamp": int(time.time() * 1000), "universeSize": len(universe),
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
    if d["dryRatio"] is not None:
        vol.append(f"Volume {'secou' if d['dryRatio'] <= 1 else 'cresceu'} no pullback ({fmt_num(d['dryRatio'], 1)}x do avanço)")
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


def views(scan):
    """Tudo o que a página mostra, nas quatro combinações de base × candle de rejeição."""
    result = {}
    for mode in MODES:
        analyses = analyses_for(scan, mode)
        scanned = len(analyses)
        breadth = sum(1 for a in analyses if a["aboveRisingMM200"]) / scanned * 100
        view = {"refDate": day_of(max(a["date"] for a in analyses)), "scanned": scanned,
                "breadth": breadth, "regime": regime(breadth)}
        for key, strict in (("loose", False), ("strict", True)):
            ranking = build_ranking(analyses, strict)
            top = [{k: a[k] for k in ("ticker", "name", "sector", "price", "change", "setup", "bt", "score")}
                   | {"pressure": a["pressure"]["total"], "bottle": bottle_state(a["pressure"]["total"]),
                      "justification": justification(a)} for a in ranking[:CONFIG["topN"]]]
            view[key] = {
                "qualified": len(ranking),
                "top": top,
                "avgPressure": avg(a["pressure"] for a in top) if top else None,
                "avgWinRate": avg(a["bt"]["winRate"] for a in top) if top else None,
            }
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


def write_scan(scan):
    """Grava a varredura nas tabelas scan (1 linha), scan_failures e scan_analyses (ativo × base)."""
    analyses = []
    for a in scan["analyses"]:
        asset = {k: a[k] for k in ("ticker", "name", "sector")}
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
        a = analyses.setdefault(r["ticker"], {k: r[k] for k in ("ticker", "name", "sector")})
        a[r["mode"]] = db.unflatten_analysis(r)
    return {"version": meta[0]["version"], "timestamp": meta[0]["timestamp"], "universeSize": meta[0]["universe_size"],
            "analyses": list(analyses.values()), "failures": db.read_table("scan_failures")}


class ScanJob:
    """Uma varredura por vez, em segundo plano. status() devolve o resultado pronto (se ainda
    válido) ou inicia/acompanha a varredura."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.done = self.total = 0
        self.error = None
        self.scan = self._read_file()
        self.payload = scan_payload(self.scan) if self.scan else None

    @staticmethod
    def _read_file():
        try:
            return read_scan()
        except Exception:
            log.warning("não foi possível ler a varredura salva no banco", exc_info=True)
            return None

    def _fresh(self):
        return self.scan and time.time() * 1000 - self.scan["timestamp"] < CONFIG["cacheTtlMinutes"] * 60000

    def _progress(self, done, total):
        self.done, self.total = done, total

    def _work(self):
        try:
            scan = run_scan(self._progress)
            payload = scan_payload(scan)
            try:
                write_scan(scan)
            except Exception:
                log.warning("não foi possível salvar a varredura no banco", exc_info=True)
            with self.lock:
                self.scan, self.payload, self.error = scan, payload, None
        except Exception as err:
            log.warning("varredura falhou: %s", err)
            with self.lock:
                self.error = str(err)
        finally:
            with self.lock:
                self.running = False

    def status(self, force=False):
        with self.lock:
            if not self.running and (force or not self._fresh()) and not (self.error and not force):
                self.running, self.done, self.total, self.error = True, 0, 0, None
                threading.Thread(target=self._work, daemon=True).start()
            if self.running:
                return {"state": "running", "done": self.done, "total": self.total}
            if self.error:
                # Falhou: mostra a última varredura salva, se houver. A próxima chamada tenta de novo.
                error, self.error = self.error, None
                return {"state": "error", "message": error, "scan": self.payload}
            return {"state": "done", "scan": self.payload}


scan_job = ScanJob()


# ---------------------------------------------------------------- KPI do BOVA11

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
