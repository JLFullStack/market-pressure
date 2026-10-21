// Aba Backtest. Depende de core.js (apiGet e formatadores) e do Lightweight Charts (CDN),
// carregados antes deste script.
//
// O backtest inteiro é calculado no Python:
//   GET  /api/backtest?ticker=&market=&target=&fraction=&strict=&from=&to=&amount=  (screener.backtest_view)
//        -> um ativo: candles, médias, lotes, marcadores, lucro acumulado e resumo
//   POST /api/backtest/carteira  (portfolio_backtest.run)
//        -> carteira com um único caixa: lucro de cada estratégia, BOVA11 e CDI, comparação e operações
//   POST /api/backtest/carteira/robustez  (portfolio_backtest.robustness)
//        -> a mesma carteira a partir de cada ano de início: uma linha por início e o resumo
//   GET  /api/backtest/prompt, POST /api/backtest/carteira/prompt, POST /api/backtest/carteira/robustez/prompt
//        -> botão "ANALISAR BACKTEST": o texto para a IA, com os parâmetros do resultado mostrado
// Aqui só se desenham os gráficos e as tabelas.

// Cores (validadas sobre o fundo #161b22 do painel). Médias como no TradingView do usuário:
// MM20 laranja, MM50 azul, MM200 roxa.
const COLORS = {
    up: '#26a69a', down: '#ef5350', close: '#c9d1d9',
    mm20: '#e3a33b', mm50: '#3987e5', mm200: '#a371f7',
    buy: '#3fb950', partial: '#e3b341', exit: '#f85149',
    profit: '#58a6ff', profitB: '#e3a33b', zero: '#8b949e',
    grid: '#21262d', border: '#30363d', text: '#c9d1d9', panel: '#161b22'
};
const MA_LABEL = { mm20: 'MM20', mm50: 'MM50', mm200: 'MM200' };
// Marcador de cada evento de lote (o rótulo, ex. "C3" ou "V1–4", vem pronto do servidor).
const MARKER = {
    buy: { position: 'belowBar', shape: 'arrowUp', color: COLORS.buy },
    partial: { position: 'aboveBar', shape: 'circle', color: COLORS.partial },
    exit: { position: 'aboveBar', shape: 'arrowDown', color: COLORS.exit }
};
const CHART_KEY = 'marketpressure_backtest_grafico'; // "candles" | "line"
const PAGE_SIZE_KEY = 'marketpressure_backtest_por_pagina'; // linhas por página das tabelas de lotes e de operações
// Linhas do gráfico do modo Carteira, por estratégia (chave do servidor).
const PF_COLORS = { rule: '#58a6ff', onlyB: '#e3a33b', bova: '#3fb950', ivvb: '#a371f7', mix: '#39c5cf', cdi: '#8b949e' };
// Linhas da tabela de comparação do modo Carteira: [rótulo, ajuda, campo, formato].
const PF_ROWS = [
    ['Patrimônio final', 'Valor da carteira no último dia: caixa + ativos pelo fechamento.', 'final', 'brl'],
    ['Total aportado', 'Capital inicial + aportes mensais do período.', 'contributed', 'brl'],
    ['Lucro', 'Patrimônio final − total aportado: quanto você ganhou de fato no período.', 'profit', 'brlSigned'],
    ['Lucro sobre o aportado', 'Lucro ÷ total aportado.', 'profitPct', 'pct'],
    ['Retorno ao ano', 'Taxa interna de retorno: o rendimento anual equivalente, levando em conta quando cada aporte entrou.', 'irr', 'pct'],
    ['Imposto de renda pago', 'Imposto sobre as vendas, pago no mês seguinte a cada mês com ganho (só com a opção de imposto).', 'taxPaid', 'brl'],
    ['Imposto vendendo tudo no fim', 'Imposto que ainda seria pago se a carteira (ou o índice) fosse vendida no último dia.', 'taxDue', 'brl'],
    ['Lucro líquido (vendendo tudo no fim)', 'Lucro − imposto que seria pago vendendo tudo no último dia: a comparação justa com os índices, que só pagam quando vendem.', 'profitNet', 'brlSigned'],
    ['Retorno ao ano líquido', 'Retorno ao ano depois do imposto, vendendo tudo no último dia.', 'irrNet', 'pct'],
    ['Maior queda', 'Maior queda do patrimônio de um topo até um fundo, descontados os aportes.', 'maxDrawdown', 'pct'],
    ['Compras', 'Quantas compras a estratégia fez (encerradas + ainda abertas no fim do período).', 'trades', 'int'],
    ['Taxa de acerto', 'Compras encerradas com lucro (vendas + proventos − valor investido).', 'winRate', 'pct0'],
    ['Resultado médio por compra', 'Média do resultado das compras encerradas, sobre o valor investido em cada uma.', 'avgReturn', 'pct'],
    ['Vendas parciais', 'Compras que atingiram o alvo e venderam a parte escolhida.', 'partials', 'int'],
    ['Ativos negociados', 'Quantos ativos diferentes a estratégia chegou a comprar.', 'assets', 'int'],
    ['Proventos recebidos', 'Dividendos e JCP recebidos pela carteira (já incluídos no lucro).', 'dividends', 'brl'],
    ['Caixa médio parado', 'Parte do patrimônio que ficou em caixa, em média, esperando sinal.', 'avgCash', 'pct0']
];

const $ = (id) => document.getElementById(id);
const signClass = (v) => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
const state = { data: null, type: 'candles', price: null, equity: null, series: {}, seq: 0, mode: 'ativo', pfChart: null,
                pf: null, robust: null, robustChart: null, pfPrice: null, query: null, pfBody: null, robustBody: null, pfSeq: 0, pageSize: 25,
                pages: { lots: 0, trades: 0 }, selected: { lots: null, trades: null } };

document.addEventListener('DOMContentLoaded', init);

