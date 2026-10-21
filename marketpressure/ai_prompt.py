"""Botão "ANALISAR ATIVO" do ranking do Screener: o texto (prompt) que o usuário cola numa IA.

Monta, para um ativo do ranking, as instruções do papel (analista experiente que fala de forma
simples), a tarefa (comprar agora ou esperar, e por quê) e todos os dados que o app tem dele: a
linha do ranking, os componentes da pressão, o backtest, os indicadores do histórico, o regime do
tipo, o BOVA11, a estratégia de saída salva e as compras abertas da carteira.
"""

import logging

from . import market_data, screener
from . import strategy as st
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

Você investe para o médio e longo prazo: as posições duram meses, saem na reversão de tendência e não \
têm stop curto. Sabe que perder uma alta inteira custa muito mais do que pagar um pouco mais caro na \
entrada, e que a entrada perfeita quase nunca aparece.

Fale com a clareza de um bom professor: qualquer pessoa leiga precisa entender sua recomendação. \
Use frases curtas, evite jargão e, quando um termo técnico for inevitável, explique-o em poucas \
palavras (por exemplo: "MM200, a média dos preços dos últimos 200 pregões, que mostra a tendência \
de longo prazo"). Seja direto, honesto e concreto: diga o que fazer, a que preço e por quê. Não \
aceite os números abaixo cegamente — confronte-os entre si e com o que você sabe do ativo."""

TASK = """# O que eu preciso de você

1. **Resumo do ativo**: o que é a empresa/fundo, o setor e em que momento o preço está, em linguagem simples.
2. **Eventos e notícias**: o resultado da checagem de "Eventos e fundamentos" (o que encontrou, \
com data e fonte, ou que não conseguiu confirmar).
3. **Leitura dos dados**: avalie as evidências abaixo (tendência, pullback, volume, momentum, \
histórico da estratégia, contexto de mercado). Aponte o que é forte e o que é fraco ou contraditório.
4. **Veredito claro**, começando a resposta por uma destas opções: **COMPRAR AGORA**, **COMPRAR COM \
POSIÇÃO MENOR** ou **EVITAR**, com o motivo principal em uma frase. Siga "Como decidir", abaixo.
5. **Se for comprar**: faixa de preço de entrada para hoje (não um preço ideal que talvez não volte), \
o preço em que a tese deixa de valer (onde eu deveria repensar), os alvos de saída pela minha \
estratégia e o tamanho da posição em % do patrimônio (na posição menor, diga quanto menor e por quê).
6. **Se for evitar**: o motivo concreto (da lista de "Como decidir" ou um fato que os números não \
mostram) e o que faria o ativo voltar a ser uma compra.
7. **Riscos**: os principais, incluindo o que os números não mostram (resultados, notícias, \
dívida, setor, juros, câmbio). Se tiver acesso a notícias e dados atualizados, consulte-os e diga a \
data; se não tiver, deixe isso claro.
8. **Nota de confiança** de 0 a 10 para o seu veredito, com uma linha de justificativa.

Termine com um resumo de três linhas, para quem não quiser ler o resto."""

DECIDE = """# Como decidir (leia antes do veredito)

- O ativo já passou pelos filtros do app: tendência de alta de longo prazo e recuo até uma média. \
**O ponto de partida é comprar**; para não comprar é preciso um motivo concreto. A checagem de "Eventos e fundamentos" (abaixo) vem antes: um evento de veto vale \
mais que qualquer número do app.
- **Esperar uma entrada melhor tem custo.** Num backtest de carteira deste app (2017 a 2026, cerca de \
2 mil decisões de um analista), esperar uma confirmação (candle de alta, rompimento, volume) antes de \
comprar reduziu o retorno médio de cada compra em cerca de 3 pontos percentuais, e boa parte dos \
ativos que não deram a confirmação subiu mesmo assim. Comprar tudo o que o analista não mandou evitar \
rendeu cerca de 25% a mais no período do que esperar as confirmações.
- Por isso não há a opção "esperar": se houver dúvida, **compre com posição menor** e diga quanto. \
Use **EVITAR** só com um motivo concreto, como:
  - tendência fraca nas ações: MM200 subindo menos de 1% em 20 pregões, retorno de 12 meses negativo, recuo até a \
