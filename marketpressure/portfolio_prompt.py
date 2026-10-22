"""Botão "ANALISAR CARTEIRA" da aba Carteira: o texto (prompt) que o usuário cola numa IA.

Mesmo padrão do "ANALISAR ATIVO" do Screener (ai_prompt.py): o papel (o mesmo analista), a tarefa
(diagnóstico da saúde da carteira e um veredito para cada ativo), como decidir pela estratégia do
app e todos os dados que o app tem: patrimônio e rentabilidade contra CDI e BOVA11, alocação por
classe contra as metas, cada posição (situação do app, compras abertas, indicadores do histórico,
presença no ranking do Screener), renda fixa, proventos e a estratégia de venda de cada classe.
"""

import logging

from . import database as db
from . import market_data, screener
from . import portfolio as pf
from . import portfolio_service as svc
from . import strategy as st
from .ai_prompt import EVENTS, ROLE, _bova, _pct, _ratio_pct
from .core import add_up, compute_indicators, fmt_brl, fmt_date, fmt_num, fmt_qty, local_time, today_iso

log = logging.getLogger(__name__)

MARKET_LABEL = {"b3": "B3", "us": "EUA (US$)", "crypto": "cripto (US$)"}
SETUP_MODE = "fechamento"  # base do setup e do ranking citados: pregões encerrados

TASK = """# O que eu preciso de você

Analise a minha carteira inteira e depois cada ativo dela.

1. **Diagnóstico da carteira**: em que estado ela está, em linguagem simples. Avalie a \
rentabilidade contra o CDI e o BOVA11, a diversificação (quantos ativos, setores, moedas), a \
concentração (peso da maior posição e das três maiores), a alocação por classe contra as minhas \
metas, a renda de proventos e os riscos que se acumulam (setor, país, câmbio, juros). Dê uma \
**nota de saúde de 0 a 10**, com uma linha de justificativa.
2. **Ativo por ativo**, na ordem da tabela de posições: comece cada um por **MANTER**, \
**AUMENTAR**, **REDUZIR** ou **VENDER**, com o motivo principal em uma ou duas frases. Siga \
"Como decidir", abaixo. Em REDUZIR ou VENDER, diga quanto (quantidade ou % da posição); em \
AUMENTAR, a faixa de preço de entrada para hoje.
3. **Candidatos do ranking do Screener**, um a um: todos os ativos com Indicação COMPRAR nas \
tabelas de "Ranking do Screener hoje" (os com AGUARDAR, numa linha só, com o motivo do app). \
Para cada um, **pesquise as notícias antes** (veja "Eventos e fundamentos") e comece por \
**COMPRAR**, **COMPRAR COM POSIÇÃO MENOR**, **NÃO NESTE APORTE** (motivo da carteira: classe na \
meta ou acima, a mesma empresa ou o mesmo setor já pesado na carteira, aporte pequeno demais para \
uma ação) ou **EVITAR** (motivo do ativo: evento de veto, fato grave, tendência quebrando). Em \
seguida: o que a pesquisa encontrou (fato, data e fonte) ou que não conseguiu confirmar, e o \
porquê da decisão em uma ou duas frases, com o que pesou (Score, prioridade, histórico, setor, \
efeito na concentração da carteira). Não pule nenhum candidato com COMPRAR.
4. **Próximos passos**, em ordem de prioridade: o que vender agora (se houver), o que não deve \
receber dinheiro novo (classes e posições na meta ou acima), o **próximo aporte** e se algo \
precisa mudar nas metas ou na estratégia. O próximo aporte vem **numa tabela ordenada por \
prioridade**, com as colunas Prioridade (1º, 2º...), Ativo (código e nome), Indicação do app \
(com a posição no ranking e o Score, ou a Situação, se já for posição), Faixa de entrada hoje, \
Quantidade (ações e valor em R$, pela regra de "Tamanho da posição", e o mínimo de 1 ação) e Por \
quê. Entram as posições para AUMENTAR e os candidatos do item 3 com COMPRAR ou COMPRAR COM POSIÇÃO \
MENOR; os que ficaram de fora (NÃO NESTE APORTE ou EVITAR) vêm no fim da tabela, com "—" na \
prioridade e o motivo em poucas palavras. Se nenhum ativo servir, diga onde deixar o dinheiro até \
o próximo aporte (renda fixa ou classe abaixo da meta).
5. **Riscos** que os números não mostram (resultados, notícias, dívida, governança, setor, juros, \
câmbio), com o resultado da checagem de "Eventos e fundamentos" para as posições e para os ativos \
do ranking que você indicar (o que encontrou, com data e fonte, ou que não conseguiu confirmar).
6. **Nota de confiança** de 0 a 10 para a sua análise, com uma linha de justificativa.

Termine com um resumo de três linhas, para quem não quiser ler o resto."""