function init() {
    try { state.type = localStorage.getItem(CHART_KEY) === 'line' ? 'line' : 'candles'; } catch (e) { /* sem preferência */ }
    setTypeButtons();
    $('btForm').addEventListener('submit', (ev) => { ev.preventDefault(); if (state.mode === 'carteira') runPortfolio(); else run(); });
    $('btMode').addEventListener('click', (ev) => { const mode = ev.target.dataset?.mode; if (mode) setMode(mode); });
    $('pfUniverse').addEventListener('change', () => setMode(state.mode));
    $('btExit').addEventListener('change', syncExit);
    $('pfAlloc').addEventListener('change', syncAlloc);
    syncExit();
    try { $('pfLiquidity').value = localStorage.getItem(LIQUIDITY_KEY) || 'padrao'; } catch (e) { /* sem preferência salva */ }
    if (!$('pfLiquidity').value) $('pfLiquidity').value = 'padrao';
    $('pfLiquidity').addEventListener('change', () => {
        try { localStorage.setItem(LIQUIDITY_KEY, $('pfLiquidity').value); } catch (e) { /* preferência não salva */ }
    });
    $('pfChartType').addEventListener('click', (ev) => {
        const type = ev.target.dataset?.type;
        if (!type || type === state.type) return;
        state.type = type;
        try { localStorage.setItem(CHART_KEY, type); } catch (e) { /* preferência não salva */ }
        setTypeButtons();
        applyChartType();
    });
    $('pfRobustBtn').addEventListener('click', runRobustness);
    // "ANALISAR BACKTEST": o prompt do resultado na tela (parâmetros do último cálculo, não do formulário).
    $('btAnalyzeBtn').addEventListener('click', () => analyzeResult($('btAnalyzeBtn'), apiGet(`/backtest/prompt?${state.query}`)));
    $('pfAnalyzeBtn').addEventListener('click', () => analyzeResult($('pfAnalyzeBtn'), apiPost('/backtest/carteira/prompt', state.pfBody)));
    $('pfRobustAnalyzeBtn').addEventListener('click', () => analyzeResult($('pfRobustAnalyzeBtn'), apiPost('/backtest/carteira/robustez/prompt', state.robustBody)));
    $('pfAsset').addEventListener('change', () => showPfAsset($('pfAsset').value));
    $('pfTradesBody').addEventListener('click', onPfTradeClick);
    $('pfTradesBody').addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onPfTradeClick(ev); } });
    setupPagers();
    $('chartType').addEventListener('click', (ev) => {
        const type = ev.target.dataset?.type;
        if (!type || type === state.type) return;
        state.type = type;
        try { localStorage.setItem(CHART_KEY, type); } catch (e) { /* preferência não salva */ }
        setTypeButtons();
        applyChartType();
    });
    $('lotsBody').addEventListener('click', onLotClick);
    setupTips();
    $('lotsBody').addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onLotClick(ev); } });

    // Endereço com ?mode=carteira (simulação salva no link): preenche e roda.
    const q = new URLSearchParams(location.search);
    if (q.get('mode') === 'carteira') {
        const fields = { universe: 'pfUniverse', tickers: 'pfTickers', topN: 'pfTopN', liquidity: 'pfLiquidity', initial: 'pfInitial', monthly: 'pfMonthly', position: 'pfPosition',
                         maxAssets: 'pfMaxAssets', cost: 'pfCost', target: 'btTarget', fraction: 'btFraction', from: 'btFrom', to: 'btTo', saida: 'btExit',
                         momento: 'btTiming' };
        for (const [key, id] of Object.entries(fields)) if (q.get(key) !== null) $(id).value = q.get(key);
        $('btStrict').checked = q.get('strict') === '1';
        $('pfCdi').checked = q.get('cdi') !== '0';
        $('pfTax').checked = q.get('ir') === '1';
        $('pfAdvice').checked = q.get('indicacao') !== '0';
        if (q.get('alocacao')) $('pfAlloc').value = q.get('alocacao') === 'classes' ? 'classes' : 'ativos';
        for (const g of META_GROUPS) if (q.get(`meta_${g}`) !== null) $(`pfMeta_${g}`).value = q.get(`meta_${g}`);
        syncExit();
        setMode('carteira');
        runPortfolio();
        return;
    }
    // Endereço com ?ticker= (links do screener e da carteira): roda direto.
    if (q.get('ticker')) {
        $('btTicker').value = q.get('ticker');
        if (q.get('market')) $('btMarket').value = q.get('market') === 'us' ? 'us' : 'b3';
        if (q.get('target')) $('btTarget').value = q.get('target');
        if (q.get('fraction')) $('btFraction').value = q.get('fraction');
        // Links antigos, sem o momento da venda parcial, eram todos "no alvo".
        $('btTiming').value = q.get('momento') === 'forca' ? 'forca' : (q.get('target') || q.get('fraction') ? 'alvo' : $('btTiming').value);
        // Links antigos com alvo/parte e sem "saida" usam os valores do link.
        $('btExit').value = q.get('saida') || (q.get('target') || q.get('fraction') ? 'manual' : 'config');
        syncExit();
        if (q.get('amount')) $('btAmount').value = q.get('amount');
        if (q.get('from')) $('btFrom').value = q.get('from');
        if (q.get('to')) $('btTo').value = q.get('to');
        $('btStrict').checked = q.get('strict') === '1';
        run();
    }
}

// Saída "Conforme as Configurações": o alvo, a parte e o momento do formulário não valem (ficam desabilitados).
function syncExit() {
    const manual = $('btExit').value === 'manual';
    $('btTarget').disabled = !manual;
    $('btFraction').disabled = !manual;
    $('btTiming').disabled = !manual;
}

// Alocação por classe: as metas aparecem no lugar do "Máx. de ativos".
const META_GROUPS = ['acoes', 'fundos', 'etfs', 'internacionais'];
function syncAlloc() {
    for (const el of document.querySelectorAll('#btForm [data-alloc]')) {
        el.hidden = state.mode !== 'carteira' || el.dataset.alloc !== $('pfAlloc').value;
    }
}

// "Um ativo" ou "Carteira": mostra os campos e o resultado de cada modo.
function setMode(mode) {
    state.mode = mode;
    for (const b of $('btMode').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
    for (const el of document.querySelectorAll('#btForm [data-mode]')) el.hidden = el.dataset.mode !== mode;
    if (mode === 'carteira') $('pfTickersField').hidden = $('pfUniverse').value !== 'lista';
    syncAlloc();
    $('btHeaderText').innerText = mode === 'carteira'
        ? 'O setup em vários ativos com um único caixa: o que é vendido volta para o caixa e compra os próximos sinais'
        : 'Cada compra (lote) do setup no histórico do ativo, com a venda parcial e a saída pela regra B';
    $('btResult').hidden = mode !== 'ativo' || !state.data;
    $('pfResult').hidden = mode !== 'carteira' || !state.pf;
    $('pfRobust').hidden = mode !== 'carteira' || !state.robust;
    setMsg('');
}

function setTypeButtons() {
    for (const b of document.querySelectorAll('#chartType button, #pfChartType button')) b.setAttribute('aria-pressed', String(b.dataset.type === state.type));
}

function setMsg(text, kind = '') {
    $('btMsg').innerText = text || '';
    $('btMsg').className = `form-msg ${kind}`;
}

async function run() {
    const params = new URLSearchParams({
        ticker: $('btTicker').value.trim(), market: $('btMarket').value,
        target: $('btTarget').value, fraction: $('btFraction').value, amount: $('btAmount').value,
        strict: $('btStrict').checked ? '1' : '0', saida: $('btExit').value, momento: $('btTiming').value
    });
    // Período: só entra na consulta (e na URL) quando preenchido.
    if ($('btFrom').value) params.set('from', $('btFrom').value);
    if ($('btTo').value) params.set('to', $('btTo').value);
    if (!params.get('ticker')) { setMsg('Informe o ativo.', 'error'); return; }
    const seq = ++state.seq;
    setMsg('');
    const busy = busyStart(`Rodando o backtest de ${params.get('ticker').toUpperCase()}...`);
    let data;
    try {
        data = await apiGet(`/backtest?${params}`);
    } catch (e) {
        if (seq === state.seq) setMsg(e.message, 'error');
        return;
    } finally {
        busy();
    }
    if (seq !== state.seq) return;
    state.data = data;
    state.query = params;
    $('btTicker').value = data.ticker;
    // Datas possíveis para este ativo (do primeiro pregão com MM200 ao último fechado).
    for (const id of ['btFrom', 'btTo']) { $(id).min = data.bounds.min; $(id).max = data.bounds.max; }
    if (data.note) setMsg(data.note);
    history.replaceState(null, '', `?${new URLSearchParams({ ...Object.fromEntries(params), ticker: data.ticker })}`);
    $('btResult').hidden = false;
    $('pfResult').hidden = true;
    render(data);
}

// ---------------------------------------------------------------- Renderização

function render(d) {
    $('btTitle').innerText = `${d.ticker} — ${d.name}`;
    $('btSubtitle').innerText = `${d.kindLabel} · ${d.period} (${fmtNum(d.stats.periodSessions, 0)} pregões, ≈ ${d.stats.periodDuration}) · ${d.rule.text}${d.strict ? ' Com candle de rejeição.' : ''}`;
    $('btLegend').innerHTML = priceLegend();
    $('btEquityLegend').innerHTML = `<span><i class="swatch-line" style="background:${COLORS.profit}"></i>${d.equityB ? `Com venda parcial (${fmtNum(d.rule.fraction, 0)}% ${d.rule.timing === 'forca' ? 'depois de' : 'em'} +${fmtNum(d.rule.target, 1)}%${d.rule.timing === 'forca' ? ', quando a alta perde força' : ''}) + regra B` : 'Estratégia escolhida'}</span>`
        + (d.equityB ? `<span><i class="swatch-line" style="background:${COLORS.profitB}"></i>Só a regra B</span>` : '');
    $('btCompareChart').innerText = d.compare || '';
    $('btChartNote').innerText = `Lucro (ganho ou perda) somado de todas as compras, já descontado o valor investido, com ${fmtBRL(d.stats.amount)} em cada compra; a parte ainda aberta é marcada pelo fechamento do dia.`
        + (d.adjusted ? ' Preços ajustados por proventos (fundo listado).' : '')
        + (d.market === 'us' ? ' Ativo dos EUA: resultado em reais sem a variação do dólar.' : '');
    drawCharts(d);
    renderKpis(d);
    state.pages.lots = 0;
    state.selected.lots = null;
    renderLots(d);
    keepTipsWithText($('btResult') || document);
}

// O tamanho vem do contêiner (.bt-chart no CSS): autoSize acompanha a largura da página.
function chartOptions() {
    return {
        layout: { background: { type: 'solid', color: COLORS.panel }, textColor: COLORS.text, fontSize: 12 },
        grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
        rightPriceScale: { borderColor: COLORS.border },
        timeScale: { borderColor: COLORS.border },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        localization: { locale: 'pt-BR', priceFormatter: (p) => fmtNum(p, 2) },
        autoSize: true
    };
}

function drawCharts(d) {
    state.price?.remove();
    state.equity?.remove();
    const s = state.series = priceChart($('priceChart'), d);
    const price = state.price = s.chart;
    applyChartType();

    // Lucro acumulado (só o ganho, sem o valor investido): a estratégia escolhida e, para comparar,
    // a mesma sem venda parcial (só a regra B). Linha tracejada no zero.
    const equity = state.equity = LightweightCharts.createChart($('equityChart'), chartOptions());
    const money = { type: 'custom', formatter: (v) => fmtBRL(v) };
    const eq = equity.addLineSeries({ color: COLORS.profit, lineWidth: 2, priceFormat: money, title: d.equityB ? 'Com venda parcial' : '' });
    eq.setData(d.equity);
    eq.createPriceLine({ price: 0, color: COLORS.zero, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: false });
    if (d.equityB) {
        const eqB = equity.addLineSeries({ color: COLORS.profitB, lineWidth: 2, priceFormat: money, title: 'Só a regra B' });
        eqB.setData(d.equityB);
    }

    // Os dois gráficos têm os mesmos pregões: rolar ou dar zoom num move o outro.
    let syncing = false;
    const link = (from, to) => from.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (syncing || !range) return;
        syncing = true;
        to.timeScale().setVisibleLogicalRange(range);
        syncing = false;
    });
    link(price, equity);
    link(equity, price);
    price.timeScale().fitContent();
}

