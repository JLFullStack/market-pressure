"""Botão "ANALISAR BACKTEST" da aba Backtest: o texto (prompt) que o usuário cola numa IA.

Um texto por tipo de resultado da página: um ativo (screener.backtest_view), carteira
(portfolio_backtest.run) e robustez (portfolio_backtest.robustness). Todos pedem o mesmo papel
(analista quantitativo e investidor experiente, cético com backtests), a mesma tarefa (nota e
classificação do resultado, qualidade das regras, vieses e melhorias testáveis no app), as regras
da estratégia, o que o app já testou (para a IA não sugerir de novo o que já foi descartado) e
todos os números do resultado.

O resultado vem do cache de `remember`/`recall` (a página acabou de calculá-lo) ou é calculado de
novo pela rota.
"""

import json
import threading

from . import screener as sc
from . import strategy as st
from .core import add_up, fmt_brl, fmt_date, fmt_num, today_iso

CACHE_SIZE = 6          # resultados guardados (os últimos de cada tipo)
MAX_TRADES = 250        # compras listadas no prompt da carteira
MAX_LOTS = 200          # lotes listados no prompt de um ativo

_cache = {}
_cache_lock = threading.Lock()


def cache_key(kind, params):
    """Chave do cache: o tipo, o dia (os históricos ganham um pregão por dia) e os parâmetros já
    validados (antes de rodar o backtest, que acrescenta campos a eles)."""
    return f"{kind}:{today_iso()}:" + json.dumps(params, sort_keys=True, default=str)


def remember(key, result):
    with _cache_lock:
        _cache.pop(key, None)
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.pop(next(iter(_cache)))


def recall(key):
    with _cache_lock:
        return _cache.get(key)


ROLE = """# Seu papel

Você é um analista quantitativo e investidor renomado, com mais de 50 anos de experiência no \
mercado de ações brasileiro e internacional. Já viu centenas de estratégias que eram lindas no \
backtest e quebraram na vida real, e outras, simples, que funcionaram por décadas. Conhece os \
vícios clássicos de um backtest: excesso de ajuste ao passado (overfitting), viés de \
sobrevivência, olhar o futuro sem querer, custos e impostos ignorados, poucos ganhos grandes \
decidindo tudo e resultados que dependem do período escolhido.

Você é justo: não descarta uma estratégia só por desconfiança, nem se empolga com um número bonito. \
Julga pelos números, confrontando-os entre si.

Fale com a clareza de um bom professor: qualquer pessoa leiga precisa entender. Frases curtas, sem \
jargão; quando um termo técnico for inevitável, explique-o em poucas palavras (por exemplo: "taxa \
interna de retorno, o rendimento anual equivalente, levando em conta quando cada aporte entrou")."""

TASK = """# O que eu preciso de você

1. **O que foi testado**, em linguagem simples: a estratégia, o período e as condições.
2. **Qualidade do resultado**: comece por uma destas classificações — **ROBUSTO**, **PROMISSOR**, \
**FRÁGIL** ou **INCONCLUSIVO** — e dê uma nota de 0 a 10. Justifique com os números: retorno contra \
as comparações, maiores quedas, consistência ano a ano (ou entre períodos), quantidade de compras, \
concentração do lucro em poucos ativos ou poucas compras e o que pode ter sido sorte.
3. **Qualidade das regras**: o que nas regras parece funcionar e o que parece fraco ou \
contraditório, sempre apoiado nos números deste resultado. {rules_focus}
4. **Riscos e vieses** que podem fazer o resultado real ficar pior que o do backtest, dizendo o \
tamanho provável de cada um (pequeno, médio, grande).
5. **Melhorias**: até 5, em ordem de prioridade. Para cada uma: o que mudar, por que, como testar \
na própria aba Backtest (qual campo ou modo) e que resultado confirmaria que ela é melhor. Prefira \
o que dá para testar hoje no app; se a melhoria exigir uma função nova, diga isso. Não repita o \
que já foi testado (lista abaixo), a não ser que traga um ângulo realmente novo. Se o resultado \
não pede mudança, diga isso — é uma resposta válida.
6. **Próximo teste** que você rodaria primeiro, com os campos exatos.
7. **Nota de confiança** de 0 a 10 para a sua análise, com uma linha de justificativa.

Termine com um resumo de três linhas, para quem não quiser ler o resto."""

