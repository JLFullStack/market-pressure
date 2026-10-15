"""Botão "COPIAR ANÁLISE" do ranking do Screener: o texto (prompt) que o usuário cola numa IA.

Monta, para um ativo do ranking, as instruções do papel (analista experiente que fala de forma
simples), a tarefa (comprar agora ou esperar, e por quê) e todos os dados que o app tem dele: a
linha do ranking, os componentes da pressão, o backtest, os indicadores do histórico, o regime do
tipo, o BOVA11, a estratégia de saída salva e as compras abertas da carteira.
"""

import logging

from . import market_data, screener
from .core import (adjusted_candles, closed_candles, compute_indicators, day_of, fmt_brl, fmt_date, fmt_num,
                   fmt_pct_value, fmt_qty, local_time)

log = logging.getLogger(__name__)

MODE_LABEL = {"fechamento": "último fechamento (pregões encerrados)",
              "intradiario": "intradiário (inclui o pregão em andamento, se houver)"}
BOTTLE_LABEL = {"cheia": "Cheia (pressão ≥ 75%)", "quase": "Quase cheia (pressão de 60% a 75%)",
                "enchendo": "Enchendo (pressão abaixo de 60%)"}
REGIME_LABEL = {"alta": "alta (pullbacks tendem a ser oportunidades)",
                "misto": "misto (mercado seletivo)", "defensivo": "defensivo (poucos ativos em alta)"}
RECENT_SESSIONS = 10  # pregões mostrados na tabela de candles recentes
RETURN_WINDOWS = (("1 mês", 21), ("3 meses", 63), ("6 meses", 126), ("12 meses", 252))

ROLE = """# Seu papel

Você é um analista financeiro assistente renomado, com mais de 50 anos de experiência no mercado de \
ações brasileiro e internacional. Atravessou crises, bolhas e ciclos de alta e construiu ao longo \
dessas décadas um histórico de ganhos expressivos e consistentes, sempre com disciplina de risco. \
Domina análise técnica (médias móveis, volume, momentum, padrões de candle), análise fundamentalista \
e leitura do cenário macroeconômico.

Fale com a clareza de um bom professor: qualquer pessoa leiga precisa entender sua recomendação. \
Use frases curtas, evite jargão e, quando um termo técnico for inevitável, explique-o em poucas \
palavras (por exemplo: "MM200, a média dos preços dos últimos 200 pregões, que mostra a tendência \
de longo prazo"). Seja direto, honesto e concreto: diga o que fazer, a que preço e por quê. Não \
aceite os números abaixo cegamente — confronte-os entre si e com o que você sabe do ativo."""

TASK = """# O que eu preciso de você

1. **Resumo do ativo**: o que é a empresa/fundo, o setor e em que momento o preço está, em linguagem simples.
2. **Leitura dos dados**: avalie as evidências abaixo (tendência, pullback, volume, momentum, \
histórico da estratégia, contexto de mercado). Aponte o que é forte e o que é fraco ou contraditório.
3. **Veredito claro**, começando a resposta por uma destas opções: **COMPRAR AGORA**, **ESPERAR** ou \
**EVITAR**, com o motivo principal em uma frase.
4. **Se for comprar**: faixa de preço razoável de entrada, o preço em que a tese deixa de valer \
(onde eu deveria repensar ou sair), os alvos de saída pela minha estratégia e um tamanho de posição \
prudente (em % do patrimônio).
5. **Se for esperar**: o que precisa acontecer, de forma concreta (preço, média, volume ou candle), \
para virar uma compra — e o que faria você desistir do ativo.
6. **Riscos**: os principais, incluindo o que os números não mostram (resultados, notícias, \
dívida, setor, juros, câmbio). Se tiver acesso a notícias e dados atualizados, consulte-os e diga a \
data; se não tiver, deixe isso claro.
7. **Nota de confiança** de 0 a 10 para o seu veredito, com uma linha de justificativa.

Termine com um resumo de três linhas, para quem não quiser ler o resto."""