// Gráfico de preço com candles, linha de fechamento e as médias (só uma das duas séries de preço
// fica visível: showType). Devolve o gráfico e as séries.
function priceChart(el, d) {
    const chart = LightweightCharts.createChart(el, chartOptions());
    const candles = chart.addCandlestickSeries({
        upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down
    });
    candles.setData(d.candles);
    const line = chart.addLineSeries({ color: COLORS.close, lineWidth: 2, priceLineVisible: false });
    line.setData(d.candles.map(c => ({ time: c.time, value: c.close })));
    for (const k of Object.keys(MA_LABEL)) {
        const ma = chart.addLineSeries({ color: COLORS[k], lineWidth: k === 'mm200' ? 2 : 1, priceLineVisible: false,
                                         lastValueVisible: false, crosshairMarkerVisible: false });
        ma.setData(d.ma[k]);
    }
    return { chart, candles, line };
}

// Candles ou linha; os marcadores das operações ficam na série visível.
function showType(series, markers) {
    if (!series?.candles) return;
    const candles = state.type === 'candles';
    series.candles.applyOptions({ visible: candles });
    series.line.applyOptions({ visible: !candles });
    const list = (markers || []).map(m => ({ time: m.time, text: m.text, ...MARKER[m.kind] }));
    series.candles.setMarkers(candles ? list : []);
    series.line.setMarkers(candles ? [] : list);
}

function applyChartType() {
    if (state.data) showType(state.series, state.data.markers);
    if (state.pfPrice) showType(state.pfPrice, state.pfPrice.markers);
}

// Legenda das médias e dos marcadores (C, P, V).
function priceLegend() {
    return [
        ...Object.entries(MA_LABEL).map(([k, label]) => `<span><i class="swatch-line" style="background:${COLORS[k]}"></i>${label}</span>`),
        `<span><i class="swatch-dot" style="background:${COLORS.buy}"></i>C = compra</span>`,
        `<span><i class="swatch-dot" style="background:${COLORS.partial}"></i>P = venda parcial</span>`,
        `<span><i class="swatch-dot" style="background:${COLORS.exit}"></i>V = saída (regra B)</span>`
    ].join('');
}

const pct = (v, d = 1) => v === null || v === undefined ? '—' : fmtPct(v, d);
const money = (v, currency) => currency === 'R$' ? fmtBRL(v) : `${currency} ${fmtNum(v, 2)}`;

function kpi(label, value, note = '', cls = '', help = '') {
    return `<div class="kpi"><div class="kpi-label">${label} ${help ? tip(help) : ''}</div><div class="kpi-value ${cls}">${value}</div><div class="kpi-note">${note}</div></div>`;
}

// ---------------------------------------------------------------- Tooltips