RULES_FOCUS = {
    "ativo": "Compare a venda parcial (no alvo ou quando a alta perde força, conforme a regra) com a \
regra B sozinha e diga se as compras que a Indicação \
do Screener mandaria aguardar renderam menos que as outras neste ativo. Lembre que um ativo só é \
uma amostra pequena.",
    "carteira": "Compare a saída escolhida com a regra B sozinha, o tamanho da fatia, o limite de \
ativos, as metas por classe (se houver) e o efeito de seguir a Indicação do Screener.",
    "robustez": "O foco aqui é a consistência: a estratégia ganha na maioria dos períodos ou só \
em alguns? A venda parcial ganha da regra B sozinha de forma consistente?",
}

TESTED = """# O que o app já testou (backtests de carteira de 2017 a 2026, ações e internacionais)

- **Esperar confirmação** (candle de alta, rompimento, volume) antes de comprar reduziu o retorno \
médio por compra em cerca de 3 pontos percentuais.
- **Stop loss** fixo (−15%, −20%, −25%) e **stop móvel** por ATR não aumentaram o lucro de forma \
consistente: cortam justamente as altas grandes que pagam as perdas.
- **Saídas**: nas ações, vender 50% em +10% e o resto na regra B ganhou da regra B sozinha por pouco \
(cerca de 1 ponto percentual ao ano), com o sinal mudando conforme o período. **Vender os 50% depois \
do alvo, na abertura seguinte ao primeiro fechamento em que a alta perde força** (menos de 1 ATR acima \
da MM20 ou RSI abaixo de 65, com o fechamento ainda no alvo ou acima) foi a melhor saída das ações: \
veja os números nos testes de robustez abaixo; é a regra "Venda parcial quando a alta perder força" \
das Configurações e o padrão das ações. Confirmar a perda de força por 2, 3 ou 5 pregões seguidos não \
melhorou (medianas parecidas e quedas maiores); segurar até o fechamento perder a MM20, saídas por \
tamanho da alta e saídas em duas etapas também não. Nos internacionais, a venda parcial vendia cedo \
os grandes vencedores: a regra é só a reversão. Fundos e ETFs Brasil ainda não foram testados com a \
parcial por força.
- **Filtro de entrada (Indicação do Screener)**: compras em tendência fraca ou com o ativo mais \
fraco que o BOVA11 em 6 meses renderam menos por compra nas duas metades do período; as do Top 5 \
ou com Score ≥ 65 renderam mais. No lucro total da carteira, porém, nenhum conjunto de regras de \
entrada ganhou de sorteios aleatórios: poucos ganhos grandes decidem o resultado.
- **Histórico do próprio ativo**: deixar de comprar ativos com backtest negativo (10 ou 3 anos) \
reduziu o lucro da carteira.
- **Pressão sozinha** (garrafa cheia) não antecipou compras melhores; ela vale dentro do Score.
- **Candle de rejeição obrigatório** não ajudou.
- **Small caps** (menos de R$ 17 milhões negociados por dia) renderam cerca de +0,2% por compra; \
fatia menor ou exigir prioridade para elas não melhorou a carteira.
- **Liquidez mínima**: R$ 5 milhões foi melhor em 2017–2026 e R$ 2 milhões foi melhor desde 2022 — \
o melhor piso muda com o período.
- **Testes de robustez (outubro de 2026; carteira 60% ações / 40% internacionais, fatia 10%, Indicação ligada, saída das \
Configurações, caixa no CDI)**:
  - Cada ano até o fim (8 inícios): retorno ao ano de 19,5% a 24,6% (mediana 21,3%); ganhou do BOVA11 em 6 de 8 e do CDI em 8 de 8.
  - Janelas de 3 anos (7 janelas): ganhou do BOVA11 em 5 de 7, com folga grande quando a bolsa brasileira ficou parada \
(2019–21, 2020–22, 2022–24); ficou para trás num mercado sem tendência (2021–23, abaixo até do CDI: 7,1% contra 12,0% \
ao ano) e numa alta forte do Brasil (2023–25). Quedas de quase 40% aconteceram dentro de uma janela (Covid).
  - **Fatia por compra**: platô entre 7,5% e 10% (medianas de 21,8% e 21,3%); de 12,5% para cima o resultado despenca \
(15%: 16,8%; 20%: 15,2%), porque poucas posições — ainda mais com 1,5x nas compras com prioridade — passam a decidir tudo.
  - **Classes**: 60/40 ganhou de 100% ações em 8 de 8 inícios; só ações perdeu do BOVA11 desde 2021 e caiu até −53,5%. \
Só internacionais foi estável, mas com poucas compras e ajudado pelo dólar.
  - **Indicação do Screener ligada x desligada**: ligada ganhou em 6 de 8 inícios (mediana de cerca de +4 pontos ao ano) \
e teve queda máxima menor; perdeu pouco nos inícios de alta forte (2023 e 2024). Ainda não testada em janelas.
  - **Venda parcial quando a alta perde força** (outubro de 2026; 60/40, fatia 10%, Indicação ligada, sem CDI): no período \
inteiro, sem imposto, 22,1% ao ano contra 20,3% da parcial no alvo e 22,9% da regra B sozinha (queda máxima de −40% contra \
−46% da regra B). Com imposto e venda na abertura seguinte: 20,7% ao ano líquido contra 16,9% (parcial no alvo) e 18,7% \
(regra B sozinha); cada ano até o fim: ganhou da parcial no alvo em 7 de 8 inícios e da regra B sozinha em 8 de 8 \
(medianas 20,8%, 17,7% e 18,9%); janelas de 3 anos: 5 de 7 contra as duas. Perdeu nos inícios curtos e recentes (2024; \
2022–24 e 2023–25 em algumas comparações). Só 4 das 80 vendas na abertura saíram abaixo do alvo.
  - **Venda parcial no alvo x regra B sozinha**: empate — a parcial venceu 32 de 63 comparações somando todos os testes. Nas \
janelas de 3 anos, a parcial protege em mercado ruim ou sem tendência e a regra B ganha muito em alta forte; a mediana \
da diferença é perto de zero.
  - **Régua justa e imposto de renda** (cada ano até o fim, com imposto): contra os índices na proporção das metas \
(60% BOVA11 + 40% IVVB11) ganhou em 7 de 8 inícios, com vantagem mediana de +5,0 pontos ao ano (sem imposto, 6 de 8 e \
+4,4); retorno ao ano de 18,2% a 22,9% (mediana 20,1%). Ganhou do IVVB11 em 8 de 8: a vantagem não vem só do dólar. O \
imposto custou cerca de 1,5 ponto ao ano à estratégia (a isenção de vendas de ações até R$ 20 mil por mês e a compensação \
de prejuízos ajudam; o efeito diminui com um patrimônio maior) e de 1,7 a 3,2 aos índices, que pagam tudo na venda final. \
Com imposto, a venda parcial venceu 5 de 8; somando todos os testes, 37 de 71 (empate). Ainda não rodado: janelas de 3 \
anos com imposto e régua justa. Nesse teste o CDI do caixa ainda não pagava imposto (hoje paga).
  - Ao comparar períodos, use medianas e contagens de vitórias, nunca um início isolado: pagar imposto (ou qualquer \
mudança pequena) altera o caixa, que altera as compras seguintes, e outra sequência de vencedores entra na carteira.
- **Lição geral**: mudanças de configuração geram diferenças grandes de lucro que se invertem de \
um período para outro. Uma melhoria só é confiável se ganhar em vários inícios ou janelas (botão \
"Testar robustez"), não só no período inteiro."""