MM200 ou ação rendendo menos que o Ibovespa em 6 meses (nos testes do app, essas compras renderam menos nas duas \
metades de 2017–2026, e seguir essa regra deu mais lucro à carteira em 6 de 8 anos de início; nos internacionais isso não valeu);
  - tendência quebrando: MM20 bem abaixo da MM50 com MACD afundando;
  - queda forte até a média ("faca caindo": 15% ou mais em poucos pregões, RSI perto de 30) em vez de um recuo ordenado;
  - choque de notícia: gap ou queda forte com volume muito acima da média;
  - volatilidade extrema para uma estratégia sem stop;
  - um evento ou fato grave que os números do app não mostram (veja "Eventos e fundamentos": OPA, \
fechamento de capital, fusão, recuperação judicial, fraude, calote).
- **Prioridade**: no Top 5 do ranking do tipo ou com Score ≥ 65, as compras renderam mais em média nas duas metades \
de 2017–2026; nesses casos uma fatia maior (até 1,5x a normal) é razoável.
- **Tamanho da posição**: a compra normal é de 7,5% a 10% do patrimônio; com prioridade, até 1,5x \
(cerca de 15%), nunca mais que isso. Nos testes de robustez do app (2017 a 2026, vários anos de início), fatias \
de 12,5% ou mais renderam bem menos: poucas posições grandes passam a decidir o resultado. Na posição menor, use \
metade da normal ou menos.
- A pressão sozinha (garrafa cheia) não antecipou compras melhores nos testes: ela vale dentro do Score, junto com o \
histórico do ativo, e não como motivo isolado para comprar mais ou evitar.
- Candle vermelho no dia do sinal, RSI ainda caindo, mercado misto ou dólar oscilando são normais num \
recuo e não bastam para evitar. Regime defensivo também não: nos testes, as poucas ações em alta num \
mercado defensivo renderam mais que a média.
- **Por que não há stop loss**: nos backtests de carteira do app (2017 a 2026, as mesmas compras), \
stops de −15%, −20% e −25% e stop móvel por ATR não aumentaram o lucro de forma consistente. O stop \
móvel subiu o acerto de 53% para 69%, mas reduziu o lucro (R$ 242 mil contra R$ 292 mil); os stops \
fixos variaram sem padrão (de R$ 192 mil a R$ 320 mil). O sistema ganha deixando os vencedores correrem \
(ganho médio de +26% contra perda média de −12%, com acerto perto de 50%): um stop corta justamente as \
altas grandes, que pagam as perdas, e quedas em gap passam por qualquer stop. A proteção vem da saída \
na reversão de tendência, da diversificação e do tamanho de cada compra. Não sugira stop curto; o preço \
"em que a tese deixa de valer" é um ponto para repensar, não uma ordem de stop. Se o risco for alto, \
prefira posição menor ou EVITAR.
- **Ativos internacionais (BDRs e ETFs de ações do exterior)**: nos mesmos testes, até os sinais que o \
analista mandou evitar renderam em média cerca de +10% por compra. Pense duas vezes antes de vetar um \
internacional; o câmbio sozinho não é motivo.
- **Histórico do próprio ativo**: o backtest de 10 anos quase não separou compras boas de ruins. O dos \
últimos 3 anos separou nas ações (recente negativo: −1,5% por compra; positivo: +4,1%); nos BDRs, \
nenhum dos dois antecipou nada. Mesmo assim, deixar de comprar ações pelo histórico negativo (de 10 \
ou de 3 anos) reduziu o lucro da carteira nos testes. Use o histórico como um dado entre outros, \
para calibrar o tamanho da posição, nunca como veto sozinho."""

EVENTS = """# Eventos e fundamentos (antes de qualquer veredito)

O app só lê preço e volume: não sabe de OPA, fusão, recuperação judicial nem notícias. Essa \
checagem é sua e tem peso **complementar**: ela não cria compras nem derruba um bom sinal por \
opinião, mas barra os casos em que o gráfico engana.

- **Pesquise antes de decidir**, para cada ativo que você for indicar para compra (e, na carteira, \
para cada posição): notícias e fatos relevantes dos últimos 6 meses, pelo código e pelo nome da \
empresa ou do fundo. Se a primeira busca não trouxer nada útil, tente de novo com outros termos (nome \
do controlador, "OPA", "fato relevante"). **Busca vazia não é "sem notícias"**: diga que não \
conseguiu confirmar e reduza a nota de confiança. Se não tiver acesso a notícias, diga isso.
- **Veto (não comprar, mesmo com prioridade, Top 5 ou Score alto)**: eventos que travam ou distorcem \
o preço, porque a alta que a estratégia busca não pode acontecer:
  - OPA (oferta pública de aquisição) anunciada ou pedida, fechamento de capital, saída do Novo \
