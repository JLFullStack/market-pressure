// Aba Backtest. Depende de core.js (apiGet e formatadores) e do Lightweight Charts (CDN),
// carregados antes deste script.
//
// O backtest inteiro é calculado no Python (screener.backtest_view):
//   GET /api/backtest?ticker=&market=&target=&fraction=&strict=  -> candles, médias, lotes,
//       marcadores, resultado acumulado e resumo
// Aqui só se desenham os gráficos e as tabelas.

// Cores (validadas sobre o fundo #161b22 do painel). Médias como no TradingView do usuário:
// MM20 laranja, MM50 azul, MM200 roxa.
const COLORS = {
    up: '#26a69a', down: '#ef5350', close: '#c9d1d9',
    mm20: '#e3a33b', mm50: '#3987e5', mm200: '#a371f7',
    buy: '#3fb950', partial: '#e3b341', exit: '#f85149',
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

const $ = (id) => document.getElementById(id);
const signClass = (v) => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
const state = { data: null, type: 'candles', price: null, equity: null, series: {}, seq: 0 };

document.addEventListener('DOMContentLoaded', init);

function init() {
    try { state.type = localStorage.getItem(CHART_KEY) === 'line' ? 'line' : 'candles'; } catch (e) { /* sem preferência */ }
    setTypeButtons();
    $('btForm').addEventListener('submit', (ev) => { ev.preventDefault(); run(); });
    $('chartType').addEventListener('click', (ev) => {
        const type = ev.target.dataset?.type;
        if (!type || type === state.type) return;
        state.type = type;
        try { localStorage.setItem(CHART_KEY, type); } catch (e) { /* preferência não salva */ }
        setTypeButtons();
        applyChartType();
    });
    $('lotsBody').addEventListener('click', onLotClick);
    $('lotsBody').addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onLotClick(ev); } });

    // Endereço com ?ticker= (links do screener e da carteira): roda direto.
    const q = new URLSearchParams(location.search);
    if (q.get('ticker')) {
        $('btTicker').value = q.get('ticker');
        if (q.get('market')) $('btMarket').value = q.get('market') === 'us' ? 'us' : 'b3';
        if (q.get('target')) $('btTarget').value = q.get('target');
        if (q.get('fraction')) $('btFraction').value = q.get('fraction');
        if (q.get('from')) $('btFrom').value = q.get('from');
        if (q.get('to')) $('btTo').value = q.get('to');
        $('btStrict').checked = q.get('strict') === '1';
        run();
    }
}

function setTypeButtons() {
    for (const b of $('chartType').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.type === state.type));
}

function setMsg(text, kind = '') {
    $('btMsg').innerText = text || '';
    $('btMsg').className = `form-msg ${kind}`;
}