function renderKpis(d) {
    const s = d.stats;
    const brl = (v) => v === null || v === undefined ? '—' : fmtBRL(v);
    $('btKpis').innerHTML = [
        kpi('Lucro no período', brl(s.profit), `${pct(s.sumReturn, 0)} do valor de uma compra · realizado ${brl(s.realized)} · em aberto ${brl(s.unrealized)}`,
            signClass(s.profit), 'Quanto você teria ganho (ou perdido) em reais no período, investindo o valor por compra em cada compra do setup. Inclui as vendas já feitas e o que segue aberto, pelo último fechamento. Sem custos, impostos e proventos.'),
        kpi('Total aplicado', brl(s.invested), `${s.lots} compra(s) × ${brl(s.amount)}`, '',
            'Soma de todas as compras do período. Não é o dinheiro necessário de uma vez: o que é vendido volta para o caixa e pode pagar as compras seguintes.'),
        kpi('Capital máximo ao mesmo tempo', brl(s.maxCapital), `hoje em aberto: ${brl(s.openCapital)}`, '',
            'Maior valor investido ao mesmo tempo (preço de compra da parte ainda não vendida de cada compra). É quanto você precisaria ter para seguir todos os sinais.'),
        kpi('Retorno sobre o capital máximo', pct(s.returnOnMaxCapital, 1), `sobre o total aplicado: ${pct(s.returnOnInvested, 1)}`, signClass(s.returnOnMaxCapital),
            'Lucro no período dividido pelo capital máximo empregado ao mesmo tempo: o retorno sobre o dinheiro que a estratégia realmente precisou. "Sobre o total aplicado" divide pela soma de todas as compras (é a média por compra).'),
        kpi('Compras', fmtNum(s.lots, 0), `${s.closed} encerrada(s) · ${s.open} aberta(s) · ${s.exits} saída(s) pela regra B`, '',
            'Cada sinal do setup que gerou compra é um lote. Encerrada = vendida por inteiro; aberta = ainda em carteira no fim do período. Saídas = vezes em que a regra B vendeu o que estava aberto.'),
        kpi('Taxa de acerto', s.winRate === null ? '—' : `${fmtNum(s.winRate, 0)}%`,
            `${s.wins} de ${s.closed} encerradas no lucro · no ranking: ${fmtNum(s.winRateSmoothed, 0)}% (suavizada)`, '',
            'Percentual das compras encerradas que deram lucro (venda parcial + saída). A do ranking do screener é suavizada para 50% quando há poucas operações.'),
        kpi('Retorno médio por compra', pct(s.avgReturn), `mediana ${pct(s.medianReturn)} · compras encerradas`, signClass(s.avgReturn),
            'Média do resultado das compras encerradas. A mediana é o resultado do meio: metade das compras rendeu mais e metade menos.'),
        kpi('Melhor e pior compra', `${pct(s.best, 0)} / ${pct(s.worst, 0)}`, 'compras encerradas', '',
            'Maior ganho e maior perda entre as compras encerradas.'),
        kpi('Vendas parciais', d.rule.fraction > 0 ? `${s.partialHits} de ${s.lots}` : '—',
            d.rule.fraction > 0 ? `compras que atingiram +${fmtNum(d.rule.target, 1)}%` : 'regra sem venda parcial', '',
            'Quantas compras chegaram ao alvo e venderam a parte escolhida.'),
        kpi('Tempo médio', s.avgDays === null ? '—' : `${fmtNum(s.avgDays, 0)} pregões`,
            s.avgDays === null ? 'compras encerradas' : `≈ ${escapeHTML(s.avgDuration)} · da compra à saída, compras encerradas`, '',
            'Quanto tempo cada compra ficou na carteira até a saída, em pregões (dias de bolsa) e aproximado em anos e meses (cerca de 21 pregões por mês, 252 por ano).'),
        kpi('Devolvido do topo', s.avgGiveback === null ? '—' : `${fmtNum(s.avgGiveback, 1)}%`, 'média: do maior fechamento até a saída', '',
            'Quanto o preço caiu, em média, do maior fechamento depois da compra até a venda pela regra B. Mostra quanto do ganho foi devolvido antes da saída.'),
        kpi('Alta máxima média', pct(s.avgMaxGain), 'maior máxima depois da compra', '',
            'Em média, quanto o preço chegou a subir depois de cada compra. Comparado ao resultado, mostra o potencial deixado na mesa.'),
        kpi('Indicação do Screener', s.advice ? `${s.advice.wait} de ${s.lots} seriam AGUARDAR` : '—',
            s.advice && s.advice.wait ? `retorno médio ${pct(s.advice.waitReturn)} nelas x ${pct(s.advice.buyReturn)} nas demais` : 'todas as compras seriam COMPRAR', '',
            'Quantas compras do backtest a Indicação do Screener mandaria aguardar (tendência fraca ou mais fraca que o Ibovespa em 6 meses; internacionais não são filtrados) e como elas se saíram em comparação com as demais. Essas compras aparecem com o selo AGUARDAR na tabela.'),
        kpi('Comprar e manter', pct(s.buyHold), s.buyHoldFrom ? `da 1ª compra (${fmtDate(s.buyHoldFrom)}) até ${fmtDate(d.lastDate)}` : 'sem compras', signClass(s.buyHold),
            'Quanto teria rendido comprar uma vez, no preço da primeira compra do período, e manter até o fim, sem vender. Serve de comparação para a estratégia.')
    ].join('');
    const b = d.onlyB;
    $('btCompare').innerHTML = b ? `<strong>Só a regra B</strong> (sem venda parcial), no mesmo período e com o mesmo valor por compra: lucro <strong>${brl(b.profit)}</strong>`
        + ` · capital máximo ao mesmo tempo ${brl(b.maxCapital)} · retorno sobre o capital máximo <strong class="${signClass(b.returnOnMaxCapital)}">${pct(b.returnOnMaxCapital, 1)}</strong>`
        + ` (com a venda parcial: ${pct(s.returnOnMaxCapital, 1)})`
        + ` · acerto ${b.winRate === null ? '—' : `${fmtNum(b.winRate, 0)}%`} · retorno médio ${pct(b.avgReturn)} · ${b.closed} compra(s) encerrada(s). `
        + tip('A mesma estratégia vendendo tudo só pela regra B, sem a venda parcial no alvo. O lucro em reais favorece a regra B quando ela mantém mais dinheiro investido; o retorno sobre o capital máximo compara as duas pelo dinheiro que cada uma realmente precisou ter aplicado ao mesmo tempo.') : '';
}

function lotCell(point, currency) {
    if (!point) return '<span class="muted">—</span>';
    const ret = point.return === undefined ? '' : ` <span class="${signClass(point.return)}">${pct(point.return)}</span>`;
    return `${fmtDate(point.date)}<div class="small muted">${money(point.price, currency)}${ret}</div>`;
}

function renderLots(d) {
    const rows = pageRows('lots', d.lots);
    $('lotsBody').innerHTML = rows.length ? rows.map(l => `
        <tr class="lot-row${String(l.n) === state.selected.lots ? ' selected' : ''}" tabindex="0" data-n="${l.n}" aria-label="Focar o lote ${l.n} no gráfico">
            <td class="c"><strong>${l.n}</strong></td>
            <td class="nowrap">${lotCell(l.entry, d.currency)}</td>
            <td class="nowrap">${lotCell(l.partial, d.currency)}</td>
            <td class="nowrap">${lotCell(l.exit, d.currency)}</td>
            <td class="r nowrap ${signClass(l.return)}"><strong>${pct(l.return)}</strong></td>
            <td class="r nowrap ${signClass(l.profit)}">${fmtBRL(l.profit)}</td>
            <td class="r nowrap">${fmtNum(l.days, 0)}<div class="small muted">≈ ${escapeHTML(l.duration)}</div></td>
            <td class="r nowrap">${pct(l.maxGain)}</td>
            <td class="r nowrap">${l.giveback === null ? '—' : `${fmtNum(l.giveback, 1)}%`}</td>
            <td><span class="badge ${l.open ? 'badge-fill' : 'badge-low'}">${escapeHTML(l.status)}</span>
                ${l.wait && l.wait.length ? signalBadge('AGUARDAR', `A Indicação do Screener mandaria aguardar esta compra: ${l.wait.join('; ')}.`, 'badge-fill') : ''}</td>
        </tr>`).join('')
        : '<tr><td colspan="10" class="empty-row">Nenhum sinal do setup no período.</td></tr>';
}

// ---------------------------------------------------------------- Paginação

// Tabelas paginadas: lotes (um ativo) e operações (carteira). A quantidade por página é a mesma
// nas duas e fica salva; a página e a linha selecionada de cada uma ficam em state.pages/selected.
const PAGERS = {
    lots: { nav: 'lotsPager', items: () => state.data?.lots, render: () => renderLots(state.data) },
    trades: { nav: 'pfTradesPager', items: () => state.pf?.trades, render: () => renderPfTrades() }
};
const PAGE_SIZES = [25, 50, 100];

function setupPagers() {
    try { state.pageSize = Number(localStorage.getItem(PAGE_SIZE_KEY)) || state.pageSize; } catch (e) { /* sem preferência salva */ }
    if (!PAGE_SIZES.includes(state.pageSize)) state.pageSize = PAGE_SIZES[0];
    for (const [name, pager] of Object.entries(PAGERS)) {
        const nav = $(pager.nav);
        const select = nav.querySelector('select');
        select.value = String(state.pageSize);
        select.addEventListener('change', () => {
            state.pageSize = Number(select.value);
            try { localStorage.setItem(PAGE_SIZE_KEY, String(state.pageSize)); } catch (e) { /* preferência não salva */ }
            for (const [other, p] of Object.entries(PAGERS)) {
                $(p.nav).querySelector('select').value = select.value;
                state.pages[other] = 0;
                if (p.items()) p.render();
            }
        });
        nav.addEventListener('click', (ev) => {
            const go = ev.target.closest?.('button[data-page]')?.dataset.page;
            const items = pager.items();
            if (!go || !items) return;
            const last = Math.max(0, Math.ceil(items.length / state.pageSize) - 1);
            state.pages[name] = { first: 0, prev: state.pages[name] - 1, next: state.pages[name] + 1, last }[go];
            pager.render();
        });
    }
}

