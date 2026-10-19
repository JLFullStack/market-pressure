"""Núcleo de cálculo compartilhado pelo Screener (screener.py) e pela Carteira (portfolio.py).

Relógio da B3, indicadores, regra de entrada (evaluate_setup), regra de saída (exit_signal)
e formatadores pt-BR usados nas mensagens. Só biblioteca padrão.

Candles: listas de dicts {"date": unix (s), "o", "h", "l", "c", "v", "adj"}, em ordem de data
(o formato de market_data.history). Os índices seguem os do front-end antigo (core.js).
"""

import math
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

SESSION_END_HOUR = 18  # após as 18h (Brasília) o candle do dia é tratado como fechado
SESSION_START_HOUR = 10  # abertura da B3 (Brasília): antes disso não há candle do dia

# Parâmetros do setup "Garrafa de Gás" (evaluate_setup). O CONFIG do screener os incorpora.
SETUP = {
    "maxDistance": 0.03,       # fechamento até 3% ACIMA da média testada
    "maxBelowDistance": 0.03,  # fechamentos anteriores do pullback podem ficar até 3% ABAIXO da média
    "minPullback": 0.03,       # correção mínima: do topo de 20 pregões até a menor mínima do pullback
    "mm200SlopeLookback": 20,  # MM200 hoje > MM200 de N pregões atrás
}

# ---------------------------------------------------------------- Fusos e relógio da B3
# O Windows não traz a base de fusos (zoneinfo) e o projeto não usa pacotes externos, então os
# três fusos usados são calculados aqui. Brasília é UTC−3 fixo: não tem horário de verão desde 2019,
# e nos anos anteriores do histórico o candle diário da B3 vem às 10h (13h UTC), longe da
# meia-noite, então o dia não muda; Londres segue a regra europeia (último domingo de março e de
# outubro, à 01:00 UTC).

SAO_PAULO = timezone(timedelta(hours=-3))


def _last_sunday(year: int, month: int) -> date:
    last = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _london_offset(moment: datetime) -> timedelta:
    start = datetime.combine(_last_sunday(moment.year, 3), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(moment.year, 10), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    return timedelta(hours=1) if start <= moment < end else timedelta(0)


def local_time(ts: float, zone: str = "America/Sao_Paulo") -> datetime:
    """Instante (unix, s) no fuso: "America/Sao_Paulo", "UTC" ou "Europe/London"."""
    utc = datetime.fromtimestamp(ts, timezone.utc)
    if zone == "UTC":
        return utc
    if zone == "Europe/London":
        return (utc + _london_offset(utc)).replace(tzinfo=None)
    return utc.astimezone(SAO_PAULO)


def iso_day_in(ts: float, zone: str) -> str:
    """Data (AAAA-MM-DD) de um instante num fuso."""
    return local_time(ts, zone).date().isoformat()


def market_clock(ts: float) -> tuple[str, int]:
    """Data (AAAA-MM-DD) e hora no fuso da B3."""
    t = local_time(ts)
    return t.date().isoformat(), t.hour


def day_of(ts: float) -> str:
    return market_clock(ts)[0]


def today_iso(now: float | None = None) -> str:
    return day_of(time.time() if now is None else now)


def session_in_progress(candles: list, now: float | None = None) -> bool:
    """O último candle é de um pregão ainda em andamento?"""
    today, hour = market_clock(time.time() if now is None else now)
    return day_of(candles[-1]["date"]) == today and hour < SESSION_END_HOUR


def closed_candles(candles: list, now: float | None = None) -> list:
    """Candles só de pregões fechados: descarta o candle do pregão em andamento."""
    return candles[:-1] if candles and session_in_progress(candles, now) else candles


def in_session_hours(now: float | None = None) -> bool:
    """Dia útil (seg–sex) entre a abertura e o fechamento da B3, quando existe candle do dia em
    andamento. Os feriados não são conhecidos e contam como dia útil."""
    t = local_time(time.time() if now is None else now)
    return t.weekday() < 5 and SESSION_START_HOUR <= t.hour < SESSION_END_HOUR


def last_close(now: float | None = None) -> float:
    """Instante (unix) do último fechamento da B3 até now: SESSION_END_HOUR do último dia útil
    (seg–sex) que já passou desse horário (feriados contam como dia útil)."""
    t = local_time(time.time() if now is None else now)
    close = t.replace(hour=SESSION_END_HOUR, minute=0, second=0, microsecond=0)
    if close > t:
        close -= timedelta(days=1)
    while close.weekday() >= 5:
        close -= timedelta(days=1)
    return close.timestamp()


def adjusted_candles(candles: list) -> list:
    """Candles ajustados por proventos (o, h, l e c × adj / c; volume igual): a queda da data-ex
    deixa de parecer pullback. O último candle tem fator 1, então o preço atual não muda."""
    out = []
    for c in candles:
        f = c["adj"] / c["c"] if c["c"] else 1.0
        out.append({**c, "o": c["o"] * f, "h": c["h"] * f, "l": c["l"] * f, "c": c["c"] * f})
    return out


# ---------------------------------------------------------------- Indicadores

def sma(values, n):
    out = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values, n):
    out = [None] * len(values)
    k = 2 / (n + 1)
    prev = None
    for i, v in enumerate(values):
        if v is None:
            continue
        prev = v if prev is None else v * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= n:
            gain += g / n
            loss += l / n
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l) / n
        if i >= n:
            out[i] = 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def stochastic(candles, n=14, smooth=3):
    highs, lows = [c["h"] for c in candles], [c["l"] for c in candles]
    raw = [None] * len(candles)
    for i in range(n - 1, len(candles)):
        hi = max(highs[i - n + 1:i + 1])
        lo = min(lows[i - n + 1:i + 1])
        raw[i] = 50 if hi == lo else (candles[i]["c"] - lo) / (hi - lo) * 100
    out = [None] * len(candles)
    for i in range(n - 1 + smooth - 1, len(candles)):
        s = 0.0
        for j in range(i - smooth + 1, i + 1):
            s += raw[j]
        out[i] = s / smooth
    return out


