"""Previsão do cruzamento da MM50 para baixo da MM200 (a última condição da reversão de tendência,
core.exit_signal), validada no histórico em 26/11/2026, e o registro ao vivo do acerto dela.

Quando gera: o fechamento está abaixo da MM200 e a MM50 ainda está acima dela. O modelo é a
projeção linear das médias, calibrada: com a MM50 e a MM200 andando no ritmo dos últimos
PROJ_WINDOW pregões, em quantos pregões elas se encontram? Se o encontro cai dentro do prazo, a
probabilidade é a frequência histórica dos casos assim; senão, a dos casos fora do prazo (PROB,
ajustada só em 2016–2021). Na validação fora da amostra (2022–2026, ~19 mil casos de ações, FIIs,
ETFs e BDRs acima do piso de liquidez) ficou calibrada em todas as faixas, com acerto de 86–87% nas
previsões acima de 50% e erro mediano do prazo de 1 a 3 pregões (VALIDATION). Modelos mais
complexos (regressão logística, faixas de spread) não passaram na calibração em todos os prazos.

O cruzamento para cima (MM50 voltando acima da MM200, chance de recompra) não se confirmou: é raro
(1–12% dos casos em 10–40 pregões) e nenhum modelo calibrado passou de 50%. Por isso ele não é
previsto, para não passar falsa precisão.

A previsão só informa: não muda a Situação nem as regras de venda. Filtros que esperam mais para
vender na reversão (spread mínimo, N pregões, MM200 caindo, margem abaixo da MM200) foram testados
no mesmo dia e não melhoraram a carteira de forma consistente.

Registro ao vivo (live_rows/live_stats): cada varredura recalcula, com o histórico de cada ativo do
universo, as previsões feitas a partir de FREEZE (o modelo é fixo e só usa o passado, então
recalcular dá a mesma previsão que teria saído naquele dia) e marca se confirmaram; o screener grava
na tabela crossing_log."""

import logging
import math
from datetime import date, timedelta

from . import database as db
from .core import fmt_date, fmt_num

log = logging.getLogger(__name__)

FREEZE = "2026-11-26"          # dia em que o modelo foi validado: o registro ao vivo começa aqui
HORIZONS = (10, 20, 40)        # prazos em pregões
PROJ_WINDOW = 5                # pregões para o ritmo das médias
PROJ_DRAW_MAX = 60             # pregões máximos da projeção desenhada no gráfico
# Probabilidade de cruzar em até h pregões: (encontro projetado dentro do prazo, fora dele).
PROB = {10: (0.847, 0.104), 20: (0.870, 0.356), 40: (0.909, 0.721)}
# Validação fora da amostra (2022–2026): casos, acerto das previsões acima de 50%, maior diferença
# entre o previsto e o real nas faixas com 30 casos ou mais (p.p.), erro mediano do prazo (pregões)
# e o Brier contra o da frequência geral, sem variáveis.
VALIDATION = {10: {"cases": 19785, "hitRate": 87.1, "worstBin": 2.4, "dayError": 1.0, "brier": 0.096, "brierBase": 0.221},
              20: {"cases": 18966, "hitRate": 86.3, "worstBin": 1.9, "dayError": 1.8, "brier": 0.168, "brierBase": 0.239},
              40: {"cases": 18756, "hitRate": 86.0, "worstBin": 0.9, "dayError": 2.8, "brier": 0.114, "brierBase": 0.120}}
LIVE_MIN_CLOSED = 30           # previsões encerradas para o alerta de revisão
LIVE_ALERT_GAP = 15            # p.p. entre a calibração ao vivo e a histórica que pedem revisão
LOG_TABLE = "crossing_log"