DECIDE = """# Como decidir (leia antes dos vereditos)

- **Eu sigo uma estratégia de saída por classe**, descrita em "Minha estratégia de venda": venda \
parcial no alvo, venda parcial quando a alta perde força depois do alvo, ou tudo na reversão de \
tendência (nas duas parciais, o resto sai na reversão). Não uso stop loss. Nos backtests de carteira \
deste app (2017 a 2026), essa disciplina rendeu mais do que vender por medo ou realizar lucro cedo. A \
parcial no alvo, a parcial quando a alta perde força e a venda só na reversão empataram nos testes de \
vários períodos: o vencedor muda com o período, o imposto e o CDI, porque poucos ganhos grandes decidem \
o resultado. A parcial não reduz as perdas (só age nas compras que já subiram); ela troca parte dos \
ganhos grandes por mais compras com lucro. Não vale trocar de regra por causa de um resultado recente.
- **VENDER** quando a regra de saída disparou (Situação "VENDER" na tabela) ou por um motivo \
concreto e grave que os números não mostram (fato fundamentalista, governança, tese quebrada). \
Estar no prejuízo ou ter subido muito não basta.
- **Posição com evento de veto** (OPA, fechamento de capital, fusão ou venda do controle com preço \
definido; veja "Eventos e fundamentos"): não aumente, mesmo com Situação "COMPRAR". Compare o preço \
de tela com o valor da oferta (com a correção prevista, se houver) e o prazo até a liquidação: se o \
mercado já paga perto do valor da oferta, vender agora e reaplicar costuma ser melhor que esperar; \
se paga bem menos, aderir à oferta pode valer. Diga qual dos dois e por quê. Em recuperação judicial \
ou falência, VENDER, salvo motivo forte.
- **Por que não há stop loss**: nos backtests de carteira do app (2017 a 2026, as mesmas compras), \
stops de −15%, −20% e −25% e stop móvel por ATR não aumentaram o lucro de forma consistente. O stop \
móvel subiu o acerto de 53% para 69%, mas reduziu o lucro (R$ 242 mil contra R$ 292 mil); os stops \
fixos variaram sem padrão (de R$ 192 mil a R$ 320 mil). O sistema ganha deixando os vencedores correrem \
(ganho médio de +26% contra perda média de −12%, com acerto perto de 50%): um stop corta justamente as \
altas grandes, que pagam as perdas, e quedas em gap passam por qualquer stop. A proteção vem da saída \
na reversão de tendência, da diversificação e do tamanho de cada compra. Não sugira stop curto; o preço \
"em que a tese deixa de valer" é um ponto para repensar, não uma ordem de stop. Se o risco for alto, \
prefira posição menor ou EVITAR.
- **REDUZIR** quando a venda parcial da estratégia está pendente (Situação "VENDER 50%") ou quando \
uma posição ficou grande demais para o risco dela; diga para qual peso.
- **Venda parcial e força da alta**: na regra "quando a alta perder força", o app só mostra \
"VENDER 50%" depois que a compra atingiu o alvo **e** um fechamento, ainda no alvo ou acima, mostrou \
a alta sem força (menos de 1 ATR acima da MM20 ou RSI abaixo de 65); a venda é na abertura seguinte. \
Enquanto a alta segue forte, a Situação é MANTER com o motivo "fica para a abertura seguinte...": não \
recomende vender antes. Na regra "no alvo", "VENDER 50%" aparece assim que o alvo é tocado; se a \
posição mostrar "Força da alta: forte", pode dizer que a regra por força esperaria (nos testes as duas \
empataram), mas não contrarie a regra escolhida só por isso.
- **AUMENTAR** quando o ativo está num recuo de compra (Situação "COMPRAR" ou "no setup" e, de \
preferência, no ranking do Screener), a tendência é firme e a posição está abaixo da meta. Eu só \
recompro a 5% ou mais de cada compra ainda aberta.
- **MANTER** é o padrão para uma posição em tendência de alta sem sinal de saída: o lucro vem de \
segurar os vencedores. Ações em tendência fraca (MM200 subindo menos de 1% em 20 pregões, retorno \
de 12 meses negativo, preço perto da MM200 ou rendendo menos que o Ibovespa em 6 meses) pedem \
atenção e não recebem compra nova (o app não indica COMPRAR nelas), mas não são venda automática.
- **Ativos internacionais** (BDRs, ETFs e ações do exterior, cripto): nos testes, vender na \
parcial cortava os vencedores cedo, por isso saem só na reversão; o câmbio sozinho não é motivo \
para vender.
- **Rebalancear pelos aportes**: classe acima da meta deixa de receber dinheiro novo em vez de ser \
vendida (vender gera imposto e custo); classe abaixo recebe mais.
- **Ativo novo**: prefira os do ranking do Screener das classes abaixo da meta, com Indicação \
COMPRAR, e entre eles os com prioridade (Top 5 do tipo ou Score ≥ 65, que nos testes \
renderam mais por compra; uma fatia até 1,5x a normal é razoável); o ponto de partida é comprar, e para não comprar é preciso um motivo concreto (tendência \
quebrando, queda forte, choque de notícia, evento de veto ou fato grave de "Eventos e fundamentos"; \
a prioridade não passa por cima de um veto). O histórico do ativo calibra o tamanho da posição, \
nunca veta sozinho.
- **Tamanho da posição**: a compra normal é de 7,5% a 10% do patrimônio; com prioridade, até 1,5x \
(cerca de 15%), nunca mais que isso. Nos testes de robustez do app (2017 a 2026, vários anos de início), fatias \
de 12,5% ou mais renderam bem menos: poucas posições grandes passam a decidir o resultado.
- **Períodos fracos fazem parte**: nos mesmos testes, a estratégia ficou abaixo até do CDI num trecho \
de 3 anos sem tendência (2021–23), ficou atrás do Ibovespa numa alta forte da bolsa brasileira e caiu \
quase 40% na Covid, e voltou a render depois. Não recomende abandonar a estratégia por um período fraco. \
A mistura de classes protege: 60% ações e 40% internacionais ganhou de 100% ações em todos os anos de \
início testados.
- A **pressão** sozinha (garrafa cheia) não antecipou compras melhores nos testes do app: ela vale \
dentro do Score, junto com o histórico do ativo, não como motivo isolado.
- A **Situação** de cada ativo é uma regra fixa do app, só um dado: confronte-a com o resto."""