def _method():
    c = sc.CONFIG
    pct = lambda v: fmt_num(v * 100, 0)
    return f"""# Regras da estratégia (setup "Garrafa de Gás")

- **Sinal de compra**: MM200 subindo (acima do valor de 20 pregões atrás); preço, MM20 e MM50 acima \
da MM200; recuo de pelo menos {pct(c['minPullback'])}% a partir da máxima de 20 pregões; fechamento entre \
0% e {pct(c['maxDistance'])}% acima de uma das médias (MM20, MM50 ou MM200), sem ter fechado mais de \
{pct(c['maxBelowDistance'])}% abaixo dela durante o recuo. Opcional: exigir candle de rejeição (doji, martelo, corpo estreito).
- **Compra** na abertura do pregão seguinte ao sinal. Recompra no mesmo ativo só num novo recuo, \
a {pct(c['reentryDistance'])}% ou mais do preço de cada compra ainda aberta.
- **Saída** (sem stop loss): "regra B" = fechamento abaixo de MM20, MM50 e MM200 com a MM50 abaixo \
da MM200 (tendência de baixa formada): vende o que restar na abertura seguinte. Com venda parcial \
"no alvo", cada compra vende uma parte quando a máxima de um pregão atinge o alvo sobre o preço dela; \
"quando a alta perder força", depois de atingir o alvo, vende a parte na abertura seguinte ao primeiro \
fechamento com menos de 1 ATR acima da MM20 ou RSI abaixo de 65 que ainda esteja no alvo ou acima.
- **Score Composto** = 60% × pressão compradora do recuo (volume, momentum, tendência e estrutura, \
0–100) + 40% × taxa de acerto da estratégia no histórico do ativo até aquele dia. O ranking de cada \
tipo (ações, fundos listados, ETFs, BDRs) ordena por ele e mostra o Top {c['topN']}.
- **Indicação do Screener** (opcional no backtest de carteira): fora dos internacionais, pula as \
compras em tendência fraca (MM200 subindo menos de {pct(c['weakSlope'])}% em 20 pregões, retorno de 12 \
meses negativo ou recuo até a MM200) ou com o ativo rendendo menos que o BOVA11 em 6 meses; compra \
{fmt_num(c['prioritySize'], 1)}x a fatia normal no Top {c['priorityRank']} do tipo ou com Score ≥ {c['priorityScore']}.
- **Liquidez**: um ativo só entra no ranking do dia se a média negociada nos 20 pregões anteriores \
passa do piso escolhido (padrão: R$ 5 milhões; fundos R$ 3 milhões).
- **Dados**: Yahoo Finance, pregões fechados, cerca de 10 anos (as médias precisam de 220 pregões \
antes da primeira compra). Fundos listados usam o preço ajustado por proventos para os sinais."""