Mercado ou cancelamento de registro;
  - venda do controle, incorporação ou fusão com preço ou relação de troca já definidos;
  - recuperação judicial ou extrajudicial, falência, suspensão de negociação, liquidação do fundo.
- Sinal desses eventos nos dados: o preço quase parado, colado num valor (o da oferta), com ATR muito \
baixo para o ativo e candles minúsculos. Um "recuo perfeito" até a média com a volatilidade sumindo \
pede essa checagem. O veto dura até o evento ser cancelado ou concluído.
- **Fato grave (posição menor, ou não comprar se a tese quebrou)**: fraude ou balanço refeito, calote \
ou renegociação de dívida, investigação séria, perda do principal contrato ou cliente, corte brusco \
de dividendos por falta de caixa, emissão de ações muito diluidora. Diga qual fato, a data e a fonte.
- **Opinião fundamentalista não veta**: múltiplos caros, setor fora de moda, resultado fraco já \
divulgado, juros ou política. O preço já reflete o que é público, e a tendência mostra o que o \
mercado está fazendo com isso. No máximo, ajuste o tamanho da posição e cite o ponto nos riscos.
- Seja coerente: um evento de veto vale do mesmo jeito em todas as análises; se ele deixou de \
existir (oferta cancelada, recuperação encerrada), diga isso com a data."""

METHOD = """# Como o app chegou a este ativo (setup "Garrafa de Gás")