// Linhas da página atual da tabela `name` e o paginador atualizado (escondido até 25 linhas).
function pageRows(name, items) {
    const nav = $(PAGERS[name].nav);
    const size = state.pageSize;
    const pages = Math.max(1, Math.ceil(items.length / size));
    const page = state.pages[name] = Math.min(Math.max(0, state.pages[name]), pages - 1);
    const start = page * size;
    const rows = items.slice(start, start + size);
    nav.hidden = items.length <= PAGE_SIZES[0];
    nav.querySelector('.pager-info').innerText = `Página ${page + 1} de ${pages} · ${start + 1}–${start + rows.length} de ${items.length}`;
    for (const b of nav.querySelectorAll('button[data-page]')) {
        b.disabled = b.dataset.page === 'first' || b.dataset.page === 'prev' ? page === 0 : page === pages - 1;
    }
    return rows;
}

// ---------------------------------------------------------------- Modo Carteira

function portfolioBody() {
    return {
        universe: $('pfUniverse').value, tickers: $('pfTickers').value, initial: $('pfInitial').value, monthly: $('pfMonthly').value,
        position: $('pfPosition').value, maxAssets: $('pfMaxAssets').value, topN: $('pfTopN').value, liquidity: $('pfLiquidity').value,
        cost: $('pfCost').value, target: $('btTarget').value,
        fraction: $('btFraction').value, from: $('btFrom').value, to: $('btTo').value, strict: $('btStrict').checked, cdi: $('pfCdi').checked, ir: $('pfTax').checked,
        saida: $('btExit').value, momento: $('btTiming').value, indicacao: $('pfAdvice').checked, alocacao: $('pfAlloc').value,
        metas: Object.fromEntries(META_GROUPS.map(g => [g, $(`pfMeta_${g}`).value])),
        janela: $('pfRobustWindow').value
    };
}

async function runPortfolio() {
    const body = portfolioBody();
    const seq = ++state.seq;
    setMsg('');
    const busy = busyStart('Simulando a carteira pelo ranking de cada dia (a primeira vez pode levar um minuto)...');
    let d;
    try {
        d = await apiPost('/backtest/carteira', body);
    } catch (e) {
        if (seq === state.seq) setMsg(e.message, 'error');
        return;
    } finally {
        busy();
    }
    if (seq !== state.seq) return;
    state.pf = d;
    state.pfBody = body;
    for (const id of ['btFrom', 'btTo']) { $(id).min = d.bounds.min; $(id).max = d.bounds.max; }
    if (d.note) setMsg(d.note);
    const { metas, ...plain } = body;
    const url = new URLSearchParams({ mode: 'carteira', ...plain, strict: body.strict ? '1' : '0', cdi: body.cdi ? '1' : '0', ir: body.ir ? '1' : '0',
                                      indicacao: body.indicacao ? '1' : '0',
                                      ...Object.fromEntries(Object.entries(metas).map(([g, v]) => [`meta_${g}`, v])) });
    for (const [k, v] of [...url.entries()]) if (v === '') url.delete(k);
    history.replaceState(null, '', `?${url}`);
    $('pfResult').hidden = false;
    $('btResult').hidden = true;
    renderPortfolio(d);
}

// Copia o prompt de análise de um resultado (montado no servidor) para colar numa IA.
async function analyzeResult(btn, request) {
    try {
        await copyServerText(btn, request.then(r => r.text), 'Análise do backtest');
    } catch (e) {
        setMsg(`Não foi possível montar a análise do resultado (${e.message}).`, 'error');
    }
}