def bollinger_width(closes, n=20, mult=2):
    mid = sma(closes, n)
    out = [None] * len(closes)
    for i, m in enumerate(mid):
        if m is None:
            continue
        v = 0.0
        for x in closes[i - n + 1:i + 1]:
            v += (x - m) ** 2
        out[i] = (2 * mult * math.sqrt(v / n)) / m
    return out


def obv(candles):
    out = [0.0]
    for i in range(1, len(candles)):
        d = candles[i]["c"] - candles[i - 1]["c"]
        direction = (d > 0) - (d < 0)
        out.append(out[i - 1] + direction * candles[i]["v"])
    return out


def atr(candles, n=14):
    """ATR com suavização de Wilder; true range = max(h−l, |h−c anterior|, |l−c anterior|)."""
    out = [None] * len(candles)
    prev = None
    total = 0.0
    for i, c in enumerate(candles):
        pc = candles[i - 1]["c"] if i else c["c"]
        tr = max(c["h"] - c["l"], abs(c["h"] - pc), abs(c["l"] - pc))
        if i < n:
            total += tr
            if i == n - 1:
                prev = out[i] = total / n
        else:
            prev = out[i] = (prev * (n - 1) + tr) / n
    return out


def clamp01(x):
    return min(1, max(0, x))


class Indicators(dict):
    """Indicadores de compute_indicators. Estocástico e largura de Bollinger só entram na
    pressão (screener.pressure), calculada só para quem está em setup: são calculados na
    primeira leitura (LAZY_INDICATORS), o que poupa a maior parte do custo dos demais ativos."""

    def __missing__(self, key):
        if key not in LAZY_INDICATORS:
            raise KeyError(key)
        value = self[key] = LAZY_INDICATORS[key](self)
        return value


LAZY_INDICATORS = {
    "stoch": lambda ind: stochastic(ind["candles"], 14, 3),
    "bbw": lambda ind: bollinger_width(ind["closes"], 20, 2),
}


def compute_indicators(candles):
    closes = [c["c"] for c in candles]
    ema26 = ema(closes, 26)
    macd_line = [v - ema26[i] for i, v in enumerate(ema(closes, 12))]
    signal = ema(macd_line, 9)
    return Indicators({
        "candles": candles,
        "closes": closes,
        "mm20": sma(closes, 20),
        "mm50": sma(closes, 50),
        "mm200": sma(closes, 200),
        "rsi": rsi(closes, 14),
        "macd": macd_line,
        "hist": [v - signal[i] for i, v in enumerate(macd_line)],
        "obv": obv(candles),
        "atr14": atr(candles, 14),
    })


# Saída parcial de cada compra (lote): quando a máxima atinge +partialTarget sobre o preço da
# compra, vende-se partialFraction do lote; o restante sai pela regra B (exit_signal). Os
# backtests da carteira (aportes mensais, prioridade pelo Score) deram a "50% em +10% + B" como
# a saída mais consistente entre as testadas. O CONFIG do screener os incorpora.
EXIT = {
    "partialTarget": 0.10,
    "partialFraction": 0.5,
}


# Recompra só em outro pullback: preço a pelo menos 5% de cada compra ainda aberta (backtest do
# screener, indicação COMPRAR da carteira e AGUARDAR do ranking).
REENTRY_DISTANCE = 0.05

