"""Metas financeiras da Carteira (sub-aba Metas): validação do formulário e projeção de cada meta.

Só funções puras (sem rede nem disco). A projeção é mensal e em valores de hoje (descontada a
inflação): o patrimônio rende a taxa real e recebe o aporte no fim de cada mês. Com "corrigir o
aporte pela inflação", o aporte mantém o poder de compra (constante em valores de hoje); sem
isso, o mesmo valor nominal vale um pouco menos a cada mês.

Meta de patrimônio: o valor alvo. Meta de renda: o patrimônio que gera a renda mensal desejada
com a taxa de retirada anual informada (renda × 12 ÷ taxa).
"""

import math
from datetime import date

from .core import fmt_brl, fmt_date, fmt_num
from .portfolio import add_months, is_iso_date, lower_bound, parse_number

KINDS = {"patrimonio": "Patrimônio", "renda": "Renda mensal"}
MAX_MONTHS = 1200          # projeção até 100 anos
MAX_TABLE_YEARS = 60       # linhas da tabela ano a ano
MILESTONES = (25, 50, 75, 100)
DEFAULT_WITHDRAWAL = 4.0   # % ao ano, para a "renda possível" de uma meta de patrimônio
SCENARIO_RATE_STEP = 2.0   # pontos percentuais para cima e para baixo na tabela de cenários
SCENARIO_MONTHLY = (1, 1.5, 2)
TRACK_MONTHS = 12          # janela do aporte médio real
MIN_RETURN_DAYS = 90       # rentabilidade real da carteira só com pelo menos 3 meses de série
DAYS_PER_MONTH = 365.25 / 12


def duration_text(months):
    """32 → "2 anos e 8 meses"; 12 → "1 ano"; 0 → "já alcançada"."""
    if months <= 0:
        return "já alcançada"
    years, rest = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} {'ano' if years == 1 else 'anos'}")
    if rest:
        parts.append(f"{rest} {'mês' if rest == 1 else 'meses'}")
    return " e ".join(parts)


def _month_label(iso):
    """2031-07-13 → "07/2031"."""
    return f"{iso[5:7]}/{iso[:4]}"


def goal_from_form(body, today, goal_id=None):
    """Meta a partir dos campos do formulário (números podem vir como texto digitado). Devolve
    (meta, None) ou (None, mensagem de erro)."""
    name = str(body.get("name") or "").strip()
    if not name:
        return None, "Informe o nome da meta."
    if len(name) > 60:
        return None, "Use um nome com até 60 caracteres."
    kind = body.get("kind")
    if kind not in KINDS:
        return None, "Escolha o tipo da meta."
    target = parse_number(body.get("target"))
    if target is None or target <= 0:
        return None, ("Informe a renda mensal desejada (maior que zero)." if kind == "renda"
                      else "Informe o patrimônio que quer alcançar (maior que zero).")
    withdrawal = parse_number(body.get("withdrawal"), DEFAULT_WITHDRAWAL)
    if withdrawal is None or not 0 < withdrawal <= 20:
        return None, "A taxa de retirada deve ficar entre 0 e 20% ao ano."
    use_portfolio = bool(body.get("usePortfolio"))
    initial = 0.0 if use_portfolio else parse_number(body.get("initial"), 0)
    if initial is None or initial < 0:
        return None, "O patrimônio inicial não pode ser negativo."
    monthly = parse_number(body.get("monthly"), 0)
    if monthly is None or monthly < 0:
        return None, "O aporte mensal não pode ser negativo."
    rate = parse_number(body.get("rate"))
    if rate is None or not -20 < rate <= 100:
        return None, "Informe a rentabilidade esperada (% ao ano, entre −20 e 100)."
    inflation = parse_number(body.get("inflation"), 0)
    if inflation is None or not 0 <= inflation <= 50:
        return None, "A inflação deve ficar entre 0 e 50% ao ano."
    target_date = str(body.get("targetDate") or "").strip() or None
    if target_date and (not is_iso_date(target_date) or target_date <= today):
        return None, "A data alvo deve ser uma data futura."
    if target_date and target_date > add_months(today, MAX_MONTHS):
        return None, "A data alvo deve ficar a no máximo 100 anos de hoje."
    if monthly <= 0 and initial <= 0 and not use_portfolio:
        return None, "Informe um aporte mensal ou um patrimônio inicial."
    return {"id": goal_id, "name": name, "kind": kind, "target": target, "withdrawal": withdrawal,
            "initial": initial, "usePortfolio": use_portfolio, "monthly": monthly, "rate": rate,
            "inflation": inflation, "adjust": bool(body.get("adjust", True)), "targetDate": target_date}, None