METHOD = """# Como o app chegou a este ativo (setup "Garrafa de Gás")

- **Universo**: ativos da B3 com liquidez mínima, analisados todos os dias. Cada tipo (ações, fundos, ETFs, BDRs) tem o seu ranking.
- **Filtros obrigatórios**: MM200 subindo (acima do valor de 20 pregões atrás) e preço, MM20 e MM50 acima da MM200 (tendência de alta de longo prazo); \
recuo (pullback) a partir da máxima de 20 pregões; fechamento entre 0% e {max_dist}% acima de uma das médias (a "média testada"), sem ter fechado mais de {max_below}% abaixo dela durante o recuo.
- **Pressão (0–100%)**: mede o "gás" comprador acumulado no recuo. Soma ponderada de Volume (volume secando no recuo, OBV versus preço), \
Momentum (RSI que respeita a zona de 40, Estocástico que "zerou", divergência oculta de alta), Tendência (MACD e histograma se recuperando) e Estrutura \
(compressão das Bandas de Bollinger e das últimas 5 barras). Descreve o padrão; **não é probabilidade de ganho**.
- **Taxa de acerto**: a mesma estratégia aplicada aos ~10 anos de histórico do próprio ativo (compra na abertura seguinte ao sinal, recompra só em \
um novo recuo a 5%+ das compras abertas, saída pela estratégia configurada). Suavizada para 50% quando há poucas operações. Sem custos, impostos ou proventos.
- **Score Composto** = 60% × Pressão + 40% × Taxa de acerto. O ranking ordena por ele.
- **Estado da garrafa**: Cheia (pressão ≥ 75%), Quase cheia (≥ 60%) ou Enchendo.
- **Indicação do app**: COMPRAR ou AGUARDAR (já comprado nesta região, ou histórico da estratégia negativo no ativo). O app não usa stop loss."""


def _pct(v, d=1, sign=True):
    """Número já em % → "+1,2%"."""
    if v is None:
        return "—"
    return f"{'+' if sign and v > 0 else ''}{fmt_num(v, d)}%"


def _ratio_pct(a, b):
    return (a / b - 1) * 100 if a is not None and b else None


def _millions(v):
    return f"R$ {fmt_num(v / 1e6, 1)} milhões"


def _bool(v):
    return "sim" if v else "não"


def find_row(payload, ticker, mode, strict):
    """(posição no ranking, linha do ranking, ranking do tipo) do ticker na visão escolhida, ou None."""
    side = "strict" if strict else "loose"
    for r in payload["views"][mode]["rankings"]:
        for pos, row in enumerate(r[side]["top"], 1):
            if row["ticker"] == ticker:
                return pos, row, r
    return None


def _technical(analysis, mode):
    """Indicadores tirados do histórico (médias, ATR, máximas, retornos e últimos pregões), na
    mesma base e com o mesmo preço (ajustado nos fundos) da análise. Lista de linhas ou None."""
    try:
        candles = market_data.history(analysis["ticker"])["candles"]
    except Exception:
        log.warning("histórico indisponível para a análise de %s", analysis["ticker"], exc_info=True)
        return None
    if mode == "fechamento":
        candles = closed_candles(candles)
    if (analysis.get("kind") or "acoes") in screener.CONFIG["adjustDividends"]:
        candles = adjusted_candles(candles)
    if len(candles) < 221:
        return None
    ind = compute_indicators(candles)
    i = len(candles) - 1
    c = ind["closes"][i]
    mm200_past = ind["mm200"][i - screener.CONFIG["mm200SlopeLookback"]]
    atr_now = ind["atr14"][i]
    top = max(range(i - 19, i + 1), key=lambda j: (candles[j]["h"], j))
    low = min(range(top, i + 1), key=lambda j: candles[j]["l"])
    year = candles[max(0, i - 251):]
    high52 = max(year, key=lambda x: x["h"])
    low52 = min(year, key=lambda x: x["l"])
    k = ind["stoch"][i]
    lines = ["# Indicadores técnicos (histórico diário)", ""]
    for name in ("mm20", "mm50", "mm200"):
        lines.append(f"- {name.upper()}: {fmt_brl(ind[name][i])} (preço {_pct(_ratio_pct(c, ind[name][i]), 2)} em relação a ela)")
    lines += [
        f"- Inclinação da MM200: {_pct(_ratio_pct(ind['mm200'][i], mm200_past), 2)} em 20 pregões",
        f"- RSI (14): {fmt_num(ind['rsi'][i], 0)} · Estocástico (14): {fmt_num(k, 0) if k is not None else '—'}",
        f"- MACD: {fmt_num(ind['macd'][i], 3)} · histograma: {fmt_num(ind['hist'][i], 3)}",
        f"- ATR (14), oscilação média diária: {fmt_brl(atr_now)} ({fmt_num(atr_now / c * 100, 2)}% do preço)" if atr_now else "- ATR (14): —",
        f"- Topo do recuo (máxima de 20 pregões): {fmt_brl(candles[top]['h'])} em {fmt_date(day_of(candles[top]['date']))}; "
        f"menor mínima desde então: {fmt_brl(candles[low]['l'])} em {fmt_date(day_of(candles[low]['date']))}",
        f"- Máxima de 52 semanas: {fmt_brl(high52['h'])} ({fmt_date(day_of(high52['date']))}; preço {_pct(_ratio_pct(c, high52['h']))} dela)",
        f"- Mínima de 52 semanas: {fmt_brl(low52['l'])} ({fmt_date(day_of(low52['date']))}; preço {_pct(_ratio_pct(c, low52['l']))} dela)",
        "- Retorno no período: " + " · ".join(f"{label} {_pct(_ratio_pct(c, ind['closes'][i - n]))}"
                                             for label, n in RETURN_WINDOWS if i - n >= 0),
        "",
        f"Últimos {RECENT_SESSIONS} pregões:",
        "",
        "| Data | Abertura | Máxima | Mínima | Fechamento | Volume |",
        "|---|---|---|---|---|---|",
    ]
    for x in candles[-RECENT_SESSIONS:]:
        lines.append(f"| {fmt_date(day_of(x['date']))} | {fmt_num(x['o'], 2)} | {fmt_num(x['h'], 2)} | "
                     f"{fmt_num(x['l'], 2)} | {fmt_num(x['c'], 2)} | {fmt_num(x['v'], 0)} |")
    if (analysis.get("kind") or "acoes") in screener.CONFIG["adjustDividends"]:
        lines += ["", "Preços ajustados por proventos (rendimentos mensais), como o app usa para fundos; o último fechamento é o real."]
    return lines