# Filtro de entrada (indicação AGUARDAR do Screener, Situação da Carteira e modo pullback do
# aporte), fora dos internacionais: tendência fraca (MM200 subindo menos de weakSlope em 20
# pregões, retorno de 12 meses negativo ou recuo até a MM200) ou ativo mais fraco que o BOVA11
# em relativeWindow pregões. Nos backtests de 2017–2026 essas compras de ações renderam menos nas
# duas metades do período (ver screener.CONFIG).
ENTRY = {"weakSlope": 0.01, "relativeWindow": 126}


# Força da alta quando uma compra atinge o alvo da venda parcial (só informação, na Situação da
# Carteira e no "Analisar carteira"): forte = fechamento a atrAboveMm20 ATR ou mais acima da MM20
# e RSI ≥ rsi. Nos backtests (ações, 2017–2026), adiar a parcial até o fechamento perder a MM20
# rendeu mais por compra nas duas metades do período, mas menos na carteira: o dinheiro da parcial
# volta ao caixa e compra os próximos pullbacks.
STRENGTH = {"atrAboveMm20": 1.0, "rsi": 65}


def trend_strength(ind, i):
    """{strong, rsi, atrAboveMm20, mm20} no candle i, ou None sem MM20/ATR/RSI."""
    if not all(k in ind for k in ("closes", "mm20", "atr14", "rsi")):
        return None
    c, mm20, atr_now, r = ind["closes"][i], ind["mm20"][i], ind["atr14"][i], ind["rsi"][i]
    if not mm20 or not atr_now or r is None:
        return None
    above = (c - mm20) / atr_now
    return {"strong": above >= STRENGTH["atrAboveMm20"] and r >= STRENGTH["rsi"], "rsi": r,
            "atrAboveMm20": above, "mm20": mm20}


def entry_stats(closes, mm200, i):
    """Força da tendência no candle i: inclinação da MM200 em 20 pregões e retornos de 12 e 6
    meses, em fração (None sem histórico)."""
    w = ENTRY["relativeWindow"]
    return {
        "mm200Slope": mm200[i] / mm200[i - 20] - 1 if i >= 20 and mm200[i] and mm200[i - 20] else None,
        "return12m": closes[i] / closes[i - 252] - 1 if i >= 252 else None,
        "return6m": closes[i] / closes[i - w] - 1 if i >= w else None,
    }


def entry_warnings(stats, ma_name=None, bench6m=None):
    """Motivos para aguardar uma compra no setup (lista vazia = entrada firme). stats: entry_stats;
    ma_name: a média testada; bench6m: retorno de 6 meses do BOVA11 na mesma base."""
    reasons = []
    slope, r12, r6 = stats.get("mm200Slope"), stats.get("return12m"), stats.get("return6m")
    if slope is not None and slope < ENTRY["weakSlope"]:
        reasons.append(f"a MM200 subiu só {fmt_num(slope * 100)}% em 20 pregões (firme: {fmt_pct_value(ENTRY['weakSlope'])}% ou mais)")
    if r12 is not None and r12 < 0:
        reasons.append(f"o preço caiu {fmt_num(-r12 * 100)}% em 12 meses")
    if ma_name == "MM200":
        reasons.append("o recuo chegou até a MM200, o último suporte da tendência")
    if r6 is not None and bench6m is not None and r6 < bench6m:
        reasons.append(f"rendeu {fmt_num(r6 * 100)}% em 6 meses, abaixo dos {fmt_num(bench6m * 100)}% do Ibovespa "
                       "(BOVA11): está mais fraco que o mercado")
    return reasons


def exit_signal(ind, i):
    """Regra de venda (regra "B"): fechamento abaixo de MM20, MM50 e MM200 E tendência de baixa
    formada (MM50 < MM200). Usada no backtest do screener e na situação das posições da carteira."""
    c, mm20, mm50, mm200 = ind["closes"][i], ind["mm20"][i], ind["mm50"][i], ind["mm200"][i]
    if None in (mm20, mm50, mm200):
        return False
    return c < mm20 and c < mm50 and c < mm200 and mm50 < mm200


# ---------------------------------------------------------------- Setup "Garrafa de Gás"

def candle_pattern(c):
    rng = c["h"] - c["l"]
    if rng <= 0:
        return "doji"
    body = abs(c["c"] - c["o"])
    lower = min(c["o"], c["c"]) - c["l"]
    upper = c["h"] - max(c["o"], c["c"])
    if body <= rng * 0.1:
        return "doji"
    if lower >= body * 2 and upper <= rng * 0.3:
        return "martelo"
    if body <= rng * 0.35:
        return "corpo estreito"
    return None