def target_capital(goal):
    """Patrimônio necessário (valores de hoje)."""
    if goal["kind"] == "renda":
        return goal["target"] * 12 / (goal["withdrawal"] / 100)
    return goal["target"]


def rates(goal):
    """Taxas ao ano e ao mês: nominal, inflação e real (descontada a inflação), em fração."""
    nominal = goal["rate"] / 100
    inflation = goal["inflation"] / 100
    real = (1 + nominal) / (1 + inflation) - 1
    monthly = lambda r: (1 + r) ** (1 / 12) - 1
    return {"nominalYear": nominal, "nominalMonth": monthly(nominal), "inflationYear": inflation,
            "inflationMonth": monthly(inflation), "realYear": real, "realMonth": monthly(real)}


def simulate(start, monthly, real_month, inflation_month, adjust, months, stop_at=None):
    """Patrimônio mês a mês em valores de hoje: [(mês, patrimônio, aportado acumulado, aporte do mês)],
    do mês 0 (start) até `months`, ou até passar de stop_at."""
    value, contributed = start, 0.0
    rows = [(0, value, 0.0, 0.0)]
    for t in range(1, months + 1):
        deposit = monthly if adjust else monthly / (1 + inflation_month) ** t
        value = value * (1 + real_month) + deposit
        contributed += deposit
        rows.append((t, value, contributed, deposit))
        if stop_at is not None and value >= stop_at:
            break
    return rows


def months_to_reach(start, monthly, real_month, inflation_month, adjust, goal_value):
    """Meses até o patrimônio alcançar goal_value (0 se já alcança; None se não alcança em 100 anos)."""
    if start >= goal_value:
        return 0
    rows = simulate(start, monthly, real_month, inflation_month, adjust, MAX_MONTHS, goal_value)
    return rows[-1][0] if rows[-1][1] >= goal_value else None