- **Universo**: ativos da B3 com liquidez mínima, analisados todos os dias. Cada tipo (ações, fundos, ETFs, BDRs) tem o seu ranking.
- **Filtros obrigatórios**: MM200 subindo (acima do valor de 20 pregões atrás) e preço, MM20 e MM50 acima da MM200 (tendência de alta de longo prazo); \
recuo (pullback) a partir da máxima de 20 pregões; fechamento entre 0% e {max_dist}% acima de uma das médias (a "média testada"), sem ter fechado mais de {max_below}% abaixo dela durante o recuo.
- **Pressão (0–100%)**: mede o "gás" comprador acumulado no recuo. Soma ponderada de Volume (volume secando no recuo, OBV versus preço), \
Momentum (RSI que respeita a zona de 40, Estocástico que "zerou", divergência oculta de alta), Tendência (MACD e histograma se recuperando) e Estrutura \
(compressão das Bandas de Bollinger e das últimas 5 barras). Descreve o padrão; **não é probabilidade de ganho**.
- **Histórico da estratégia (backtest)**: a mesma estratégia aplicada aos ~10 anos de histórico do próprio ativo (compra na abertura seguinte ao sinal, \
recompra só em um novo recuo a 5%+ das compras abertas, saída pela regra configurada para o tipo do ativo). Mostra as compras encerradas \
(taxa de acerto suavizada para 50% quando há poucas e o retorno médio), as ainda abertas (marcadas pelo preço de hoje; num ativo em alta \
costumam ser as melhores) e as encerradas nos últimos 3 anos. Sem custos, impostos ou proventos. Com a venda parcial, acerto alto com \
retorno médio baixo (ou o contrário) é normal: olhe os dois juntos.
- **Score Composto** = 60% × Pressão + 40% × Taxa de acerto. O ranking ordena por ele.
- **Estado da garrafa**: Cheia (pressão ≥ 75%), Quase cheia (≥ 60%) ou Enchendo.
- **Indicação automática do app**: uma regra fixa, não uma recomendação. AGUARDAR quando já tenho compra nesta região (a \
estratégia recompra apenas em outro recuo) ou, fora dos internacionais, quando a tendência está fraca (MM200 subindo menos de 1% \
em 20 pregões, retorno de 12 meses negativo ou recuo até a MM200) ou o ativo rendeu menos que o Ibovespa em 6 meses; COMPRAR \
com prioridade (o motivo começa por "Prioridade") no Top 5 do tipo ou com Score ≥ 65; senão COMPRAR, com um alerta quando o backtest dos últimos 3 anos é negativo. O app não usa stop loss."""


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


def _history_line(bt):
    """O backtest do ativo em uma linha: encerradas, abertas e recentes."""
    parts = [f"{bt['wins']} de {bt['trades']} compras encerradas com lucro em ~10 anos (acerto {fmt_num(bt['winRate'], 0)}% suavizado"
             + (f", retorno médio {_pct(bt['avgReturn'])} por compra)" if bt.get("avgReturn") is not None else ")")]
    if bt.get("open"):
        parts.append(f"{bt['open']} compra(s) ainda aberta(s), {_pct(bt['openReturn'])} em média pelo preço de hoje")
    if bt.get("recentTrades"):
        parts.append(f"últimos 3 anos: {bt['recentWins']} de {bt['recentTrades']} com lucro, retorno médio {_pct(bt['recentReturn'])}")
    elif "recentTrades" in bt:
        parts.append("nenhuma compra encerrada nos últimos 3 anos")
    return "; ".join(parts)


def build(scan, payload, ticker, mode, strict, lots, settings):
    """Texto da análise de `ticker` (que precisa estar no ranking da visão escolhida) em Markdown.
    scan: a varredura completa; payload: scan_payload na liquidez e nas regras por tipo escolhidas;
    lots: compras abertas do ativo na carteira (ou None); settings: strategy.load (a regra de saída
    vem do tipo do ativo). ValueError se o ticker não está."""
    found = find_row(payload, ticker, mode, strict)
    if not found:
        raise ValueError(f"{ticker} não está no ranking desta visão.")
    pos, row, ranking = found
    side = "strict" if strict else "loose"
    analysis = next(a for a in screener.analyses_for(scan, mode) if a["ticker"] == ticker)
    kind = analysis.get("kind") or "acoes"
    group = st.group_of(kind, "b3", row.get("name") or "")
    strategy = st.describe_one(st.for_group(settings, group))
    kinds = st.screener_kinds(settings)
    advice = screener.advice(row, lots, strategy, group, pos)
    p, d, setup, bt = analysis["pressure"], analysis["pressure"]["details"], row["setup"], row["bt"]
    other_bt = analysis["backtest"][screener.bt_key(not strict, group, kinds)]
    weights = screener.CONFIG["pressureWeights"].get(analysis.get("kind"), screener.CONFIG["pressureWeights"]["default"])
    peers = [a["bt"]["avgReturn"] for a in ranking[side]["top"] if a["bt"].get("avgReturn") is not None]
    stamp = local_time(scan["timestamp"] / 1000).strftime("%d/%m/%Y %H:%M")
    pattern = setup["pattern"] or "nenhum (sem candle de rejeição)"

    lines = [ROLE, "", TASK, "", DECIDE, "", EVENTS, "", METHOD.format(max_dist=fmt_pct_value(screener.CONFIG["maxDistance"]),
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
        f"- **Histórico da estratégia neste ativo** ({strategy['label'].lower()}): {_history_line(bt)}",
        "- Referência: " + (f"retorno médio por compra no histórico dos {len(peers)} ativos do Top deste tipo: "
                            f"{_pct(sum(peers) / len(peers))}" if peers else "sem histórico encerrado no Top deste tipo"),
        f"- Mesmo backtest {'sem' if strict else 'com'} o candle de rejeição: {_history_line(other_bt)}",
        f"- **Estado da garrafa**: {BOTTLE_LABEL[row['bottle']]}",
        f"- Indicação automática do app (regra fixa, só um dado): {advice['label']} — {advice['reason'].replace(chr(10), ' ')}",
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
    lines += [f"# Minha estratégia de saída para {st.GROUPS[group]} (configurada no app)", "", f"- {strategy['text']}"]
    if strategy["key"] == "parcial":
        lines.append(f"- Alvo da venda parcial, se eu comprar hoje: {fmt_brl(target_price)} (+{fmt_pct_value(strategy['target'])}%)")
    elif strategy["key"] == "forca":
        lines.append(f"- Alvo que libera a venda parcial, se eu comprar hoje: {fmt_brl(target_price)} "
                     f"(+{fmt_pct_value(strategy['target'])}%). Depois dele, a parte sai na abertura seguinte ao primeiro "
                     f"fechamento sem força ({st.WEAK_TEXT}) ainda em {fmt_brl(target_price)} ou acima; enquanto a alta "
                     "segue forte, mantenho tudo. Nos backtests de carteira do app (2017 a 2026), essa regra, a parcial no "
                     "alvo e a venda só na reversão empataram (o vencedor muda com o período, o imposto e o CDI).")
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