def evaluate_setup(ind, i, confirm_previous=False):
    """Regra de entrada, usada no sinal do screener, no backtest e no aporte "pelas regras do
    pullback" da carteira. confirm_previous: no intradiário, o último pregão fechado também
    precisa estar acima da média."""
    no_setup = {"trend": False, "pullback": None, "ma": None, "pattern": None, "topIdx": None,
                "ok": False, "okStrict": False}
    candles = ind["candles"]
    past = i - SETUP["mm200SlopeLookback"]
    if past < 0 or i < 19:
        return no_setup
    c = candles[i]
    mm200, mm200_past = ind["mm200"][i], ind["mm200"][past]
    if mm200 is None or mm200_past is None:
        return no_setup

    # Tendência primária de alta: MM200 subindo e preço, MM20 e MM50 acima dela.
    # MM20 abaixo da MM50 é aceito (correções profundas quebram esse alinhamento).
    trend = mm200 > mm200_past and c["c"] > mm200 and ind["mm20"][i] > mm200 and ind["mm50"][i] > mm200

    # Pullback = do topo de 20 pregões até hoje; profundidade medida pela menor mínima,
    # para que um repique já iniciado não descaracterize a correção.
    top = i - 19
    for j in range(i - 19, i + 1):
        if candles[j]["h"] >= candles[top]["h"]:
            top = j
    lowest = min(candles[j]["l"] for j in range(top, i + 1))
    pullback = 1 - lowest / candles[top]["h"]

    # Média testada: fechamento atual acima dela (até maxDistance). Fechamentos anteriores
    # do pullback podem ficar abaixo, desde que próximos (até maxBelowDistance) — quem
    # fechou muito abaixo perdeu o suporte em vez de testá-lo.
    ma = None
    for name in ("MM20", "MM50", "MM200"):
        arr = ind[name.lower()]
        dist = c["c"] / arr[i] - 1
        if dist < 0 or dist > SETUP["maxDistance"]:
            continue
        if confirm_previous and candles[i - 1]["c"] < arr[i - 1]:
            continue
        near = all(candles[j]["c"] >= arr[j] * (1 - SETUP["maxBelowDistance"]) for j in range(top, i))
        if near and (ma is None or dist < ma["dist"]):
            ma = {"name": name, "value": arr[i], "dist": dist}

    pattern = candle_pattern(c)
    ok = trend and pullback >= SETUP["minPullback"] and ma is not None
    return {"trend": trend, "pullback": pullback, "ma": ma, "pattern": pattern, "topIdx": top,
            "ok": ok, "okStrict": ok and pattern is not None}


# ---------------------------------------------------------------- Aritmética e formatadores

def add_up(values):
    """Soma da esquerda para a direita, como o reduce do JavaScript (o sum() do Python 3.12+
    compensa erros de arredondamento e pode diferir na última casa)."""
    total = 0.0
    for v in values:
        total += v
    return total


def avg(values):
    values = list(values)
    return add_up(values) / len(values) if values else 0


def js_round(x):
    """Math.round do JavaScript: meio arredonda para cima (o round() do Python é bancário)."""
    return math.floor(x + 0.5)


def _pt_br(text):
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def fmt_num(v, d=1):
    """Como toLocaleString('pt-BR') com d casas: arredonda o valor binário exato, meio para cima."""
    exact = Decimal(v).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    return _pt_br(f"{exact:,.{d}f}")


def fmt_brl(v):
    return f"R$ {fmt_num(v, 2)}"


def fmt_pct_value(v):
    """Fração → número em % sem casas desnecessárias: 0,1 → "10"; 0,125 → "12,5"."""
    v = round(v * 100, 4)
    return fmt_num(v, 0 if float(v).is_integer() else 1)


def fmt_qty(q):
    """Quantidade: inteira sem casas; fracionária (cripto, EUA) com até 8 casas."""
    if float(q).is_integer():
        return fmt_num(q, 0)
    exact = Decimal(q).quantize(Decimal("1e-8"), rounding=ROUND_HALF_UP)
    return _pt_br(f"{exact:,.8f}".rstrip("0").rstrip("."))


SESSIONS_PER_MONTH = 21  # pregões num mês, em média (cerca de 252 por ano)


def fmt_duration(months):
    """32 → "2 anos e 8 meses"; 12 → "1 ano"; 0 → "menos de 1 mês"."""
    months = int(months)
    if months <= 0:
        return "menos de 1 mês"
    years, rest = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} {'ano' if years == 1 else 'anos'}")
    if rest:
        parts.append(f"{rest} {'mês' if rest == 1 else 'meses'}")
    return " e ".join(parts)


def fmt_sessions(sessions):
    """Pregões → tempo aproximado em anos e meses (21 pregões por mês): 303 → "1 ano e 2 meses"."""
    return fmt_duration(round(sessions / SESSIONS_PER_MONTH))


def fmt_date(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}" if iso else "—"