def _pct(v, d=1, sign=True):
    if v is None:
        return "—"
    return f"{'+' if sign and v > 0 else ''}{fmt_num(v, d)}%"


def _brl(v):
    return "—" if v is None else fmt_brl(v)


def _date(d):
    return fmt_date(d) if d else "—"


def _rule_text(target, fraction, timing="alvo"):
    if fraction <= 0:
        return "só a regra B (vende tudo na reversão)"
    when = (f"depois de +{fmt_num(target, 1)}%, quando a alta perde força" if timing == "forca"
            else f"em +{fmt_num(target, 1)}%")
    if fraction >= 100:
        return f"vende 100% {when}"
    return f"vende {fmt_num(fraction, 0)}% {when} e o resto na regra B"


def _exit_lines(p):
    """A saída usada no backtest de carteira: a de cada grupo (Configurações) ou a manual."""
    if p.get("exit") == "config" and p.get("rules"):
        return ["- Saída: a de cada grupo nas Configurações do app:"] + [
            f"  - {st.GROUPS.get(g, g)}: {_rule_text(r['target'], r['fraction'], r.get('timing', 'alvo'))}"
            for g, r in p["rules"].items()]
    return [f"- Saída: {_rule_text(p['target'], p['fraction'], p.get('timing', 'alvo'))} (a mesma para todos os ativos)"]


def _params_lines(p, universe):
    liquidity = "padrão (R$ 5 milhões; fundos R$ 3 milhões)" if p["liquidity"] == "padrao" else f"R$ {p['liquidity']} milhões"
    lines = [
        "# Configuração do teste", "",
        f"- Ativos: {universe['label']}" + (f" ({', '.join(p['tickers'])})" if p.get("tickers") else "")
        + f" — {universe['candidates']} candidatos, {universe['ranked']} passaram pelo ranking em algum dia, "
          f"{universe['illiquid']} nunca tiveram a liquidez mínima",
        f"- Top do ranking por tipo: {p['topN']} · liquidez mínima: {liquidity}",
        f"- Capital inicial: {fmt_brl(p['initial'])} · aporte mensal: {fmt_brl(p['monthly'])}",
        f"- Fatia por compra: {fmt_num(p['position'], 1)}% do patrimônio do dia",
    ]
    if p.get("classes"):
        lines.append("- Alocação por metas de classe (cada classe compra só até a sua parte do patrimônio): "
                     + ", ".join(f"{st.GROUPS.get(g, g)} {fmt_num(v, 0)}%" for g, v in p["classes"].items()))
    else:
        lines.append(f"- Máximo de ativos diferentes ao mesmo tempo: {p['maxAssets']}")
    lines += _exit_lines(p)
    lines += [
        f"- Seguir a Indicação do Screener: {'sim' if p['advice'] else 'não'}",
        f"- Candle de rejeição obrigatório: {'sim' if p['strict'] else 'não'}",
        f"- Custo por operação: {fmt_num(p['cost'], 2)}% em cada compra e venda · caixa parado rende CDI: {'sim' if p['cdi'] else 'não'}",
        ("- Imposto de renda: sim — 15% sobre o ganho líquido do mês em ações, ETFs e BDRs (ações isentas com vendas de até "
         "R$ 20 mil no mês), 20% em fundos imobiliários, prejuízo compensado nos meses seguintes, pago no mês seguinte. "
         "O CDI do caixa rende 15% a menos (imposto da renda fixa). Os índices e o CDI de comparação pagam 15% sobre o ganho só "
         "se vendidos no fim. Sem imposto sobre JCP e proventos de BDRs (os dados não os separam)."
         if p.get("tax") else "- Imposto de renda: não descontado (opção desligada)."),
        "- Proventos entram no caixa na data-ex. Empresas que saíram da bolsa não estão nos dados (viés de sobrevivência).",
    ]
    return lines