async function run() {
    const params = new URLSearchParams({
        ticker: $('btTicker').value.trim(), market: $('btMarket').value,
        target: $('btTarget').value, fraction: $('btFraction').value, strict: $('btStrict').checked ? '1' : '0'
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
    $('btTicker').value = data.ticker;
    // Datas possíveis para este ativo (do primeiro pregão com MM200 ao último fechado).
    for (const id of ['btFrom', 'btTo']) { $(id).min = data.bounds.min; $(id).max = data.bounds.max; }
    if (data.note) setMsg(data.note);
    history.replaceState(null, '', `?${new URLSearchParams({ ...Object.fromEntries(params), ticker: data.ticker })}`);
    $('btResult').hidden = false;
    render(data);
}

// ---------------------------------------------------------------- Renderização

function render(d) {
    $('btTitle').innerText = `${d.ticker} — ${d.name}`;
    $('btSubtitle').innerText = `${d.kindLabel} · ${d.period} · ${d.rule.text}${d.strict ? ' Com candle de rejeição.' : ''}`;
    $('btLegend').innerHTML = [
        ...Object.entries(MA_LABEL).map(([k, label]) => `<span><i class="swatch-line" style="background:${COLORS[k]}"></i>${label}</span>`),
        `<span><i class="swatch-dot" style="background:${COLORS.buy}"></i>C = compra</span>`,
        `<span><i class="swatch-dot" style="background:${COLORS.partial}"></i>P = venda parcial</span>`,
        `<span><i class="swatch-dot" style="background:${COLORS.exit}"></i>V = saída (regra B)</span>`
    ].join('');
    $('btChartNote').innerText = 'Soma do resultado de cada lote, em % do valor de um lote; a parte ainda aberta de cada lote é marcada pelo fechamento do dia.'
        + (d.adjusted ? ' Preços ajustados por proventos (fundo listado).' : '');
    drawCharts(d);
    renderKpis(d);
    renderLots(d);
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
    const price = state.price = LightweightCharts.createChart($('priceChart'), chartOptions());
    const s = state.series = {};
    s.candles = price.addCandlestickSeries({
        upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down
    });
    s.candles.setData(d.candles);
    s.line = price.addLineSeries({ color: COLORS.close, lineWidth: 2, priceLineVisible: false });
    s.line.setData(d.candles.map(c => ({ time: c.time, value: c.close })));
    for (const k of Object.keys(MA_LABEL)) {
        const ma = price.addLineSeries({ color: COLORS[k], lineWidth: k === 'mm200' ? 2 : 1, priceLineVisible: false,
                                         lastValueVisible: false, crosshairMarkerVisible: false });
        ma.setData(d.ma[k]);
    }
    applyChartType();

    const equity = state.equity = LightweightCharts.createChart($('equityChart'), chartOptions());
    const eq = equity.addBaselineSeries({
        baseValue: { type: 'price', price: -0.001 }, // resultado zero (antes da 1ª compra) fica na cor de cima
        topLineColor: COLORS.buy, topFillColor1: 'rgba(63, 185, 80, 0.25)', topFillColor2: 'rgba(63, 185, 80, 0.02)',
        bottomLineColor: COLORS.exit, bottomFillColor1: 'rgba(248, 81, 73, 0.02)', bottomFillColor2: 'rgba(248, 81, 73, 0.25)',
        priceFormat: { type: 'custom', formatter: (v) => `${fmtNum(v, 1)}%` }
    });
    eq.setData(d.equity);

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

// Candles ou linha de fechamento; os marcadores dos lotes ficam na série visível.
function applyChartType() {
    const s = state.series;
    if (!s.candles || !state.data) return;
    const candles = state.type === 'candles';
    s.candles.applyOptions({ visible: candles });
    s.line.applyOptions({ visible: !candles });
    const markers = state.data.markers.map(m => ({ time: m.time, text: m.text, ...MARKER[m.kind] }));
    s.candles.setMarkers(candles ? markers : []);
    s.line.setMarkers(candles ? [] : markers);
}

const pct = (v, d = 1) => v === null || v === undefined ? '—' : fmtPct(v, d);
const money = (v, currency) => currency === 'R$' ? fmtBRL(v) : `${currency} ${fmtNum(v, 2)}`;

function kpi(label, value, note = '', cls = '') {
    return `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value ${cls}">${value}</div><div class="kpi-note">${note}</div></div>`;
}

function renderKpis(d) {
    const s = d.stats;
    $('btKpis').innerHTML = [
        kpi('Lotes', fmtNum(s.lots, 0), `${s.closed} encerrado(s) · ${s.open} aberto(s) · ${s.exits} saída(s) pela regra B`),
        kpi('Taxa de acerto', s.winRate === null ? '—' : `${fmtNum(s.winRate, 0)}%`,
            `${s.wins} de ${s.closed} lotes encerrados no lucro · no ranking: ${fmtNum(s.winRateSmoothed, 0)}% (suavizada)`),
        kpi('Retorno médio por lote', pct(s.avgReturn), `mediana ${pct(s.medianReturn)} · lotes encerrados`, signClass(s.avgReturn)),
        kpi('Melhor e pior lote', `${pct(s.best, 0)} / ${pct(s.worst, 0)}`, 'lotes encerrados'),
        kpi('Soma dos resultados', pct(s.sumReturn, 0), 'todos os lotes, em % de um lote (abertos pelo último fechamento)', signClass(s.sumReturn)),
        kpi('Vendas parciais', d.rule.fraction > 0 ? `${s.partialHits} de ${s.lots}` : '—',
            d.rule.fraction > 0 ? `lotes que atingiram +${fmtNum(d.rule.target, 1)}%` : 'regra sem venda parcial'),
        kpi('Tempo médio', s.avgDays === null ? '—' : `${fmtNum(s.avgDays, 0)} pregões`, 'da compra à saída, lotes encerrados'),
        kpi('Devolvido do topo', s.avgGiveback === null ? '—' : `${fmtNum(s.avgGiveback, 1)}%`, 'média: do maior fechamento até a saída'),
        kpi('Alta máxima média', pct(s.avgMaxGain), 'maior máxima depois da compra, todos os lotes'),
        kpi('Comprar e manter', pct(s.buyHold), s.buyHoldFrom ? `da 1ª compra (${fmtDate(s.buyHoldFrom)}) até ${fmtDate(d.lastDate)}` : 'sem compras', signClass(s.buyHold))
    ].join('');
    const b = d.onlyB;
    $('btCompare').innerHTML = b ? `<strong>Só a regra B</strong> (sem venda parcial), no mesmo período: acerto ${b.winRate === null ? '—' : `${fmtNum(b.winRate, 0)}%`}`
        + ` · retorno médio ${pct(b.avgReturn)} · soma dos resultados ${pct(b.sumReturn, 0)} · ${b.closed} lote(s) encerrado(s).` : '';
}

function lotCell(point, currency) {
    if (!point) return '<span class="muted">—</span>';
    const ret = point.return === undefined ? '' : ` <span class="${signClass(point.return)}">${pct(point.return)}</span>`;
    return `${fmtDate(point.date)}<div class="small muted">${money(point.price, currency)}${ret}</div>`;
}

function renderLots(d) {
    $('lotsBody').innerHTML = d.lots.length ? d.lots.map(l => `
        <tr class="lot-row" tabindex="0" data-n="${l.n}" aria-label="Focar o lote ${l.n} no gráfico">
            <td class="c"><strong>${l.n}</strong></td>
            <td class="nowrap">${lotCell(l.entry, d.currency)}</td>
            <td class="nowrap">${lotCell(l.partial, d.currency)}</td>
            <td class="nowrap">${lotCell(l.exit, d.currency)}</td>
            <td class="r nowrap ${signClass(l.return)}"><strong>${pct(l.return)}</strong></td>
            <td class="r">${fmtNum(l.days, 0)}</td>
            <td class="r nowrap">${pct(l.maxGain)}</td>
            <td class="r nowrap">${l.giveback === null ? '—' : `${fmtNum(l.giveback, 1)}%`}</td>
            <td><span class="badge ${l.open ? 'badge-fill' : 'badge-low'}">${escapeHTML(l.status)}</span></td>
        </tr>`).join('')
        : '<tr><td colspan="9" class="empty-row">Nenhum sinal do setup no período.</td></tr>';
}

function onLotClick(ev) {
    const row = ev.target.closest?.('tr[data-n]');
    if (!row || !state.data || !state.price) return;
    const lot = state.data.lots.find(l => String(l.n) === row.dataset.n);
    for (const r of $('lotsBody').querySelectorAll('tr.selected')) r.classList.remove('selected');
    row.classList.add('selected');
    state.price.timeScale().setVisibleRange(lot.range);
    $('priceChart').scrollIntoView({ behavior: 'smooth', block: 'center' });
}