// "Testar robustez": a mesma carteira começando em cada ano, até o mesmo fim.
async function runRobustness() {
    const body = portfolioBody();
    const seq = ++state.seq;
    setMsg('');
    const busy = busyStart('Simulando a carteira em cada período (pode levar alguns minutos)...');
    let d;
    try {
        d = await apiPost('/backtest/carteira/robustez', body);
    } catch (e) {
        if (seq === state.seq) setMsg(e.message, 'error');
        return;
    } finally {
        busy();
    }
    if (seq !== state.seq) return;
    state.robust = d;
    state.robustBody = body;
    $('pfRobust').hidden = false;
    $('btResult').hidden = true;
    renderRobustness(d);
    $('pfRobust').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderRobustness(d) {
    setTimeout(() => keepTipsWithText($('pfRobust')));
    const unit = d.window ? 'janelas' : 'inícios';
    $('pfRobustTitle').innerText = d.window ? `Robustez — janelas de ${d.window} anos` : 'Robustez — cada ano de início até o fim';
    $('pfRobustSubtitle').innerText = `${d.rule} · ${d.periods} ${unit}` + (d.window ? '' : `, todos até ${fmtDate(d.end)}`)
        + ` · régua: ${d.ref.label}` + (d.tax ? ' · com imposto de renda (valores líquidos, vendendo tudo no fim de cada período)' : '')
        + (d.warnings.length ? ` · ${d.warnings.join(' ')}` : '');
    $('pfRobustVerdict').innerText = d.verdict;
    $('pfRobustSummary').innerHTML = d.summary.map(t => `<li>${escapeHTML(t)}</li>`).join('');

    const c = d.cards;
    const pct = (v, dec = 1) => v === null || v === undefined ? '—' : fmtPct(v, dec);
    const pp = (v) => v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${fmtNum(v, 1)} p.p.`;
    const score = (w) => `<div class="robust-score"><span>${escapeHTML(ROBUST_SHORT[w.key] || w.key)}</span>`
        + `<span class="robust-bar"><i style="width:${w.wins / d.periods * 100}%"></i></span><strong>${w.wins}/${d.periods}</strong></div>`;
    $('pfRobustKpis').innerHTML = [
        kpi('Retorno ao ano', pct(c.irr.median), `mediana · de ${pct(c.irr.min)} a ${pct(c.irr.max)}`, signClass(c.irr.median),
            'Taxa interna de retorno da estratégia em cada período: a mediana e a faixa do pior ao melhor.'),
        kpi(`Contra: ${escapeHTML(c.excess.label)}`, pp(c.excess.median), `mediana ao ano · pior ${pp(c.excess.worst)} (${escapeHTML(c.excess.worstLabel || '—')}) · à frente em ${c.excess.positive}/${d.periods}`,
            signClass(c.excess.median), `Retorno ao ano da estratégia menos o da régua (${d.ref.label}) com os mesmos aportes, em pontos percentuais. Com metas por classe, a régua são os índices na mesma proporção das metas.`),
        kpi('Placar', c.wins.map(score).join(''), `${unit} em que a estratégia lucrou mais`, '',
            'Em quantos períodos a estratégia terminou com mais lucro que cada comparação.'),
        kpi('Maior queda', pct(c.drawdown.worst), `${escapeHTML(c.drawdown.worstLabel)} · mediana ${pct(c.drawdown.median)}`, 'neg',
            'Maior queda do patrimônio de um topo até um fundo, descontados os aportes: a pior entre os períodos e a mediana.'),
        kpi('Lucro sobre o aportado', pct(c.profitPct.median, 0), 'mediana entre os períodos', signClass(c.profitPct.median),
            'Lucro ÷ total aportado em cada período. Cresce com a duração; compare períodos de mesmo tamanho.')
    ].join('');

    const cols = d.columns.map(col => [col.key, col.label, PF_COLORS[col.key]]);
    renderRobustChart(d, cols);

    const mark = (beat) => beat === undefined ? '' : beat ? ' <span class="pos" title="A estratégia ganhou desta comparação">✓</span>' : ' <span class="neg" title="A estratégia perdeu desta comparação">✗</span>';
    $('pfRobustHead').innerHTML = `<tr><th>${d.window ? 'Janela' : 'Início'}</th><th class="r">Aportado</th>`
        + cols.map(([, label, color]) => `<th class="r"><i class="swatch-line" style="background:${color}"></i> ${escapeHTML(label)}</th>`).join('')
        + `<th class="r">Compras</th><th class="r">Caixa médio</th>${d.hasPartial ? '<th class="r">Parcial − regra B</th>' : ''}`
        + `<th class="r">Retorno ao ano</th><th class="r">${escapeHTML(d.ref.short)} ao ano</th><th class="r">Diferença</th><th class="r">Maior queda</th></tr>`;
    $('pfRobustBody').innerHTML = d.rows.map(r => `<tr>
            <td class="nowrap"><strong>${fmtDate(r.from)}</strong>${d.window ? ` a ${fmtDate(r.to)}` : ''}<div class="small muted">${escapeHTML(r.duration)}</div></td>
            <td class="r nowrap">${fmtBRL(r.rule.contributed)}</td>
            ${cols.map(([key]) => `<td class="r nowrap"><span class="${signClass(r[key].profit)}">${fmtBRL(r[key].profit)}</span>${key === 'rule' ? '' : mark(r.beats[key])}</td>`).join('')}
            <td class="r">${fmtNum(r.rule.trades, 0)}</td>
            <td class="r">${fmtNum(r.rule.avgCash, 0)}%</td>
            ${d.hasPartial ? `<td class="r nowrap ${signClass(r.diffOnlyB)}">${r.diffOnlyB >= 0 ? '+' : ''}${fmtBRL(r.diffOnlyB)}</td>` : ''}
            <td class="r nowrap ${signClass(r.rule.irr)}">${pct(r.rule.irr)}</td>
            <td class="r nowrap ${signClass(r[d.ref.key].irr)}">${pct(r[d.ref.key].irr)}</td>
            <td class="r nowrap ${signClass(r.excess)}">${pp(r.excess)}</td>
            <td class="r nowrap neg">${pct(r.rule.maxDrawdown)}</td>
        </tr>`).join('');
}

// Rótulos curtos do placar (chaves do servidor).
const ROBUST_SHORT = { mix: 'Índices metas', bova: 'BOVA11', ivvb: 'IVVB11', cdi: 'CDI', onlyB: 'Só regra B' };

// Barras agrupadas: o retorno ao ano de cada linha (estratégia, regra B, BOVA11, CDI) em cada período.
function renderRobustChart(d, cols) {
    state.robustChart?.destroy();
    state.robustChart = new Chart($('pfRobustChart'), {
        type: 'bar',
        data: {
            labels: d.rows.map(r => r.label),
            datasets: cols.map(([key, label, color]) => ({
                label, data: d.rows.map(r => r[key].irr), backgroundColor: color, hoverBackgroundColor: color,
                borderRadius: 2, maxBarThickness: 28
            }))
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: COLORS.text, usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 12 } },
                tooltip: {
                    backgroundColor: COLORS.panel, borderColor: COLORS.border, borderWidth: 1, titleColor: COLORS.text, bodyColor: COLORS.text, padding: 10,
                    callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${ctx.raw === null ? '—' : `${fmtPct(ctx.raw, 1)} ao ano`}` }
                }
            },
            scales: {
                x: { ticks: { color: COLORS.zero, maxRotation: 0 }, grid: { display: false }, border: { color: COLORS.border } },
                y: { ticks: { color: COLORS.zero, callback: (v) => `${fmtNum(v, 0)}%` }, grid: { color: COLORS.grid }, border: { display: false } }
            }
        }
    });
}

function renderPortfolio(d) {
    setTimeout(() => keepTipsWithText($('pfResult')));  // "?" dos cartões e da comparação, já desenhados
    const p = d.params;
    $('pfTitle').innerText = `Carteira — ${d.universe.label} (${d.universe.ranked} ativos passaram pelo ranking)`;
    $('pfSubtitle').innerText = `${d.period} (${fmtNum(d.sessions, 0)} pregões, ≈ ${d.duration}) · Top ${p.topN} do ranking · capital inicial ${fmtBRL(p.initial)}`
        + (p.monthly > 0 ? ` + ${fmtBRL(p.monthly)} por mês` : '') + ` · ${fmtNum(p.position, 0)}% do patrimônio por compra · até ${p.maxAssets} ativos`
        + ` · liquidez mínima ${$('pfLiquidity').querySelector(`option[value="${p.liquidity}"]`)?.textContent || p.liquidity}`
        + ` · custo ${fmtNum(p.cost, 2)}% por operação${p.cdi ? ' · caixa no CDI' : ''}${p.tax ? ' · com imposto de renda' : ''}${p.strict ? ' · com candle de rejeição' : ''}`;
    $('pfWarnings').innerText = d.warnings.join(' ');
    $('pfWarnings').style.display = d.warnings.length ? 'block' : 'none';
    $('pfCompare').innerText = d.compare || '';
    $('pfNote').innerText = 'Lucro = patrimônio − tudo o que foi aportado até o dia. Passe o mouse no gráfico para ver o lucro de cada linha em cada dia.'
        + (d.adviceSkipped !== null && d.adviceSkipped !== undefined
            ? ` Seguindo a Indicação do Screener: ${fmtNum(d.adviceSkipped, 0)} sinal(is) do ranking em AGUARDAR não viraram compra (conta cada ativo em cada dia em que apareceu).` : '');
    $('pfLegend').innerHTML = d.strategies.map(st => `<span><i class="swatch-line" style="background:${PF_COLORS[st.key]}"></i>${escapeHTML(st.label)}</span>`).join('');

    state.pfChart?.remove();
    const chart = state.pfChart = LightweightCharts.createChart($('pfChart'), chartOptions());
    const moneyFmt = { type: 'custom', formatter: (v) => fmtBRL(v) };
    d.strategies.forEach((st, k) => {
        const line = chart.addLineSeries({ color: PF_COLORS[st.key], lineWidth: st.key === 'rule' || st.key === 'onlyB' ? 2 : 1,
                                           lineStyle: st.key === 'cdi' ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
                                           priceFormat: moneyFmt, title: st.label });
        line.setData(st.curve);
        if (k === 0) line.createPriceLine({ price: 0, color: COLORS.zero, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: false });
    });
    chart.timeScale().fitContent();

    const fmt = {
        brl: (v) => fmtBRL(v), brlSigned: (v) => `<span class="${signClass(v)}">${fmtBRL(v)}</span>`,
        pct: (v) => `<span class="${signClass(v)}">${fmtPct(v, 1)}</span>`, pct0: (v) => `${fmtNum(v, 0)}%`, int: (v) => fmtNum(v, 0)
    };
    $('pfCompareHead').innerHTML = `<tr><th></th>${d.strategies.map(st => `<th class="r"><i class="swatch-line" style="background:${PF_COLORS[st.key]}"></i> ${escapeHTML(st.label)}</th>`).join('')}</tr>`;
    // Só as linhas com algum valor (as de imposto aparecem só com a opção ligada).
    const rows = PF_ROWS.filter(([, , key]) => d.strategies.some(st => st.metrics[key] !== undefined && st.metrics[key] !== null));
    $('pfCompareBody').innerHTML = rows.map(([label, help, key, kind]) => `<tr><td>${label} ${tip(help)}</td>`
        + d.strategies.map(st => {
            const v = st.metrics[key];
            return `<td class="r nowrap">${v === undefined || v === null ? '<span class="muted">—</span>' : fmt[kind](v)}${key === 'trades' && v ? `<div class="small muted">${st.metrics.closed} encerradas · ${st.metrics.open} abertas</div>` : ''}</td>`;
        }).join('') + '</tr>').join('');

    const rule = d.strategies[0];
    renderPfKpis(d);
    renderPfYears(d);
    $('pfAsset').innerHTML = d.assets.map(a => `<option value="${escapeHTML(a.ticker)}">${escapeHTML(a.ticker)} — ${a.trades} operação(ões) · lucro ${fmtBRL(a.profit)}</option>`).join('')
        || '<option value="">Nenhuma operação</option>';
    $('pfPriceLegend').innerHTML = priceLegend();
    state.pfPrice?.chart.remove();
    state.pfPrice = null;
    if (d.assets.length) showPfAsset(d.assets[0].ticker);
    $('pfTradesTitle').innerText = `Operações — ${rule.label} (${d.trades.length})`;
    state.pages.trades = 0;
    state.selected.trades = null;
    renderPfTrades();
}