METHOD = """# Como o app calcula os números

- **Valores em reais**: ativos fora da B3 são convertidos pelo dólar (USDBRL) de cada dia; o preço \
médio e os indicadores técnicos ficam na moeda do ativo.
- **Lucro de uma posição** = valor atual + vendas + proventos recebidos − total comprado. Proventos \
são brutos (JCP tem 15% de imposto; nos EUA, 30% retidos).
- **Rentabilidade da carteira**: por cotas (descontando aportes e resgates), comparada ao CDI e ao \
BOVA11 com os mesmos dias.
- **Situação**: VENDER quando a reversão de tendência disparou (fechamento abaixo da MM20, MM50 e \
MM200, com a MM50 abaixo da MM200); VENDER 50% quando compras atingiram o alvo da venda parcial (na \
regra "quando a alta perder força", só depois de um fechamento sem força ainda no alvo ou acima); \
COMPRAR quando o ativo está no setup de recuo ("Garrafa de Gás": tendência de alta de longo prazo e \
fechamento até 3% acima de uma média testada) longe das compras abertas e, fora dos internacionais, \
sem tendência fraca nem força relativa abaixo do Ibovespa (a mesma regra da Indicação do Screener); \
MANTER nos outros casos.
- **Ranking do Screener**: os 20 melhores de cada tipo de ativo da B3 no setup, pelo Score \
Composto (60% pressão compradora do recuo + 40% taxa de acerto da estratégia no histórico do ativo). \
Histórico = compras com lucro / compras encerradas e o retorno médio por compra no backtest da \
estratégia no próprio ativo (10 anos e últimos 3). Garrafa: cheia (pressão ≥ 75%), quase (≥ 60%) \
ou enchendo. Indicação AGUARDAR = já comprado nesta região ou, fora dos internacionais, tendência \
fraca (MM200 subindo menos de 1% em 20 pregões, retorno de 12 meses negativo ou recuo até a MM200) \
ou ativo rendendo menos que o Ibovespa em 6 meses; COMPRAR com prioridade = Top 5 do tipo ou Score ≥ 65; \
senão COMPRAR."""


