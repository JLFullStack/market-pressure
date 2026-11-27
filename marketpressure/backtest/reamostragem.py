"""Reamostragem (bootstrap) das operações de um backtest: o resultado depende de poucas compras?

Sorteia as operações do próprio resultado, com reposição, DRAWS vezes (semente fixa: o mesmo
backtest dá sempre a mesma resposta) e mede a faixa provável do lucro e do resultado médio por
compra, a chance de o lucro ficar negativo ou abaixo das referências (BOVA11, CDI...) e o lucro sem
as maiores operações. Só cálculo, sem rede nem disco.

Limite: sortear operações ignora a ordem e o caixa de cada dia (na carteira, as compras crescem
com o patrimônio). É uma medida de quanto o resultado depende de poucos ganhos, não uma nova
simulação.
"""
import random

from ..comum.core import fmt_brl, fmt_date as _date, fmt_num

# region Parâmetros
DRAWS = 2000          # sorteios
SEED = 20261127       # semente fixa: o mesmo resultado sempre dá a mesma reamostragem
MIN_TRADES = 10       # com menos operações, o veredito é "inconclusivo"
TOP_CUTS = (1, 3, 5)  # "sem as N maiores"
FRAGILE_CUT = 3       # sem as FRAGILE_CUT maiores abaixo da referência = "depende de poucas operações"
BINS = 24             # barras do histograma
LOW, HIGH = 5, 95     # faixa provável: percentis 5 e 95
# endregion


# region Cálculo
def _percentile(ordered, p):
    """Percentil p (0–100) de uma lista já ordenada (posição mais próxima)."""
    return ordered[round(p / 100 * (len(ordered) - 1))]


def _band(ordered):
    return {"low": _percentile(ordered, LOW), "mid": _percentile(ordered, 50), "high": _percentile(ordered, HIGH)}


def _histogram(ordered):
    lo, hi = ordered[0], ordered[-1]
    if hi - lo < 1e-9:
        return [{"from": lo, "to": hi, "count": len(ordered)}]
    width = (hi - lo) / BINS
    counts = [0] * BINS
    for v in ordered:
        counts[min(int((v - lo) / width), BINS - 1)] += 1
    return [{"from": lo + k * width, "to": lo + (k + 1) * width, "count": c} for k, c in enumerate(counts)]


def _draws(profits, returns, base):
    """(lucros, resultados médios por compra) de cada sorteio, ordenados."""
    rnd, n = random.Random(SEED), len(profits)
    totals, means = [], []
    for _ in range(DRAWS):
        idx = rnd.choices(range(n), k=n)
        totals.append(base + sum(profits[i] for i in idx))
        means.append(sum(returns[i] for i in idx) / n)
    return sorted(totals), sorted(means)


def analyze(trades, actual, refs=(), label=lambda t: ""):
    """trades: [{profit (R$), return (%)}] com as abertas marcadas no fim; actual: o lucro do
    resultado (na carteira, inclui o que não é operação: CDI do caixa, imposto; essa parte fica fixa
    nos sorteios); refs: [{key, label, profit}] das referências (vazio = comparar com zero);
    label(trade): nome da operação no "sem as N maiores". None sem operações."""
    if not trades:
        return None
    profits = [t["profit"] for t in trades]
    returns = [t["return"] for t in trades]
    base = actual - sum(profits)
    totals, means = _draws(profits, returns, base)

    def below(value):
        return sum(1 for v in totals if v < value) / len(totals) * 100
    ref = refs[0] if refs else {"key": "zero", "label": "zero", "profit": 0.0}
    gains = sorted((t for t in trades if t["profit"] > 0), key=lambda t: -t["profit"])
    without = []
    for n in TOP_CUTS:
        if n >= len(trades) or n > len(gains):
            break
        removed = sum(t["profit"] for t in gains[:n])
        without.append({"n": n, "profit": actual - removed, "removed": removed,
                        "share": removed / actual * 100 if actual > 0 else None,
                        "names": [label(t) for t in gains[:n]],
                        "beats": {r["key"]: actual - removed > r["profit"] for r in refs} or {"zero": actual - removed > 0}})
    out = {
        "draws": DRAWS, "trades": len(trades), "actual": actual, "profit": _band(totals),
        "meanReturn": _band(means), "lossChance": below(0),
        "refs": [{**r, "belowChance": below(r["profit"])} for r in refs],
        "withoutTop": without, "histogram": _histogram(totals), "low": LOW, "high": HIGH,
    }
    out["verdict"] = _verdict(out, ref)
    return out