// Tabela de operações, uma página por vez.
function renderPfTrades() {
    const d = state.pf;
    const p = d.params;
    const rows = pageRows('trades', d.trades);
    const single = (t) => `backtest.html?${new URLSearchParams({ ticker: t, market: 'b3', target: p.target, fraction: p.fraction, from: d.bounds.from, to: d.bounds.to })}`;
    const point = (x) => x ? `${fmtDate(x.date)}<div class="small muted">${fmtBRL(x.price)}</div>` : '<span class="muted">—</span>';
    $('pfTradesBody').innerHTML = rows.length ? rows.map(t => `
        <tr class="lot-row${String(t.n) === state.selected.trades ? ' selected' : ''}" tabindex="0" data-n="${t.n}" aria-label="Ver a operação ${t.n} (${escapeHTML(t.ticker)}) no gráfico">
            <td class="c">${t.n}</td>
            <td><div class="rank-inline">#${t.n}</div><a href="${single(t.ticker)}" class="ticker-link">${escapeHTML(t.ticker)}</a></td>
            <td class="c nowrap">${t.rank}º<div class="small muted">Score ${fmtNum(t.score, 0)}</div></td>
            <td class="nowrap">${point(t.entry)}</td>
            <td class="nowrap">${point(t.partial)}</td>
            <td class="nowrap">${point(t.exit)}</td>
            <td class="r nowrap">${fmtBRL(t.invested)}</td>
            <td class="r nowrap">${t.dividends ? fmtBRL(t.dividends) : '<span class="muted">—</span>'}</td>
            <td class="r nowrap ${signClass(t.profit)}"><strong>${fmtBRL(t.profit)}</strong></td>
            <td class="r nowrap ${signClass(t.return)}">${fmtPct(t.return, 1)}</td>
            <td class="r nowrap">${fmtNum(t.days, 0)}<div class="small muted">≈ ${escapeHTML(t.duration)}</div></td>
            <td><span class="badge ${t.open ? 'badge-fill' : 'badge-low'}">${escapeHTML(t.status)}</span></td>
        </tr>`).join('')
        : '<tr><td colspan="12" class="empty-row">Nenhuma compra no período.</td></tr>';
}

// Tabela ano a ano: para cada ano, o lucro (R$) e a rentabilidade (%) de cada estratégia.
function renderPfYears(d) {
    const sts = d.strategies;
    $('pfYearsHead').innerHTML = `<tr><th rowspan="2">Ano</th>${sts.map(st => `<th class="c" colspan="2"><i class="swatch-line" style="background:${PF_COLORS[st.key]}"></i> ${escapeHTML(st.label)}</th>`).join('')}</tr>`
        + `<tr>${sts.map(() => '<th class="r">Lucro (R$)</th><th class="r">Rentab.</th>').join('')}</tr>`;
    const years = sts[0].years;
    const partial = (y, k) => (k === 0 && y.from.slice(5) !== '01-02' && y.from.slice(5) > '01-05') || (k === years.length - 1 && y.to.slice(5) < '12-20');
    $('pfYearsBody').innerHTML = years.map((y, k) => `<tr>
            <td class="nowrap"><strong>${y.year}</strong>${partial(y, k) ? `<div class="small muted">${fmtDate(y.from).slice(0, 5)} a ${fmtDate(y.to).slice(0, 5)}</div>` : ''}</td>
            ${sts.map(st => { const r = st.years[k]; return `<td class="r nowrap ${signClass(r.profit)}">${fmtBRL(r.profit)}</td><td class="r nowrap ${signClass(r.return)}">${fmtPct(r.return, 1)}</td>`; }).join('')}
        </tr>`).join('')
        + `<tr class="total-row"><td><strong>Total</strong></td>${sts.map(st => `<td class="r nowrap ${signClass(st.metrics.profit)}"><strong>${fmtBRL(st.metrics.profit)}</strong></td>`
            + `<td class="r nowrap ${signClass(st.metrics.irr)}"><strong>${st.metrics.irr === null ? '—' : `${fmtPct(st.metrics.irr, 1)} a.a.`}</strong></td>`).join('')}</tr>`;
    $('pfYearsNote').innerText = 'Total: lucro do período inteiro e o retorno ao ano equivalente. Lucro do ano = patrimônio no fim − patrimônio no início − aportes do ano (já inclui proventos e custos). Rentabilidade pela cota, sem o efeito dos aportes.';
}