def _options():
    """O que dá para mudar na aba Backtest, para a IA sugerir só testes que o usuário consegue rodar."""
    liquidity = ", ".join("padrão (R$ 5 mi; fundos R$ 3 mi)" if k == "padrao" else f"R$ {k} mi" for k in sc.LIQUIDITY)
    return f"""# O que dá para testar na aba Backtest (sugira só testes com estes campos)

- **Modo**: "Um ativo" (a estratégia lote a lote num ativo da B3 ou dos EUA) ou "Carteira" (um caixa que segue o ranking).
- **Ranking** (Carteira): ações, fundos listados, ETFs, BDRs, todos os tipos ou uma lista de códigos. **Top do ranking** por \
tipo: 1 a 100 (padrão 20). **Liquidez mínima**: {liquidity}.
- **Dinheiro**: capital inicial, aporte mensal, **fatia por compra** (% do patrimônio; padrão 10%) e custo por operação (%). \
No modo Um ativo, o valor de cada compra.
- **Alocação**: "Máx. de ativos" (limite de ativos diferentes) ou "Metas por classe" (Ações, Fundos imobiliários, ETFs \
Brasil e Internacionais, em %).
- **Saída**: "Conforme as Configurações" (a regra de cada classe na aba Configurações: venda parcial no alvo + regra B, venda \
parcial quando a alta perder força + regra B, ou só a regra B) ou "Informada aqui" (alvo, parte vendida e **momento da venda \
parcial** — no alvo ou quando a alta perder força — para todos; parte 0 = só a regra B).
- **Período**: datas De e Até (o histórico começa em 2017).
- **Opções**: seguir a Indicação do Screener, caixa rende CDI, exigir candle de rejeição, descontar imposto de renda.
- **Testar robustez**: a mesma configuração em vários períodos — "cada ano até o fim" ou janelas de 2, 3 ou 5 anos — \
com o placar contra BOVA11, IVVB11, os índices na proporção das metas, CDI e a regra B sozinha.
- **Comparações em todo teste de carteira**: BOVA11, IVVB11, CDI e, com metas por classe, os índices na mesma proporção \
(ações, fundos e ETFs Brasil no BOVA11; internacionais no IVVB11).
- **Não dá para mudar na tela**: as regras do setup (médias, recuo, distância), os pesos do Score, o multiplicador de \
1,5x das compras com prioridade e a regra B. Uma melhoria nelas exige mudar o app; diga isso quando sugerir."""


def _guide(kind):
    """Papel, tarefa, regras da estratégia, o que dá para testar e o que já foi testado: o começo de todo prompt."""
    return [ROLE, "", TASK.format(rules_focus=RULES_FOCUS[kind]), "", _method(), "", _options(), "", TESTED, ""]


# ---------------------------------------------------------------- Um ativo