def _bova(mode):
    try:
        b = screener.bova_kpi(mode)
    except Exception:
        log.warning("BOVA11 indisponível para a análise", exc_info=True)
        return None
    position = ("acima das três médias (MM20, MM50 e MM200)" if len(b["above"]) == 3
                else "abaixo das três médias (MM20, MM50 e MM200)" if not b["above"]
                else f"acima de {' e '.join(b['above'])}; abaixo de {' e '.join(b['below'])}")
    return f"- BOVA11 (Ibovespa): {fmt_brl(b['close'])}, {_pct(b['change'])} em 12 meses, {position}"


def build(scan, payload, ticker, mode, strict, lots, strategy):
    """Texto da análise de `ticker` (que precisa estar no ranking da visão escolhida) em Markdown.
    scan: a varredura completa; payload: scan_payload na liquidez escolhida; lots: compras abertas
    do ativo na carteira (ou None); strategy: strategy.describe. ValueError se o ticker não está."""
    found = find_row(payload, ticker, mode, strict)
    if not found:
        raise ValueError(f"{ticker} não está no ranking desta visão.")
    pos, row, ranking = found
    side = "strict" if strict else "loose"
    analysis = next(a for a in screener.analyses_for(scan, mode) if a["ticker"] == ticker)
    advice = screener.advice(row, lots, strategy)
    p, d, setup, bt = analysis["pressure"], analysis["pressure"]["details"], row["setup"], row["bt"]
    other_bt = analysis["backtest"]["loose" if strict else "strict"]
    weights = screener.CONFIG["pressureWeights"].get(analysis.get("kind"), screener.CONFIG["pressureWeights"]["default"])
    kind = analysis.get("kind") or "acoes"
    stamp = local_time(scan["timestamp"] / 1000).strftime("%d/%m/%Y %H:%M")
    pattern = setup["pattern"] or "nenhum (sem candle de rejeição)"

    lines = [ROLE, "", TASK, "", METHOD.format(max_dist=fmt_pct_value(screener.CONFIG["maxDistance"]),
                                               max_below=fmt_pct_value(screener.CONFIG["maxBelowDistance"])), ""]
    lines += [
        f"# Dados do ativo: {ticker}",
        "",
        f"- Nome: {row.get('name') or ticker}" + (f" · setor: {row['sector']}" if row.get("sector") else ""),
        f"- Tipo: {screener.KIND_LABEL.get(kind, kind)}",
        f"- Base da análise: {MODE_LABEL[mode]}, pregão de {fmt_date(day_of(analysis['date']))} (varredura de {stamp}, horário de Brasília)",
        f"- Filtro de candle de rejeição: {'exigido' if strict else 'não exigido'}",
        f"- **Rank**: {pos}º de {len(ranking[side]['top'])} no Top {screener.CONFIG['topN']} "
        f"({ranking[side]['qualified']} ativos do tipo no setup, de {ranking['scanned']} com liquidez)",
        f"- **Score Composto**: {fmt_num(row['score'])}",
        f"- **Preço atual**: {fmt_brl(row['price'])} ({_pct(row['change'], 2)} no dia)",
        f"- Liquidez média (20 pregões): {_millions(analysis['traded'])} por pregão",
        f"- **Média testada**: {setup['ma']['name']} em {fmt_brl(setup['ma']['value'])} (preço {_pct(setup['ma']['dist'] * 100, 2)} acima dela)",
        f"- Profundidade do recuo: {fmt_num(setup['pullback'] * 100)}% desde a máxima de 20 pregões",
        f"- Candle de hoje: {pattern}",
        f"- **Pressão**: {fmt_num(row['pressure'], 0)}% · componentes: Volume {fmt_num(p['volume'], 0)} (peso {fmt_pct_value(weights['volume'])}%), "
        f"Momentum {fmt_num(p['momentum'], 0)} (peso {fmt_pct_value(weights['momentum'])}%), Tendência {fmt_num(p['trend'], 0)} "
        f"(peso {fmt_pct_value(weights['trend'])}%), Estrutura {fmt_num(p['structure'], 0)} (peso {fmt_pct_value(weights['structure'])}%)",
        f"- **Taxa de acerto**: {fmt_num(bt['winRate'], 0)}% suavizada ({bt['wins']} de {bt['trades']} compras encerradas com lucro"
        + (f"; retorno médio {_pct(bt['avgReturn'])} por compra)" if bt["avgReturn"] is not None else ")"),
        f"- Mesmo backtest {'sem' if strict else 'com'} o candle de rejeição: {fmt_num(other_bt['winRate'], 0)}% "
        f"({other_bt['wins']} de {other_bt['trades']}"
        + (f"; retorno médio {_pct(other_bt['avgReturn'])})" if other_bt["avgReturn"] is not None else ")"),
        f"- **Estado da garrafa**: {BOTTLE_LABEL[row['bottle']]}",
        f"- **Indicação do app**: {advice['label']} — {advice['reason'].replace(chr(10), ' ')}",
        "",
        "## Justificativa quantitativa (Volume & Momentum)",
        "",
    ]
    lines += [f"- **{part['label']}**: {'; '.join(part['items'])}" for part in row["justification"]]
    lines += [
        "",
        "## Detalhes da pressão",
        "",
        f"- RSI agora {fmt_num(d['rsi'], 0)}, mínimo no recuo {fmt_num(d['rsiMin'], 0)}; Estocástico agora {fmt_num(d['stoch'], 0)}, mínimo {fmt_num(d['stochMin'], 0)}",
        f"- Divergência oculta de alta: {_bool(d['hiddenDivergence'])}",
        f"- Volume no recuo ÷ volume no avanço: " + (f"{fmt_num(d['dryRatio'], 2)}x" if d["dryRatio"] is not None else "sem recuo ainda")
        + (f" · volume dos dias de alta ÷ média de 60 pregões: {fmt_num(d['advanceRatio'], 2)}x" if d["advanceRatio"] is not None else ""),
        f"- OBV no percentil {fmt_num(d['obvPct'] * 100, 0)} de 60 pregões, preço no percentil {fmt_num(d['closePct'] * 100, 0)}",
        f"- MACD ÷ ATR: {fmt_num(d['macdAtr'], 2)} · histograma recuperou {fmt_num(d['hist'] * 100, 0)}% da força vendedora do recuo",
        f"- Largura das Bandas de Bollinger no percentil {fmt_num(d['bbwPct'] * 100, 0)} de 120 pregões (baixo = comprimida) · "
        f"amplitude dos últimos 5 pregões: {fmt_num(d['range5Atr'], 1)} ATR",
        "",
    ]
    technical = _technical(analysis, mode)
    if technical:
        lines += technical + [""]

    lines += ["# Contexto de mercado", ""]
    if ranking["regime"]:
        lines.append(f"- Regime do tipo: {REGIME_LABEL[ranking['regime']]}; {fmt_num(ranking['breadth'], 0)}% dos ativos do tipo "
                     "em tendência de alta de longo prazo")
    else:
        lines.append("- Regime do tipo: amostra pequena (menos de 20 ativos), sem leitura")
    bova = _bova(mode)
    if bova:
        lines.append(bova)
    lines.append("")

    target_price = row["price"] * (1 + strategy["target"])
    lines += ["# Minha estratégia de saída (configurada no app)", "", f"- {strategy['text']}"]
    if strategy["key"] == "parcial":
        lines.append(f"- Alvo da venda parcial, se eu comprar hoje: {fmt_brl(target_price)} (+{fmt_pct_value(strategy['target'])}%)")
    lines += [f"- Reversão de tendência (venda do restante): {strategy['reversal']}",
              "- Recompra só em outro recuo, a 5% ou mais de cada compra ainda aberta. Não uso stop loss.", ""]

    lines += ["# Minha posição neste ativo", ""]
    if lots:
        for lot in lots:
            lines.append(f"- Compra aberta de {fmt_date(lot['date'])}: {fmt_qty(lot['remaining'])} cotas a {fmt_brl(lot['price'])} "
                         f"({_pct(_ratio_pct(row['price'], lot['price']))} até hoje)")
    else:
        lines.append("- Não tenho compras abertas deste ativo na carteira.")
    lines += ["", "# Limitações dos dados",
              "",
              "- Cotações diárias do Yahoo Finance; podem ter atraso de alguns minutos.",
              "- O backtest não considera custos, impostos nem proventos, e empresas que saíram da bolsa não entram nele.",
              "- O app olha só preço e volume: nada de balanço, notícias ou valuation. Complete com o que você souber."]
    return "\n".join(lines) + "\n"