# region Previsão
def projected_sessions(ind, i):
    """Pregões até a MM50 encontrar a MM200 no ritmo dos últimos PROJ_WINDOW pregões, ou None
    (médias se afastando, paradas ou sem dados)."""
    a, b = ind["mm50"], ind["mm200"]
    j = i - PROJ_WINDOW
    if j < 0 or None in (a[i], b[i], a[j], b[j]):
        return None
    gap, before = a[i] - b[i], a[j] - b[j]
    speed = (gap - before) / PROJ_WINDOW
    if speed == 0 or gap / speed >= 0:
        return None
    return -gap / speed


def in_state(ind, i):
    """O caso que o modelo prevê: fechamento abaixo da MM200 com a MM50 ainda acima dela."""
    c, a, b = ind["closes"][i], ind["mm50"][i], ind["mm200"][i]
    return None not in (a, b) and c < b and a > b


def probabilities(sessions):
    """{h: probabilidade (fração)} de a MM50 cruzar abaixo da MM200 em até h pregões."""
    return {h: PROB[h][0] if sessions is not None and sessions <= h else PROB[h][1] for h in HORIZONS}


def add_sessions(iso, n):
    """Data aproximada n pregões depois de iso (dias úteis; feriados da bolsa não são conhecidos)."""
    d, left = date.fromisoformat(iso), max(0, int(math.ceil(n)))
    while left:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d.isoformat()


def forecast(ind, i, day):
    """Previsão no pregão fechado i (dia iso `day`), ou None fora do caso previsto. Devolve o
    evento, os pregões projetados até o encontro e a data aproximada, as probabilidades em 10,
    20 e 40 pregões, o rótulo curto do gráfico, o texto e a projeção das duas médias."""
    if not in_state(ind, i):
        return None
    sessions = projected_sessions(ind, i)
    probs = probabilities(sessions)
    close, a, b = ind["closes"][i], ind["mm50"][i], ind["mm200"][i]
    # Rótulo: o menor prazo que contém o encontro projetado; sem encontro em 40, o de 40.
    h = next((h for h in HORIZONS if sessions is not None and sessions <= h), HORIZONS[-1])
    meet = add_sessions(day, sessions) if sessions is not None else None
    line = []
    if sessions is not None:
        v50 = (a - ind["mm50"][i - PROJ_WINDOW]) / PROJ_WINDOW
        v200 = (b - ind["mm200"][i - PROJ_WINDOW]) / PROJ_WINDOW
        steps = min(PROJ_DRAW_MAX, int(math.ceil(sessions)) + 3)
        line = [{"time": add_sessions(day, k), "mm50": a + v50 * k, "mm200": b + v200 * k} for k in range(0, steps + 1)]
    pct = lambda p: fmt_num(p * 100, 0)
    when = (f"no ritmo dos últimos {PROJ_WINDOW} pregões, as médias se encontram em cerca de {fmt_num(sessions, 0)} pregões "
            f"(perto de {fmt_date(meet)})" if sessions is not None
            else f"no ritmo dos últimos {PROJ_WINDOW} pregões, as médias não se aproximam")
    text = (f"Fechamento abaixo da MM200 com a MM50 ainda acima: {when}. Chance histórica de a MM50 cruzar abaixo da MM200 "
            f"(a última condição da reversão, o VENDER): {pct(probs[10])}% em 10 pregões, {pct(probs[20])}% em 20 e "
            f"{pct(probs[40])}% em 40. A previsão só informa: a venda continua sendo a da regra.")
    return {"event": "abaixo", "day": day, "sessions": sessions, "date": meet, "horizon": h,
            "probs": {str(k): v * 100 for k, v in probs.items()},
            "spread": (a / b - 1) * 100, "distance": (close / b - 1) * 100,
            "label": f"Cruz. abaixo: {pct(probs[h])}% em {h} pregões", "text": text, "projection": line}

# endregion