# endregion


# region Veredito
def _verdict(r, ref):
    """{key: consistente|incerto|fragil|abaixo|inconclusivo, title, text} (o texto que a página mostra)."""
    zero = ref["key"] == "zero"
    # "abaixo de zero" / "abaixo da referência (Índices nas metas, R$ X)"
    target = "zero" if zero else f"a referência ({ref['label']}, {fmt_brl(ref['profit'])})"
    chance = r["lossChance"] if zero else next(x["belowChance"] for x in r["refs"] if x["key"] == ref["key"])
    cut = next((w for w in r["withoutTop"] if w["n"] == FRAGILE_CUT), None)
    if r["trades"] < MIN_TRADES:
        return {"key": "inconclusivo", "title": "Inconclusivo",
                "text": f"Só {r['trades']} operação(ões): poucas para medir a sorte. Aumente o período ou o universo."}
    if r["actual"] <= ref["profit"]:
        return {"key": "abaixo", "title": "Prejuízo" if zero else "Abaixo da referência",
                "text": f"O lucro do período ({fmt_brl(r['actual'])}) já ficou abaixo de {target}, e em "
                        f"{fmt_num(chance, 0)}% dos sorteios também."}
    if cut and cut["profit"] <= ref["profit"]:
        names = ", ".join(n for n in cut["names"] if n)
        miss = "não teria dado lucro" if zero else f"teria rendido menos que {ref['label']}"
        return {"key": "fragil", "title": "Depende de poucas operações",
                "text": f"Sem as {FRAGILE_CUT} melhores operações{f' ({names})' if names else ''}, o lucro cai para "
                        f"{fmt_brl(cut['profit'])}, abaixo de {target}. O resultado veio de poucos ganhos grandes: "
                        f"num período sem eles, a estratégia {miss}."}
    if r["profit"]["low"] > ref["profit"]:
        return {"key": "consistente", "title": "Resultado consistente",
                "text": f"Em {fmt_num(100 - r['low'], 0)}% dos sorteios o lucro ficou acima de {target}, e sem as "
                        f"{FRAGILE_CUT} melhores operações ainda fica acima. O resultado não depende de poucas compras."}
    return {"key": "incerto", "title": "Resultado incerto",
            "text": f"Em {fmt_num(chance, 0)}% dos sorteios o lucro ficou abaixo de {target}. Parte do resultado pode "
                    "ter vindo de quais operações aconteceram no período, não só da regra."}
# endregion


# region Resultados do backtest
def single(view):
    """Reamostragem do modo "Um ativo" (screener.backtest_view): os lotes, com o valor por compra."""
    lots = [lot for lot in view["lots"] if lot.get("return") is not None]
    return analyze(lots, sum(lot["profit"] for lot in lots),
                   label=lambda lot: f"compra {lot['n']} de {_date(lot['entry']['date'])}")


# Referências da carteira, na ordem de preferência para o veredito (a régua justa com metas por
# classe é o mix dos índices; senão o BOVA11), mais as demais para as chances.
PORTFOLIO_REFS = ("mix", "bova", "ivvb", "cdi")


def portfolio(result):
    """Reamostragem do modo "Carteira" (portfolio_backtest.run): as operações da estratégia
    escolhida; com imposto, o lucro líquido (vendendo tudo no fim), como na comparação da página."""
    rule = result["strategies"][0]["metrics"]
    key = "profitNet" if rule.get("profitNet") is not None else "profit"
    by_key = {s["key"]: s for s in result["strategies"]}
    refs = [{"key": k, "label": by_key[k]["label"].replace(" (mesmos aportes)", ""), "profit": by_key[k]["metrics"][key]}
            for k in PORTFOLIO_REFS if k in by_key and by_key[k]["metrics"].get(key) is not None]
    return analyze(result["trades"], rule[key], refs,
                   label=lambda t: f"{t['ticker']} de {_date(t['entry']['date'])}")
# endregion