def required_monthly(start, real_month, inflation_month, adjust, goal_value, months):
    """Aporte mensal (em valores de hoje, no primeiro mês) para alcançar goal_value em `months` meses."""
    def reaches(a):
        return simulate(start, a, real_month, inflation_month, adjust, months)[-1][1] >= goal_value
    if reaches(0):
        return 0.0
    lo, hi = 0.0, goal_value
    while not reaches(hi):
        hi *= 2
    for _ in range(60):
        mid = (lo + hi) / 2
        if reaches(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _days(d0, d1):
    return (date.fromisoformat(d1) - date.fromisoformat(d0)).days


def average_contribution(flows, today, months=TRACK_MONTHS):
    """Aporte líquido médio por mês (compras e aportes − vendas e resgates, em reais) nos últimos
    `months` meses, ou desde o primeiro lançamento se a carteira é mais nova. flows: [(data, valor)].
    None sem lançamentos."""
    if not flows:
        return None
    start = max(add_months(today, -months), min(d for d, _ in flows))
    span = max(1.0, _days(start, today) / DAYS_PER_MONTH)
    total = sum(v for d, v in flows if d >= start)
    return {"monthly": total / span, "from": start, "months": span}


def annualized_return(pct, start, end):
    """Rentabilidade acumulada (%) de start a end → % ao ano. None com menos de MIN_RETURN_DAYS."""
    days = _days(start, end)
    if days < MIN_RETURN_DAYS:
        return None
    return ((1 + pct / 100) ** (365 / days) - 1) * 100


def value_on(dates, values, iso):
    """Patrimônio da série no dia iso (ou no último pregão antes dele); None antes da série."""
    k = lower_bound(dates, iso)
    idx = k if k < len(dates) and dates[k] == iso else k - 1
    return values[idx] if idx >= 0 else None


def tracking(goal, series, current, today):
    """Realizado × planejado de uma meta que parte da carteira, desde a data em que foi criada:
    o plano começa no patrimônio da carteira naquele dia e segue o aporte e a rentabilidade da
    meta (em valores nominais, para comparar com o patrimônio da carteira). series: {dates, value}
    da série diária da carteira; current: patrimônio de agora."""
    created = goal.get("created") or today
    if created >= today:
        return {"created": created, "started": False,
                "summary": "O acompanhamento começa hoje: a partir dos próximos aportes, aqui aparece o realizado × planejado."}
    dates, values = (series or {}).get("dates") or [], (series or {}).get("value") or []
    start_value = value_on(dates, values, created) or 0.0
    r = rates(goal)
    elapsed = _days(created, today) / DAYS_PER_MONTH
    whole = int(elapsed)
    rows = simulate(start_value, goal["monthly"], r["realMonth"], r["inflationMonth"], goal["adjust"], whole)

    def planned(k, fraction=0.0):
        """Plano em reais da data (nominal): valor real × inflação acumulada. No mês em curso, o
        aporte entra proporcional ao tempo já passado (o real pode ter sido feito em qualquer dia)."""
        deposit = goal["monthly"] if goal["adjust"] else goal["monthly"] / (1 + r["inflationMonth"]) ** (k + 1)
        real = rows[k][1] * (1 + r["realMonth"]) ** fraction + deposit * fraction
        return real * (1 + r["inflationMonth"]) ** (k + fraction)

    labels, plan, actual = [], [], []
    for k in range(whole + 1):
        d = add_months(created, k)
        labels.append(d)
        plan.append(planned(k))
        actual.append(value_on(dates, values, d) or 0.0)
    labels.append(today)
    plan.append(planned(whole, elapsed - whole))
    actual.append(current)
    diff = current - plan[-1]
    ahead = diff >= 0
    summary = (f"Desde {fmt_date(created)}, a carteira está {fmt_brl(abs(diff))} "
               f"{'à frente do' if ahead else 'atrás do'} plano ({fmt_brl(current)} contra {fmt_brl(plan[-1])} planejados).")
    return {"created": created, "started": True, "startValue": start_value, "planned": plan[-1], "actual": current,
            "diff": diff, "diffPct": diff / plan[-1] * 100 if plan[-1] > 0 else None, "ahead": ahead,
            "summary": summary, "chart": {"labels": labels, "planned": plan, "actual": actual}}


def projections(goal, current, today, contribution=None, real_return=None):
    """Tempo para alcançar a meta pelo plano e pelos dados reais da carteira: aporte médio real
    (average_contribution) e rentabilidade real anualizada (% ao ano, nominal)."""
    goal_value = target_capital(goal)

    def row(label, monthly, rate, note):
        r = rates({**goal, "rate": rate})
        m = months_to_reach(current, max(0.0, monthly), r["realMonth"], r["inflationMonth"], goal["adjust"], goal_value)
        return {"label": label, "monthly": monthly, "rate": rate, "note": note, "months": m,
                "text": duration_text(m) if m is not None else "não alcança em 100 anos",
                "date": add_months(today, m) if m is not None else None}

    out = [row("Plano da meta", goal["monthly"], goal["rate"], "aporte e rentabilidade digitados na meta")]
    if contribution:
        note = f"média de {fmt_num(contribution['months'], 0 if contribution['months'] >= 2 else 1)} mês(es) de lançamentos"
        out.append(row("Seu aporte real", contribution["monthly"], goal["rate"], note))
    if real_return is not None:
        out.append(row("Rentabilidade real da carteira", goal["monthly"], real_return["rate"], real_return["note"]))
        if contribution:
            out.append(row("Aporte e rentabilidade reais", contribution["monthly"], real_return["rate"],
                           "o ritmo atual da carteira, se continuar"))
    return out


def project(goal, current, today):
    """Tudo o que o cartão da meta mostra. current = patrimônio de partida (o inicial informado, ou
    o valor atual da carteira quando a meta usa a carteira)."""
    r = rates(goal)
    goal_value = target_capital(goal)
    withdrawal = goal["withdrawal"] / 100
    args = (r["realMonth"], r["inflationMonth"], goal["adjust"])
    months = months_to_reach(current, goal["monthly"], *args, goal_value)
    horizon = months if months is not None else MAX_MONTHS
    target_months = None
    if goal["targetDate"]:
        d0, d1 = today, goal["targetDate"]
        target_months = (int(d1[:4]) - int(d0[:4])) * 12 + int(d1[5:7]) - int(d0[5:7]) - (d1[8:] < d0[8:])
        target_months = max(1, target_months)
        horizon = max(horizon, target_months)
    rows = simulate(current, goal["monthly"], *args, horizon)
    warnings = []
    if r["realYear"] <= 0:
        warnings.append("A rentabilidade esperada não supera a inflação: o patrimônio não cresce em termos reais.")

    reach = None
    if months is not None:
        t, value, contributed, _ = rows[months]
        interest = value - current - contributed
        reach = {
            "months": months, "date": add_months(today, months), "text": duration_text(months),
            "contributed": contributed, "interest": interest, "value": value,
            "interestShare": interest / value * 100 if value > 0 else 0,
            "nominalValue": goal_value * (1 + r["inflationYear"]) ** (months / 12),
            "nominalContributed": sum(goal["monthly"] * ((1 + r["inflationMonth"]) ** k if goal["adjust"] else 1)
                                      for k in range(1, months + 1)),
        }
    if months is None:
        summary = (f"Com aporte de {fmt_brl(goal['monthly'])} por mês e {fmt_num(goal['rate'], 1)}% ao ano, "
                   f"a meta não é alcançada em 100 anos. Aumente o aporte ou revise a rentabilidade.")
    elif months == 0:
        summary = "Meta já alcançada com o patrimônio atual."
    else:
        summary = (f"Você alcança a meta em {duration_text(months)} ({_month_label(reach['date'])}), "
                   f"aportando {fmt_brl(goal['monthly'])} por mês"
                   f"{' corrigidos pela inflação' if goal['adjust'] else ''}.")

    # Ponto de virada: primeiro mês em que os juros do mês superam o aporte do mês.
    turning = next((t for t, value, _, deposit in rows[1:]
                    if deposit > 0 and rows[t - 1][1] * r["realMonth"] >= deposit), None)
    milestones = []
    for pct in MILESTONES:
        m = months_to_reach(current, goal["monthly"], *args, goal_value * pct / 100)
        milestones.append({"pct": pct, "months": m, "date": add_months(today, m) if m is not None else None,
                           "text": duration_text(m) if m is not None else "não alcança em 100 anos",
                           "reached": m == 0})

    by_date = None
    if target_months is not None:
        projected = rows[target_months][1]
        need = required_monthly(current, *args, goal_value, target_months)
        on_track = projected >= goal_value
        by_date = {
            "date": goal["targetDate"], "months": target_months, "text": duration_text(target_months),
            "projected": projected, "gap": goal_value - projected, "requiredMonthly": need, "onTrack": on_track,
            "summary": (f"No ritmo atual, em {fmt_date(goal['targetDate'])} você terá {fmt_brl(projected)} "
                        + ("e a meta estará alcançada." if on_track
                           else f"(faltarão {fmt_brl(goal_value - projected)}). Para chegar lá nessa data, "
                                f"aporte {fmt_brl(need)} por mês.")),
        }

    base_rate = goal["rate"]
    scenario_rates = [base_rate - SCENARIO_RATE_STEP, base_rate, base_rate + SCENARIO_RATE_STEP]
    scenario_monthly = ([goal["monthly"] * k for k in SCENARIO_MONTHLY] if goal["monthly"] > 0 else [500.0, 1000.0, 2000.0])
    cells = []
    for a in scenario_monthly:
        line = []
        for rate in scenario_rates:
            rr = rates({**goal, "rate": rate})
            m = months_to_reach(current, a, rr["realMonth"], rr["inflationMonth"], goal["adjust"], goal_value)
            line.append({"months": m, "text": duration_text(m) if m is not None else "+100 anos",
                         "current": a == goal["monthly"] and rate == base_rate})
        cells.append({"monthly": a, "cells": line})

    yearly = []
    last_year = min(math.ceil(horizon / 12), MAX_TABLE_YEARS)
    for y in range(0, last_year + 1):
        t = min(y * 12, len(rows) - 1)
        _, value, contributed, _ = rows[t]
        yearly.append({"year": y, "date": add_months(today, t), "contributed": contributed + current,
                       "interest": value - current - contributed, "value": value,
                       "income": value * withdrawal / 12, "progress": value / goal_value * 100})

    return {
        "goalValue": goal_value, "current": current, "missing": max(0.0, goal_value - current),
        "progress": min(100.0, current / goal_value * 100),
        "income": goal_value * withdrawal / 12, "incomeNow": current * withdrawal / 12,
        "monthlyInterestAtGoal": goal_value * r["realMonth"],
        "rates": {k: v * 100 for k, v in r.items()},
        "reach": reach, "summary": summary,
        "turningPoint": turning and {"months": turning, "date": add_months(today, turning), "text": duration_text(turning)},
        "milestones": milestones, "byDate": by_date,
        "scenarios": {"rates": scenario_rates, "rows": cells},
        "yearly": yearly, "warnings": warnings,
    }
