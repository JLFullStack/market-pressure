"""Risco da carteira (Visão geral → "Risco"): maior queda histórica, queda atual e VaR/CVaR
históricos de 1 dia e de 1 mês, da carteira e do BOVA11 no mesmo período. Só cálculo, sem rede nem
disco: usa a série diária de portfolio.build_series (rentabilidade pela cota, sem o efeito dos
aportes, a mesma do gráfico de rentabilidade).

VaR 95% = a perda que só os 5% piores dias (ou meses) superaram; CVaR 95% = a média desses 5%
piores. Históricos (os dias reais da carteira, sem supor uma distribuição): olham só o passado.
"""
import math

from ..comum.core import fmt_brl, fmt_date, fmt_num

# region Parâmetros
LEVEL = 95            # nível do VaR/CVaR (%)
MONTH = 21            # pregões de "1 mês" (janelas móveis, sobrepostas)
MIN_SESSIONS = 20     # com menos pregões, não mostra o risco
RELIABLE_SESSIONS = 252  # com menos de 1 ano, avisa que os números são pouco confiáveis
# endregion


# region Medidas
def _tail(returns):
    """(VaR, CVaR) em fração (negativos = perda) do nível LEVEL; (None, None) sem dados."""
    if not returns:
        return None, None
    ordered = sorted(returns)
    k = max(0, math.floor(len(ordered) * (100 - LEVEL) / 100) - 1)
    worst = ordered[:k + 1]
    return ordered[k], sum(worst) / len(worst)


def _drawdowns(index):
    """Queda desde o topo anterior em cada dia (fração, ≤ 0)."""
    out, peak = [], index[0]
    for v in index:
        peak = max(peak, v)
        out.append(v / peak - 1 if peak > 0 else 0.0)
    return out


def measures(index, dates):
    """Medidas de risco de uma série de cota (index: 1 = início; dates: AAAA-MM-DD do mesmo tamanho)."""
    n = len(index)
    dd = _drawdowns(index)
    trough = min(range(n), key=lambda k: dd[k])
    peak = max(range(trough + 1), key=lambda k: index[k])
    recovered = next((k for k in range(trough + 1, n) if index[k] >= index[peak]), None)
    current_peak = max(range(n), key=lambda k: index[k])
    daily = [index[k] / index[k - 1] - 1 for k in range(1, n) if index[k - 1] > 0]
    monthly = [index[k] / index[k - MONTH] - 1 for k in range(MONTH, n) if index[k - MONTH] > 0]
    var_d, cvar_d = _tail(daily)
    var_m, cvar_m = _tail(monthly)
    mean = sum(daily) / len(daily) if daily else 0
    vol = math.sqrt(sum((r - mean) ** 2 for r in daily) / len(daily)) * math.sqrt(252) if daily else None
    return {
        "maxDrawdown": {
            "pct": dd[trough] * 100, "peak": dates[peak], "trough": dates[trough],
            "recovered": dates[recovered] if recovered is not None else None,
            "fallSessions": trough - peak, "recoverySessions": recovered - trough if recovered is not None else None,
        },
        "current": {"pct": dd[-1] * 100, "peak": dates[current_peak]},
        "day": {"var": _pct(var_d), "cvar": _pct(cvar_d), "samples": len(daily)},
        "month": {"var": _pct(var_m), "cvar": _pct(cvar_m), "samples": len(monthly)},
        "volatility": vol * 100 if vol is not None else None,
        "drawdowns": [v * 100 for v in dd],
    }


def _pct(v):
    return v * 100 if v is not None else None
# endregion


# region Risco da carteira
def analyze(series, total):
    """Tudo o que o bloco "Risco" mostra, ou None com menos de MIN_SESSIONS pregões. total: patrimônio
    de hoje (para as perdas em R$)."""
    if not series or len(series.get("dates") or []) < MIN_SESSIONS:
        return None
    dates = series["dates"]
    port = measures([1 + v / 100 for v in series["portfolio"]], dates)
    bova = measures([1 + v / 100 for v in series["bova"]], dates)
    money = lambda pct: -pct / 100 * total if pct is not None and pct < 0 else 0.0
    for block in ("day", "month"):
        port[block]["varBRL"] = money(port[block]["var"])
        port[block]["cvarBRL"] = money(port[block]["cvar"])
    chart = {"labels": dates, "portfolio": port.pop("drawdowns"), "bova": bova.pop("drawdowns")}
    sessions = len(dates) - 1
    warning = None
    if sessions < RELIABLE_SESSIONS:
        warning = (f"Só {sessions} pregões de histórico (menos de 1 ano): poucos dias ruins para medir, os números "
                   "ainda são pouco confiáveis.")
    elif port["month"]["samples"] < RELIABLE_SESSIONS:
        warning = "Menos de 1 ano de janelas de 1 mês: o VaR e o CVaR mensais ainda são pouco confiáveis."
    return {"level": LEVEL, "month": MONTH, "sessions": sessions, "from": dates[0], "to": dates[-1], "total": total,
            "portfolio": port, "bova": bova, "chart": chart, "warning": warning, "text": summary(port, bova, total)}


def summary(port, bova, total):
    """Frases do bloco (e do ANALISAR CARTEIRA): o mês ruim, a maior queda e a comparação com o BOVA11."""
    lines = []
    m = port["month"]
    if m["cvar"] is not None and m["cvar"] < 0:
        lines.append(f"Num mês ruim (1 em cada 20), a carteira perdeu {fmt_num(-m['var'], 1)}% ou mais "
                     f"({fmt_brl(m['varBRL'])} sobre o patrimônio de hoje); nesses meses, a perda média foi "
                     f"{fmt_num(-m['cvar'], 1)}% ({fmt_brl(m['cvarBRL'])}).")
    dd = port["maxDrawdown"]
    if dd["pct"] < 0:
        back = (f"recuperada em {fmt_date(dd['recovered'])}" if dd["recovered"]
                else "ainda não recuperada")
        lines.append(f"Maior queda: {fmt_num(-dd['pct'], 1)}%, de {fmt_date(dd['peak'])} a {fmt_date(dd['trough'])}, {back}.")
    if port["current"]["pct"] < -0.05:
        lines.append(f"Hoje a carteira está {fmt_num(-port['current']['pct'], 1)}% abaixo do topo de "
                     f"{fmt_date(port['current']['peak'])}.")
    a, b = port["month"]["cvar"], bova["month"]["cvar"]
    if a is not None and b is not None and a < 0 and b < 0:
        ratio = a / b
        how = ("perdeu menos que o BOVA11" if ratio < 0.9 else "perdeu mais que o BOVA11" if ratio > 1.1
               else "perdeu quase o mesmo que o BOVA11")
        lines.append(f"Nos meses ruins, a carteira {how} no mesmo período ({fmt_num(-a, 1)}% contra "
                     f"{fmt_num(-b, 1)}% em média).")
    return lines
# endregion