def build_single(view, exit_mode):
    """Prompt do modo "Um ativo" (view = screener.backtest_view)."""
    s, b, cur = view["stats"], view["onlyB"], view["currency"]
    money = (lambda v: "—" if v is None else f"US$ {fmt_num(v, 2)}") if cur == "USD" else _brl
    rule = view["rule"]
    lines = [f"# Análise de backtest — {view['ticker']} ({view['name']})", "",
             "Analise o resultado de um backtest da minha estratégia de swing trade num único ativo.", ""] + _guide("ativo") + [
             "# Configuração do teste", "",
             f"- Ativo: {view['ticker']} — {view['name']} · tipo: {view['kindLabel']} · moeda: {cur}",
             f"- Período: {view['period']} ({s['periodSessions']} pregões, ≈ {s['periodDuration']})",
             f"- Saída ({'regra do tipo do ativo nas Configurações' if exit_mode == 'config' else 'alvo e parte escolhidos à mão'}): {rule['text']}",
             f"- Candle de rejeição obrigatório: {'sim' if view['strict'] else 'não'}",
             f"- Cada sinal compra {money(s['amount'])}; todos os sinais são seguidos (sem limite de capital).",
             "- Sem custos, impostos nem proventos." + (" Preços ajustados por proventos (fundo listado)." if view["adjusted"] else "")]
    if view.get("note"):
        lines.append(f"- Observação: {view['note']}")
    lines += ["", "# Resultado", "",
              f"- Compras: {s['lots']} ({s['closed']} encerradas, {s['open']} abertas) · saídas pela regra B: {s['exits']} · "
              f"vendas parciais: {s['partialHits']}",
              f"- Lucro no período: {money(s['profit'])} (realizado {money(s['realized'])}, em aberto {money(s['unrealized'])}) "
              f"sobre {money(s['invested'])} aplicados ({_pct(s['returnOnInvested'])})",
              f"- Capital máximo investido ao mesmo tempo: {money(s['maxCapital'])} → retorno sobre ele: {_pct(s['returnOnMaxCapital'])}",
              f"- Taxa de acerto (encerradas): {_pct(s['winRate'], 0, False)} (suavizada para poucas compras: {_pct(s['winRateSmoothed'], 0, False)})",
              f"- Resultado por compra encerrada: média {_pct(s['avgReturn'])}, mediana {_pct(s['medianReturn'])}, "
              f"melhor {_pct(s['best'])}, pior {_pct(s['worst'])}",
              f"- Duração média: {s['avgDuration'] or '—'} · alta máxima média depois da compra: {_pct(s['avgMaxGain'])} · "
              f"devolvido do topo até a saída: {_pct(s['avgGiveback'], 1, False)}",
              f"- Comprar e segurar desde a primeira compra ({_date(s['buyHoldFrom'])}): {_pct(s['buyHold'])}"]
    adv = s.get("advice")
    if adv:
        lines.append(f"- Indicação do Screener: {adv['wait']} compra(s) seriam AGUARDAR (média {_pct(adv['waitReturn'])}); "
                     f"{adv['buy']} seriam COMPRAR (média {_pct(adv['buyReturn'])})")
    if b:
        lines.append(f"- Só a regra B, mesmas condições: lucro {money(b['profit'])}, {b['lots']} compras, acerto {_pct(b['winRate'], 0, False)}, "
                     f"média {_pct(b['avgReturn'])}, capital máximo {money(b['maxCapital'])} (retorno sobre ele {_pct(b['returnOnMaxCapital'])})")
    if view.get("compare"):
        lines.append(f"- {view['compare']}")
    lots = view["lots"]
    lines += ["", f"# Compras (lotes){' — as ' + str(MAX_LOTS) + ' mais recentes' if len(lots) > MAX_LOTS else ''}", "",
              "| # | Compra | Preço | Venda parcial | Saída | Resultado | Pregões | Alta máx. | Devolvido | Situação | Indicação |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in lots[-MAX_LOTS:]:
        point = lambda p: f"{_date(p['date'])} {_pct(p['return'])}" if p else "—"
        lines.append(f"| {r['n']} | {_date(r['entry']['date'])} | {fmt_num(r['entry']['price'], 2)} | {point(r['partial'])} | {point(r['exit'])} | "
                     f"{_pct(r['return'])} | {r['days']} | {_pct(r['maxGain'])} | {_pct(r['giveback'], 1, False)} | {r['status']} | "
                     f"{'AGUARDAR: ' + '; '.join(r['wait']) if r['wait'] else 'COMPRAR'} |")
    return "\n".join(lines)


# ---------------------------------------------------------------- Carteira

METRIC_ROWS = (
    ("Patrimônio final", "final", "brl"), ("Total aportado", "contributed", "brl"), ("Lucro", "profit", "brl"),
    ("Lucro sobre o aportado", "profitPct", "pct"), ("Retorno ao ano (TIR)", "irr", "pct"),
    ("Imposto de renda pago no período", "taxPaid", "brl"), ("Imposto se vendesse tudo no último dia", "taxDue", "brl"),
    ("Lucro líquido (vendendo tudo no fim)", "profitNet", "brl"), ("Retorno ao ano líquido", "irrNet", "pct"),
    ("Maior queda (pela cota)", "maxDrawdown", "pct"), ("Compras", "trades", "int"), ("Encerradas", "closed", "int"),
    ("Taxa de acerto", "winRate", "pct0"), ("Resultado médio por compra", "avgReturn", "pct"),
    ("Mediana por compra", "medianReturn", "pct"), ("Melhor compra", "best", "pct"), ("Pior compra", "worst", "pct"),
    ("Vendas parciais", "partials", "int"), ("Duração média", "avgDuration", "text"),
    ("Devolvido do topo até a saída", "avgGiveback", "pctu"), ("Alta máxima média", "avgMaxGain", "pct"),
    ("Ativos negociados", "assets", "int"), ("Proventos recebidos", "dividends", "brl"), ("Caixa médio parado", "avgCash", "pct0"),
)


def _metric(v, kind):
    if v is None:
        return "—"
    return {"brl": fmt_brl, "pct": _pct, "pct0": lambda x: _pct(x, 0, False), "pctu": lambda x: _pct(x, 1, False), "int": lambda x: fmt_num(x, 0),
            "text": str}[kind](v)


def build_portfolio(result):
    """Prompt do modo "Carteira" (result = portfolio_backtest.run)."""
    p, sts = result["params"], result["strategies"]
    lines = [f"# Análise de backtest de carteira — {result['universe']['label']}", "",
             "Analise o resultado de um backtest da minha estratégia aplicada a uma carteira que segue o ranking do "
             "Screener dia a dia, com um único caixa: o que é vendido volta para o caixa e paga as próximas compras.", ""] + _guide("carteira")
    lines += _params_lines(p, result["universe"])
    lines.append(f"- Período: {result['period']} ({result['sessions']} pregões, ≈ {result['duration']})")
    if result.get("note"):
        lines.append(f"- Observação: {result['note']}")
    for w in result.get("warnings") or []:
        lines.append(f"- Aviso: {w}")
    if result.get("adviceSkipped") is not None:
        lines.append(f"- Seguindo a Indicação: {result['adviceSkipped']} sinal(is) do ranking em AGUARDAR não viraram compra "
                     "(conta cada ativo em cada dia em que apareceu).")

    lines += ["", "# Resultado por estratégia (mesmos aportes, nas mesmas datas)", "",
              "| | " + " | ".join(s["label"] for s in sts) + " |", "|---|" + "---|" * len(sts)]
    for label, key, kind in METRIC_ROWS:
        values = [s["metrics"].get(key) for s in sts]
        if all(v is None for v in values):
            continue
        lines.append(f"| {label} | " + " | ".join(_metric(v, kind) for v in values) + " |")
    if result.get("compare"):
        lines += ["", result["compare"]]

    lines += ["", "# Ano a ano (lucro em R$ = patrimônio no fim − no início − aportes do ano; rentabilidade pela cota)", "",
              "| Ano | " + " | ".join(s["label"] for s in sts) + " |", "|---|" + "---|" * len(sts)]
    for k, y in enumerate(sts[0]["years"]):
        partial = f" ({_date(y['from'])[:5]} a {_date(y['to'])[:5]})" if k in (0, len(sts[0]["years"]) - 1) else ""
        lines.append(f"| {y['year']}{partial} | " + " | ".join(
            f"{fmt_brl(s['years'][k]['profit'])} ({_pct(s['years'][k]['return'])})" for s in sts) + " |")

    assets, trades = result["assets"], result["trades"]
    total = add_up(a["profit"] for a in assets)
    by_profit = sorted(assets, key=lambda a: -a["profit"])
    lines += ["", f"# De onde veio o lucro ({sts[0]['label']})", ""]
    if total > 0:
        for n in (1, 3, 5, 10):
            if len(by_profit) > n:
                share = add_up(a["profit"] for a in by_profit[:n]) / total * 100
                who = "O ativo que mais lucrou" if n == 1 else f"Os {n} ativos que mais lucraram"
                lines.append(f"- {who}: {fmt_num(share, 0)}% do lucro das operações")
    returns = sorted(t["return"] for t in trades)
    if returns:
        top = sorted(trades, key=lambda t: -t["profit"])
        top_n = max(1, len(top) // 10)
        if total > 0:
            lines.append(f"- As {top_n} compras que mais lucraram (10%): {fmt_num(add_up(t['profit'] for t in top[:top_n]) / total * 100, 0)}% do lucro")
        lines.append(f"- Compras com resultado acima de +50%: {sum(1 for r in returns if r > 50)}; abaixo de −20%: {sum(1 for r in returns if r < -20)}")
    asset = lambda a: f"{a['ticker']} {fmt_brl(a['profit'])} ({a['trades']} compra{'s' if a['trades'] > 1 else ''})"
    losers = [a for a in sorted(assets, key=lambda a: a["profit"])[:5] if a["profit"] < 0]
    lines += ["", "Ativos que mais lucraram: " + ("; ".join(asset(a) for a in by_profit[:10]) or "—"),
              "Ativos que mais perderam: " + ("; ".join(asset(a) for a in losers) or "nenhum ativo deu prejuízo")]

    shown = trades[-MAX_TRADES:]
    lines += ["", f"# Compras ({sts[0]['label']}){' — as ' + str(MAX_TRADES) + ' mais recentes de ' + str(len(trades)) if len(trades) > MAX_TRADES else ''}", "",
              "| # | Ativo | Rank | Score | Compra | Investido | Parcial | Saída | Resultado | Lucro | Proventos | Pregões | Alta máx. | Situação |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in shown:
        point = lambda q: f"{_date(q['date'])} {_pct(q['return'])}" if q else "—"
        lines.append(f"| {t['n']} | {t['ticker']} | {t['rank'] or '—'} | {fmt_num(t['score'], 0) if t['score'] is not None else '—'} | "
                     f"{_date(t['entry']['date'])} | {fmt_brl(t['invested'])} | {point(t['partial'])} | {point(t['exit'])} | "
                     f"{_pct(t['return'])} | {fmt_brl(t['profit'])} | {fmt_brl(t['dividends'])} | {t['days']} | {_pct(t['maxGain'])} | {t['status']} |")
    return "\n".join(lines)


# ---------------------------------------------------------------- Robustez

def build_robustness(result):
    """Prompt do teste de robustez (result = portfolio_backtest.robustness)."""
    p, rows, c = result["params"], result["rows"], result["cards"]
    unit = f"janelas de {result['window']} anos" if result["window"] else f"cada ano de início até {_date(result['end'])}"
    lines = [f"# Análise do teste de robustez — {result['universe']['label']} ({unit})", "",
             "Analise o resultado de um teste de robustez: a mesma carteira (que segue o ranking do Screener dia a dia, "
             "com um único caixa) simulada em vários períodos, para ver se o resultado depende do ponto de partida. "
             + ("Cada janela tem o mesmo tamanho e pega um tipo de mercado diferente."
                if result["window"] else "Todos os inícios terminam no mesmo dia, então os últimos anos pesam em todos."),
             ""] + _guide("robustez")
    lines += _params_lines(p, result["universe"])
    lines.append(f"- Estratégia comparada: {result['rule']}" + (" (com a regra B sozinha ao lado)" if result["hasPartial"] else ""))
    for w in result.get("warnings") or []:
        lines.append(f"- Aviso: {w}")
    ref = result["ref"]
    lines += ["", "# Resumo do app", "",
              f"- Régua principal: {ref['label']}. Veredito do app (regra fixa: ganhar da régua em todos / em 2/3 dos períodos): "
              f"{result['verdict']}"]
    lines += [f"- {t}" for t in result["summary"]]
    lines.append(f"- Lucro sobre o aportado: mediana de {_pct(c['profitPct']['median'], 0)}; maior queda mediana: {_pct(c['drawdown']['median'])}")

    cols = result["columns"]
    lines += ["", "# Cada período (lucro em R$ e retorno ao ano de cada linha; todas recebem os mesmos aportes"
              + ("; com imposto, os valores líquidos vendendo tudo no fim" if result.get("tax") else "") + ")", "",
              "| Período | Aportado | " + " | ".join(col["label"] for col in cols)
              + f" | Compras | Caixa médio | Parcial − regra B | Diferença ao ano vs {ref['short']} | Maior queda |",
              "|---|---|" + "---|" * len(cols) + "---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['label']} ({_date(r['from'])} a {_date(r['to'])}) | {fmt_brl(r['rule']['contributed'])} | "
                     + " | ".join(f"{fmt_brl(r[col['key']]['profit'])} ({_pct(r[col['key']]['irr'])} a.a.)" for col in cols)
                     + f" | {r['rule']['trades']} | {fmt_num(r['rule']['avgCash'], 0)}% | {_brl(r['diffOnlyB']) if r['diffOnlyB'] is not None else '—'} | "
                       f"{'—' if r['excess'] is None else fmt_num(r['excess'], 1) + ' p.p.'} | {_pct(r['rule']['maxDrawdown'])} |")
    return "\n".join(lines)


TITLES = {"ativo": "Análise do backtest de {ticker}", "carteira": "Análise do backtest de carteira",
          "robustez": "Análise do teste de robustez"}


def single(view, exit_mode):
    return {"title": TITLES["ativo"].format(ticker=view["ticker"]), "text": build_single(view, exit_mode)}


def portfolio(result):
    return {"title": TITLES["carteira"], "text": build_portfolio(result)}


def robustness(result):
    return {"title": TITLES["robustez"], "text": build_robustness(result)}


def run_portfolio(params, fn):
    """Resultado do backtest de carteira (fn = pb.run ou pb.robustness) pelos parâmetros, do cache
    quando a página acabou de calculá-lo."""
    key = cache_key(fn.__name__, params)
    result = recall(key)
    if result is None:
        result = fn(params)
        remember(key, result)
    return result