# region Registro ao vivo
def live_rows(ticker, ind, days, since=FREEZE):
    """Previsões de cada pregão fechado a partir de `since` em que o caso valia, com o resultado:
    hit = pregões até o cruzamento (None se ainda não cruzou) e avail = pregões já passados."""
    out = []
    a, b, n = ind["mm50"], ind["mm200"], len(days)
    for i in range(n):
        if days[i] < since or not in_state(ind, i):
            continue
        hit = next((k - i for k in range(i + 1, min(n, i + HORIZONS[-1] + 1)) if a[k] < b[k]), None)
        sessions = projected_sessions(ind, i)
        probs = probabilities(sessions)
        out.append({"date": days[i], "ticker": ticker, "sessions": sessions, "hit": hit, "avail": n - 1 - i,
                    **{f"p{h}": probs[h] for h in HORIZONS}})
    return out


def live_stats(rows):
    """Acerto ao vivo por prazo: previsões encerradas (o prazo passou ou o cruzamento veio), acerto
    das acima de 50%, média prevista × real e o alerta de revisão (LIVE_MIN_CLOSED encerradas com
    a calibração mais de LIVE_ALERT_GAP p.p. longe do previsto)."""
    out = {}
    for h in HORIZONS:
        closed = [r for r in rows if (r["hit"] is not None and r["hit"] <= h) or r["avail"] >= h]
        ok = [1 if (r["hit"] is not None and r["hit"] <= h) else 0 for r in closed]
        pos = [y for r, y in zip(closed, ok) if r[f"p{h}"] > 0.5]
        pred = sum(r[f"p{h}"] for r in closed) / len(closed) * 100 if closed else None
        real = sum(ok) / len(ok) * 100 if ok else None
        out[str(h)] = {"closed": len(closed), "open": len(rows) - len(closed), "predicted": pred, "real": real,
                       "hitRate": sum(pos) / len(pos) * 100 if pos else None, "positives": len(pos),
                       "alert": len(closed) >= LIVE_MIN_CLOSED and abs(pred - real) > LIVE_ALERT_GAP}
    return out


def accuracy_text(stats):
    """Linha curta do acerto: o histórico (validação) e o ao vivo (desde FREEZE)."""
    v = [fmt_num(VALIDATION[h]["hitRate"], 0) for h in HORIZONS]
    hist = f"na validação de 2022–2026, {v[0]}% em 10 pregões, {v[1]}% em 20 e {v[2]}% em 40"
    s = (stats or {}).get("20") or {}
    if not s.get("closed"):
        live = f"ao vivo: nenhuma previsão encerrada ainda (registro desde {fmt_date(FREEZE)})"
    else:
        hit = "—" if s["hitRate"] is None else f"{fmt_num(s['hitRate'], 0)}%"
        live = (f"ao vivo (20 pregões, desde {fmt_date(FREEZE)}): acerto {hit} em {s['positives']} previsão(ões) acima de 50%; "
                f"previsto {fmt_num(s['predicted'], 0)}% × real {fmt_num(s['real'], 0)}% em {s['closed']} encerrada(s)")
    alert = any(v.get("alert") for v in (stats or {}).values())
    return f"Acerto do modelo (previsões acima de 50% que se confirmaram): {hist}; {live}." + (" ATENÇÃO: a calibração ao vivo se afastou da histórica; o modelo precisa ser revisto." if alert else "")


def merge_log(previous, rows, analyzed):
    """Tabela do registro: as linhas recalculadas dos ativos desta varredura, mais as guardadas dos
    ativos que ficaram de fora dela (saíram do universo), para o registro não perder previsões."""
    keep = [r for r in previous if r["ticker"] not in analyzed]
    return sorted(keep + rows, key=lambda r: (r["date"], r["ticker"]))


def read_log():
    """Linhas salvas do registro ao vivo (vazio se a tabela não existe ou falha)."""
    try:
        return db.read_table(LOG_TABLE)
    except Exception:
        log.warning("não foi possível ler o registro ao vivo da previsão de cruzamento", exc_info=True)
        return []

# endregion