def _names():
    """{ticker: linha da tabela universe} (nome, setor e tipo), ou {} se não houver."""
    try:
        return {u["ticker"]: u for u in db.read_table("universe")}
    except Exception:
        log.warning("lista de ativos indisponível para a análise da carteira", exc_info=True)
        return {}


def _rankings():
    """Rankings do Screener por tipo (último fechamento, liquidez padrão, sem candle de rejeição,
    backtest pela regra de cada tipo), ou [] sem varredura. Relê a salva, que a atualização pode ter
    renovado (na versão publicada é ela que vale)."""
    job = screener.scan_job
    try:
        job._reload()
        if not job.scan:
            return []
        return job.payload("padrao", st.screener_kinds(st.load()))["views"][SETUP_MODE]["rankings"]
    except Exception:
        log.warning("ranking indisponível para a análise da carteira", exc_info=True)
        return []


def _ranked(rankings):
    """{ticker: (posição, tipo, linha)} dos ativos nos rankings."""
    return {row["ticker"]: (pos, r["kind"], row) for r in rankings for pos, row in enumerate(r["loose"]["top"], 1)}


def _advice_label(row, kind, lots, settings, rank=None):
    """A Indicação do Screener em poucas palavras: COMPRAR (com prioridade) (com o alerta do
    histórico recente) ou AGUARDAR com o primeiro motivo."""
    group = screener.rule_group({**row, "kind": kind})
    a = screener.advice(row, lots, st.for_group(settings, group), group, rank)
    if a["action"] == "aguardar":
        return "AGUARDAR: " + a["reason"].split("\n")[0]
    return a["label"] + (" com prioridade" if a.get("priority") else "") + (" (histórico recente negativo)" if screener.recent_negative(row["bt"], group) else "")