// Cards de resumo da estratégia escolhida (como no modo de um ativo) e a linha "Só a regra B".
function renderPfKpis(d) {
    const s = d.strategies[0].metrics;
    // Comparações com os mesmos aportes: os índices nas metas (a régua justa com metas por classe), BOVA11 e IVVB11.
    const comps = ['mix', 'bova', 'ivvb'].map(k => d.strategies.find(x => x.key === k)).filter(Boolean);
    const brl = (v) => v === null || v === undefined ? '—' : fmtBRL(v);
    $('pfSummaryTitle').innerText = `Resumo — ${d.strategies[0].label}`;
    $('pfKpis').innerHTML = [
        kpi('Lucro no período', brl(s.profit), `${pct(s.profitPct, 1)} sobre o aportado · encerradas ${brl(s.closedProfit)} · em aberto ${brl(s.openProfit)}`,
            signClass(s.profit), 'Patrimônio final menos tudo o que foi aportado: quanto a carteira ganhou de fato no período, com o dinheiro girando entre os ativos do ranking. Inclui proventos e custos; sem imposto de renda.'),
        kpi('Patrimônio final', brl(s.final), `aportado ${brl(s.contributed)}`, '', 'Valor da carteira no último dia do período: caixa + ativos pelo fechamento.'),
        kpi('Retorno ao ano', pct(s.irr, 1), `maior queda ${pct(s.maxDrawdown, 1)}`, signClass(s.irr),
            'Rendimento anual equivalente (taxa interna de retorno), considerando quando cada aporte entrou. A maior queda é a pior perda do patrimônio de um topo até um fundo, descontados os aportes.'),
        kpi('Compras', fmtNum(s.trades, 0), `${s.closed} encerrada(s) · ${s.open} aberta(s) · ${s.exits} saída(s) pela regra B`, '',
            'Quantas compras a carteira fez. Encerrada = vendida por inteiro; aberta = ainda na carteira no fim do período.'),
        kpi('Taxa de acerto', s.winRate === null ? '—' : `${fmtNum(s.winRate, 0)}%`, `${s.wins} de ${s.closed} encerradas no lucro`, '',
            'Percentual das compras encerradas que deram lucro (vendas + proventos − valor investido, já com os custos).'),
        kpi('Resultado médio por compra', pct(s.avgReturn), `mediana ${pct(s.medianReturn)} · compras encerradas`, signClass(s.avgReturn),
            'Média do resultado das compras encerradas, sobre o valor investido em cada uma. A mediana é o resultado do meio.'),
        kpi('Melhor e pior compra', `${pct(s.best, 0)} / ${pct(s.worst, 0)}`, 'compras encerradas', '', 'Maior ganho e maior perda entre as compras encerradas.'),
        kpi('Vendas parciais', d.params.fraction > 0 ? `${s.partials} de ${s.trades}` : '—',
            d.params.fraction > 0 ? `compras que atingiram +${fmtNum(d.params.target, 1)}%` : 'regra sem venda parcial', '',
            'Quantas compras chegaram ao alvo e venderam a parte escolhida (o dinheiro voltou para o caixa e comprou outros ativos do ranking).'),
        kpi('Tempo médio', s.avgDays === null ? '—' : `${fmtNum(s.avgDays, 0)} pregões`,
            s.avgDays === null ? 'compras encerradas' : `≈ ${escapeHTML(s.avgDuration)} · da compra à saída, compras encerradas`, '',
            'Quanto tempo cada compra ficou na carteira até a saída, em pregões (dias de bolsa) e aproximado em anos e meses (cerca de 21 pregões por mês).'),
        kpi('Devolvido do topo', s.avgGiveback === null ? '—' : `${fmtNum(s.avgGiveback, 1)}%`, 'média: do maior fechamento até a saída', '',
            'Quanto o preço caiu, em média, do maior fechamento depois da compra até a venda pela regra B.'),
        kpi('Alta máxima média', pct(s.avgMaxGain), 'maior máxima depois da compra', '',
            'Em média, quanto o preço chegou a subir depois de cada compra. Comparado ao resultado, mostra o potencial deixado na mesa.'),
        kpi('Ativos negociados', fmtNum(s.assets, 0), `de ${d.universe.ranked} que passaram pelo ranking no período`, '',
            'Quantos ativos diferentes a carteira chegou a comprar, conforme o ranking de cada dia.'),
        kpi('Proventos recebidos', brl(s.dividends), 'já incluídos no lucro', '', 'Dividendos e JCP recebidos pela carteira na data-ex.'),
        kpi('Caixa médio parado', `${fmtNum(s.avgCash, 0)}%`, d.params.cdi ? 'rendendo CDI' : 'sem render juros', '',
            'Parte do patrimônio que ficou em caixa, em média, esperando ativos no ranking.'),
        s.taxPaid !== undefined ? kpi('Imposto de renda', brl(s.taxPaid), `pago no período · vendendo tudo no fim: mais ${brl(s.taxDue)} · lucro líquido ${brl(s.profitNet)} (${pct(s.irrNet, 1)} ao ano)`,
            '', 'Imposto sobre as vendas, pago no mês seguinte a cada mês com ganho. O lucro líquido desconta também o imposto que seria pago vendendo tudo no último dia: é o número a comparar com os índices, que só pagam quando vendem.') : '',
        ...comps.map(st => {
            const m = st.metrics;
            const net = m.profitNet !== undefined ? ` · líquido ${brl(m.profitNet)}` : '';
            return kpi(`${escapeHTML(st.label.replace(' (mesmos aportes)', ''))} com os mesmos aportes`, brl(m.profit),
                `retorno ao ano ${pct(m.irr, 1)} · maior queda ${pct(m.maxDrawdown, 1)}${net}`, signClass(m.profit),
                st.key === 'mix'
                    ? 'Os mesmos aportes divididos entre os índices na proporção das metas por classe (ações, fundos e ETFs Brasil no BOVA11; internacionais no IVVB11), sem vender: a régua justa para uma carteira com internacionais.'
                    : 'Lucro de comprar o índice com o mesmo capital inicial e os mesmos aportes, nas mesmas datas, sem vender: a referência para saber se a estratégia vale a pena.');
        })
    ].join('');
    const b = d.strategies.find(x => x.key === 'onlyB')?.metrics;
    $('pfOnlyB').innerHTML = b ? `<strong>Só a regra B</strong> (sem venda parcial), mesma carteira e mesmo período: lucro <strong class="${signClass(b.profit)}">${brl(b.profit)}</strong>`
        + ` · retorno ao ano ${pct(b.irr, 1)} · maior queda ${pct(b.maxDrawdown, 1)} · acerto ${b.winRate === null ? '—' : `${fmtNum(b.winRate, 0)}%`}`
        + ` · ${b.trades} compra(s) em ${b.assets} ativo(s). `
        + tip('A mesma carteira, com o mesmo ranking e o mesmo dinheiro, vendendo tudo só pela regra B. Como o dinheiro gira nas duas, a diferença de lucro mostra qual saída rendeu mais na prática.') : '';
}

// Gráfico de um ativo no período com as operações da carteira nele (candles e médias pelo backtest
// do ativo; marcadores pelas operações da carteira). range: foca uma operação.
async function showPfAsset(ticker, range = null) {
    const d = state.pf;
    if (!d || !ticker) return;
    $('pfAsset').value = ticker;
    const p = d.params;
    const seq = ++state.pfSeq;
    $('pfChartMsg').innerText = `Carregando ${ticker}...`;
    let data;
    try {
        data = await apiGet(`/backtest?${new URLSearchParams({ ticker, market: 'b3', target: p.target, fraction: p.fraction,
            strict: p.strict ? '1' : '0', from: d.bounds.from, to: d.bounds.to })}`);
    } catch (e) {
        if (seq === state.pfSeq) $('pfChartMsg').innerText = `Não foi possível carregar o gráfico de ${ticker} (${e.message}).`;
        return;
    }
    if (seq !== state.pfSeq) return;
    const info = d.assets.find(a => a.ticker === ticker);
    $('pfChartInfo').innerText = `${data.name} · ${info?.trades || 0} operação(ões) da carteira neste ativo · lucro ${fmtBRL(info?.profit || 0)}`
        + (data.adjusted ? ' · gráfico com preço ajustado por proventos' : '');
    $('pfChartMsg').innerText = '';
    state.pfPrice?.chart.remove();
    state.pfPrice = { ...priceChart($('pfPriceChart'), data), markers: d.markers[ticker] || [] };
    showType(state.pfPrice, state.pfPrice.markers);
    if (range) state.pfPrice.chart.timeScale().setVisibleRange(range);
    else state.pfPrice.chart.timeScale().fitContent();
}

function onPfTradeClick(ev) {
    if (ev.target.closest?.('a')) return; // o código abre o backtest do ativo
    const row = ev.target.closest?.('tr[data-n]');
    if (!row || !state.pf) return;
    const t = state.pf.trades.find(x => String(x.n) === row.dataset.n);
    for (const r of $('pfTradesBody').querySelectorAll('tr.selected')) r.classList.remove('selected');
    row.classList.add('selected');
    state.selected.trades = row.dataset.n;
    showPfAsset(t.ticker, t.range);
    $('pfPriceChart').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function onLotClick(ev) {
    const row = ev.target.closest?.('tr[data-n]');
    if (!row || !state.data || !state.price) return;
    const lot = state.data.lots.find(l => String(l.n) === row.dataset.n);
    for (const r of $('lotsBody').querySelectorAll('tr.selected')) r.classList.remove('selected');
    row.classList.add('selected');
    state.selected.lots = row.dataset.n;
    state.price.timeScale().setVisibleRange(lot.range);
    $('priceChart').scrollIntoView({ behavior: 'smooth', block: 'center' });
}