def _screener_section(portfolio, rankings, settings):
    """Ranking do Screener de hoje só das classes da carteira com meta acima de 0% (a classe de cada
    ativo do ranking pelo grupo da regra de venda: BDRs e ETFs do exterior em Internacionais)."""
    classes = [c for c in portfolio["classes"] if (c.get("target") or 0) > 0 and pf.CLASS_GROUP.get(c["name"])]
    if not classes:
        return []
    lines = ["# Ranking do Screener hoje (só as classes com meta na minha carteira)", ""]
    if not rankings:
        return lines + ["- Sem varredura do Screener disponível agora.", ""]
    lines += ["O Screener cobre só a B3 e deixa de fora os ETFs de cripto: ações dos EUA e cripto fazem parte da "
              "classe Internacionais da carteira (e estão em \"Posições\", se eu tiver), mas não têm ranking. No "
              "ranking de Internacionais entram os BDRs e os ETFs de ações do exterior listados na B3. São os "
              "candidatos do app para novas compras; a Indicação é a regra fixa do Screener (só um dado).", ""]
    lots = svc.held_lots(portfolio, set(_ranked(rankings)))  # compras abertas dos ativos do ranking (B3)
    for c in classes:
        group = pf.CLASS_GROUP[c["name"]]
        rows = [(pos, r, row) for r in rankings for pos, row in enumerate(r["loose"]["top"], 1)
                if screener.rule_group({**row, "kind": r["kind"]}) == group]
        kinds = list(dict.fromkeys(r["kind"] for _, r, _ in rows))
        lines += [f"## {c['name']} (meta {fmt_num(c['target'], 1)}%)", ""]
        for r in rankings:
            if r["kind"] in kinds or (not rows and group == r["kind"]):
                regime = (f"regime {r['regime']}, {fmt_num(r['breadth'], 0)}% dos ativos em tendência de alta"
                          if r["regime"] else "amostra pequena, sem leitura de regime")
                lines.append(f"- {screener.KIND_LABEL.get(r['kind'], r['kind'])}: {r['loose']['qualified']} no setup "
                             f"de {r['scanned']} com liquidez; {regime}")
        if not rows:
            lines += ["- Nenhum ativo desta classe no setup hoje.", ""]
            continue
        lines += ["", "| Pos. | Ativo | Setor | Preço | Score | Pressão | Garrafa | Histórico (10 anos · 3 anos) | Indicação | Na carteira |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for pos, r, row in sorted(rows, key=lambda x: -x[2]["score"]):
            bt = row["bt"]
            history = ((f"{bt['wins']}/{bt['trades']}, {_pct(bt.get('avgReturn'))} · "
                        + (f"{bt['recentWins']}/{bt['recentTrades']}, {_pct(bt.get('recentReturn'))}" if bt.get("recentTrades") else "—"))
                       if bt.get("trades") else "sem compras encerradas")
            place = f"{pos}º" + (f" {screener.KIND_LABEL.get(r['kind'], r['kind'])}" if len(kinds) > 1 else "")
            name = (row.get("name") or row["ticker"]).replace("|", "/")
            lines.append(f"| {place} | {row['ticker']} — {name} | {row.get('sector') or '—'} | {fmt_brl(row['price'])} | "
                         f"{fmt_num(row['score'])} | {fmt_num(row['pressure'], 0)}% | {row['bottle']} | {history} | "
                         f"{_advice_label(row, r['kind'], lots.get(row['ticker']), settings, pos)} | "
                         f"{'sim' if row['ticker'] in lots else 'não'} |")
        lines.append("")
    return lines


def _money(v, currency):
    return fmt_brl(v) if currency == "R$" else f"{currency} {fmt_num(v, 2)}"


def _history(ticker, market):
    """Candles do ativo na moeda dele (com o pregão em andamento, se houver), ou [] sem histórico."""
    try:
        return market_data.history(ticker, market)["candles"]
    except Exception:
        log.warning("histórico indisponível para a análise de %s", ticker, exc_info=True)
        return []


def _technical(candles, market, currency):
    """Indicadores dos pregões fechados, na moeda do ativo, em uma lista de linhas, ou None sem
    histórico suficiente."""
    candles = pf.closed_for(candles, market)
    if len(candles) < 221:
        return None
    ind = compute_indicators(candles)
    i = len(candles) - 1
    c = ind["closes"][i]
    year = candles[max(0, i - 251):]
    high52 = max(x["h"] for x in year)
    low52 = min(x["l"] for x in year)
    mas = " · ".join(f"{name.upper()} {_money(ind[name][i], currency)} ({_pct(_ratio_pct(c, ind[name][i]))})"
                     for name in ("mm20", "mm50", "mm200"))
    momentum = [f"inclinação da MM200 {_pct(_ratio_pct(ind['mm200'][i], ind['mm200'][i - 20]), 2)} em 20 pregões",
                f"RSI (14) {fmt_num(ind['rsi'][i], 0)}"]
    if ind["atr14"][i]:
        momentum.append(f"ATR (14) {fmt_num(ind['atr14'][i] / c * 100, 2)}% do preço")
    returns = " · ".join(f"{label} {_pct(_ratio_pct(c, ind['closes'][i - n]))}"
                         for label, n in (("3 meses", 63), ("12 meses", 252)) if i - n >= 0)
    return [
        f"  - Médias (preço em relação a cada uma): {mas}",
        "  - " + " · ".join(momentum),
        f"  - Retorno: {returns} · preço {_pct(_ratio_pct(c, high52))} da máxima de 52 semanas e "
        f"{_pct(_ratio_pct(c, low52))} da mínima",
    ]


def _position(p, info, ranked, rule, transactions):
    """Bloco de uma posição aberta."""
    t, currency = p["ticker"], p["currency"]
    name = info.get("name") or t
    head = f"## {t} — {name}" if name != t else f"## {t}"
    lines = [head, ""]
    details = [f"classe {p['cls']}", f"mercado {MARKET_LABEL.get(p['market'], p['market'])}"]
    if info.get("sector"):
        details.append(f"setor {info['sector']}")
    lines.append("- " + " · ".join(details))
    avg_native = p["avgPrice"] / (p["fx"] or 1) if currency != "R$" else p["avgPrice"]
    lines += [
        f"- Quantidade {fmt_qty(p['quantity'])} · preço atual {_money(p['priceNative'], currency)} em {fmt_date(p['priceDate'])}"
        + (f" ({_pct(p['dayChange'], 2)} no dia)" if p.get("dayChange") is not None else "")
        + f" · preço médio {_money(avg_native, currency)}" + (" (aprox., pelo dólar de hoje)" if currency != "R$" else ""),
        f"- Valor {fmt_brl(p['value'])} ({fmt_num(p['weight'], 1)}% da carteira) · lucro {fmt_brl(p['profit'])} "
        f"({_pct(p['returnPct'])}), com {fmt_brl(p['dividends'])} de proventos e {fmt_brl(p['realized'])} realizados em vendas",
    ]
    if p.get("status"):
        lines.append(f"- **Situação do app**: {p['status']} — {(p.get('statusReason') or '').replace(chr(10), ' ')}")
        force = p.get("statusStrength")
        if force:
            lines.append(f"- **Força da alta**: {'forte' if force['strong'] else 'perdeu força'} (RSI {fmt_num(force['rsi'], 0)}, "
                         f"fechamento {fmt_num(force['atrAboveMm20'], 1)} ATR em relação à MM20 de "
                         f"{_money(force['mm20'], currency)})")
    elif not p.get("hasQuote"):
        lines.append("- Situação do app: sem cotação (valores pelo último preço lançado)")
    setup = (p.get("setup") or {}).get(SETUP_MODE) or {}
    if setup.get("ok"):
        lines.append(f"- No setup de recuo agora, testando a {setup['ma']['name']}")
    if t in ranked:
        pos, kind, row = ranked[t]
        lines.append(f"- **No ranking do Screener**: {pos}º de {screener.KIND_LABEL.get(kind, kind)}, Score "
                     f"{fmt_num(row['score'])}, pressão {fmt_num(row['pressure'], 0)}%")
    candles = _history(t, p["market"]) if p.get("hasQuote") else []
    lots = pf.open_lots(transactions, t, candles, rule["target"], pf.partial_fraction(rule))
    if lots:
        lines.append("- Compras abertas: " + "; ".join(
            f"{fmt_date(lot['date'])}: {fmt_qty(lot['remaining'])} a {_money(lot['price'], currency)} "
            f"({_pct(_ratio_pct(p['priceNative'], lot['price']))})" for lot in lots))
    technical = _technical(candles, p["market"], currency)
    if technical:
        lines.append("- Indicadores técnicos (diário):")
        lines += technical
    return lines + [""]


def build(portfolio, analysis, settings):
    """Texto da análise da carteira em Markdown. analysis: portfolio_service.full_analysis;
    settings: strategy.load (a regra de venda de cada classe)."""
    k = analysis["kpis"]
    total = k["total"]
    names = _names()
    rankings = _rankings()
    ranked = _ranked(rankings)
    classes = {t: a.get("class") for t, a in portfolio["assets"].items()}
    rule_for = st.asset_rules(settings, classes)
    txs = portfolio.get("transactions") or []
    stamp = local_time(analysis["loadedAt"] / 1000).strftime("%d/%m/%Y %H:%M")

    lines = [ROLE, "", TASK, "", DECIDE, "", EVENTS, "", METHOD, ""]
    lines += [
        f"# Minha carteira: {portfolio['name']}",
        "",
        f"- Cotações de {stamp} (horário de Brasília)" + (", com o pregão em andamento" if analysis.get("live") else ""),
        f"- **Patrimônio**: {fmt_brl(total)} · investido líquido {fmt_brl(k['invested'])}",
        f"- **Lucro total**: {fmt_brl(k['profit'])} ({_pct(k['profitPct'])} sobre o investido) = valorização não "
        f"realizada {fmt_brl(k['unrealized'])} + lucro realizado {fmt_brl(k['realized'])} + proventos {fmt_brl(k['dividendsTotal'])}",
    ]
    if k.get("day"):
        lines.append(f"- Último pregão ({fmt_date(k['day']['date'])}): {fmt_brl(k['day']['value'])} ({_pct(k['day']['pct'], 2)})")
    since, last12 = k.get("sinceStart"), k.get("last12")
    for label, w in (("Desde o início", since), ("Últimos 12 meses", last12 if last12 != since else None)):
        if w:
            lines.append(f"- **{label}** (desde {fmt_date(w['from'])}): carteira {_pct(w['portfolio'])} · "
                         f"CDI {_pct(w['cdi'])} · BOVA11 {_pct(w['bova'])}")
    open_ = analysis["open"]
    if open_ and total:
        top = sorted(open_ + analysis["fixed"], key=lambda h: -h["value"])
        concentration = f"- Concentração: maior posição {top[0].get('ticker') or top[0].get('name')} com {fmt_num(top[0]['weight'], 1)}%"
        if len(top) > 3:
            concentration += f"; as três maiores somam {fmt_num(add_up(h['weight'] for h in top[:3]), 1)}%"
        lines.append(concentration)
    lines.append(f"- {len(open_)} ativo(s) em carteira e {len(analysis['fixed'])} título(s) de renda fixa")
    if analysis.get("failed"):
        lines.append(f"- Dados indisponíveis agora: {'; '.join(analysis['failed'])} (ativos sem cotação usam o último "
                     "preço lançado; sem o CDI, a comparação com ele fica em 0%)")
    lines.append("")

    lines += ["# Alocação por classe", "", "| Classe | Valor | % atual | Meta | Diferença |", "|---|---|---|---|---|"]
    for a in analysis["allocation"]:
        if not a["target"] and not a["value"]:
            continue
        lines.append(f"| {a['cls']} | {fmt_brl(a['value'])} | {fmt_num(a['pct'], 1)}% | {fmt_num(a['target'], 1)}% | "
                     f"{_pct(a['diff'])} ({'falta' if a['toTarget'] > 0 else 'sobra'} {fmt_brl(abs(a['toTarget']))}) |")
    if pf.targets_off(analysis["targetSum"]):
        lines.append(f"\nAs metas somam {fmt_num(analysis['targetSum'], 1)}%, não 100%.")
    lines.append("")

    lines += ["# Minha estratégia de venda (configurada no app, por classe)", ""]
    for c in pf.CLASSES:
        if c["group"]:
            rule = st.describe_one(st.for_group(settings, c["group"]))
            lines.append(f"- **{c['name']}**: {rule['text']}")
    lines += [f"- Reversão de tendência: {st.REVERSAL_TEXT}",
              "- Recompra só em outro recuo, a 5% ou mais de cada compra ainda aberta. Não uso stop loss.", ""]

    lines += ["# Posições (da maior para a menor)", ""]
    for p in open_:
        lines += _position(p, names.get(p["ticker"]) or {}, ranked, rule_for(p["ticker"], p["market"]), txs)

    lines += _screener_section(portfolio, rankings, settings)

    if analysis["fixed"]:
        lines += ["# Renda fixa", ""]
        for f in analysis["fixed"]:
            update = f"última atualização do valor em {fmt_date(f['lastUpdate'])}" if f.get("lastUpdate") else "valor nunca atualizado (= aportes − resgates)"
            lines.append(f"- {f['name']} ({f.get('cls') or pf.FIXED_INCOME_CLASS}): {fmt_brl(f['value'])} ({fmt_num(f['weight'], 1)}% da carteira), "
                         f"aportado {fmt_brl(f['contributed'])}, resgatado {fmt_brl(f['redeemed'])}, lucro {fmt_brl(f['profit'])} "
                         f"({_pct(f['returnPct'])}); {update}")
        lines.append("")

    year_ago = pf.add_months(today_iso(), -12)
    last_year = [d for d in analysis["dividends"] if d["exDate"] >= year_ago]
    lines += ["# Proventos", "",
              f"- Recebidos desde o início: {fmt_brl(k['dividendsTotal'])}; nos últimos 12 meses: "
              f"{fmt_brl(add_up(d['total'] for d in last_year))}"
              + (f" ({fmt_num(add_up(d['total'] for d in last_year) / total * 100, 2)}% do patrimônio atual)" if total else "")]
    by_ticker = sorted(analysis["divByTicker"].items(), key=lambda kv: -kv[1])[:5]
    if by_ticker:
        lines.append("- Maiores pagadores desde o início: " + " · ".join(f"{t} {fmt_brl(v)}" for t, v in by_ticker))
    lines.append("")

    bova = _bova(SETUP_MODE)
    lines += ["# Contexto de mercado", ""]
    lines.append(bova or "- BOVA11 indisponível agora.")
    lines += ["", "# Limitações dos dados", "",
              "- Cotações diárias do Yahoo Finance; podem ter atraso de alguns minutos.",
              "- A renda fixa vale o último valor que eu lancei; entre atualizações, não rende.",
              "- O app olha só preço, volume e os meus lançamentos: nada de balanço, notícias ou valuation. "
              "Complete com o que você souber."]
    return "\n".join(lines) + "\n"


def prompt(portfolio):
    """{name, text} para a rota: a análise (a última, se a carteira não mudou) e o texto."""
    analysis = svc.full_analysis(portfolio, reuse=True)
    return {"name": portfolio["name"], "text": build(portfolio, analysis, st.load())}
