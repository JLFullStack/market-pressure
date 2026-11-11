// Carteira → Visão geral: KPIs, evolução do patrimônio, rentabilidade acumulada, alocação por
// classe, posições e o card flutuante "Operações no gráfico" (linha das Posições). Usa carteira.js.

//#region Constantes e eventos
const PERIODS = ['6M', '1A', '2A', 'Tudo']; // os mesmos do servidor (portfolio.PERIODS)
const PATRIMONY_PERIODS = ['30D', '1A', '2A', '5A', '10A', 'Tudo']; // os mesmos do servidor (portfolio.PATRIMONY_PERIODS)
const PATRIMONY_MODES = { valor: 'Aplicado e ganho', classes: 'Por classe' };
// Cores fixas por série, nesta ordem (sobre o fundo #0e1527 do painel).
const SERIES = [
    { key: 'portfolio', label: 'Carteira', color: '#5aa2ff' },
    { key: 'cdi', label: 'CDI', color: '#ff8a5b' },
    { key: 'bova', label: 'BOVA11', color: '#4ad991' }
];
// Barras da evolução do patrimônio: pedaços empilhados (portfolio.patrimony_bars), de baixo para cima.
const PATRIMONY_SERIES = [
    { key: 'base', label: 'Valor aplicado', color: '#4f8dff' },
    { key: 'up', label: 'Ganho de capital', color: '#3ddc97' },
    { key: 'down', label: 'Perda de capital', color: '#ff7b72' }
];
// Indicação da posição (COMPRAR, VENDER, MANTER; portfolio.position_signal: statusKey) → badge.
const STATUS_BADGE = { sell: 'badge-hot', partial: 'badge-hot', buy: 'badge-ok', attention: 'badge-fill', hold: 'badge-info' };

// Botões de período e de modo dos gráficos, o "Lançar primeira operação" e o card do gráfico.
function setupVisaoGeral() {
    $('periodButtons').innerHTML = PERIODS.map(label =>
        `<button type="button" data-period="${label}" aria-pressed="${label === state.period}">${label}</button>`).join('');
    $('patrimonyPeriods').innerHTML = PATRIMONY_PERIODS.map(label =>
        `<button type="button" data-period="${label}" aria-pressed="${label === state.patrimonyPeriod}">${label}</button>`).join('');
    $('patrimonyPeriods').addEventListener('click', (ev) => {
        const period = ev.target.dataset?.period;
        if (!period) return;
        state.patrimonyPeriod = period;
        for (const b of $('patrimonyPeriods').querySelectorAll('button')) b.setAttribute('aria-pressed', b.dataset.period === period);
        renderPatrimony();
    });
    $('patrimonyClass').addEventListener('change', (ev) => { state.patrimonyClass = ev.target.value; renderPatrimony(); });
    $('patrimonyModes').innerHTML = Object.entries(PATRIMONY_MODES).map(([k, label]) =>
        `<button type="button" data-mode="${k}" aria-pressed="${k === state.patrimonyMode}">${label}</button>`).join('');
    $('patrimonyModes').addEventListener('click', (ev) => {
        const mode = ev.target.dataset?.mode;
        if (!mode) return;
        state.patrimonyMode = mode;
        for (const b of $('patrimonyModes').querySelectorAll('button')) b.setAttribute('aria-pressed', b.dataset.mode === mode);
        renderPatrimony();
    });
    $('periodButtons').addEventListener('click', (ev) => {
        const period = ev.target.dataset?.period;
        if (!period) return;
        state.period = period;
        for (const b of $('periodButtons').querySelectorAll('button')) b.setAttribute('aria-pressed', b.dataset.period === period);
        renderChart();
    });
    $('firstOpBtn').addEventListener('click', () => { goTab('aportes'); $('txTicker').focus(); });
    setupAssetChart();
}
//#endregion

//#region Resumo (KPIs)
// Cor do cartão pelo sinal do valor (verde, vermelho, ou o tom neutro quando não há valor).
function toneBySign(card, v, neutral = 'tone-blue') {
    card.classList.remove('tone-green', 'tone-red', 'tone-blue', 'tone-cyan', 'tone-violet');
    card.classList.add(v === null || v === undefined ? neutral : v >= 0 ? 'tone-green' : 'tone-red');
}

// Carteira × CDI × BOVA11 num período: uma barra por série, proporcional ao maior retorno em
// módulo (as negativas em vermelho), com o valor ao lado.
function compareBars(r) {
    if (!r) return '';
    const rows = SERIES.map(s => ({ ...s, v: r[s.key] }));
    const max = Math.max(...rows.map(x => Math.abs(x.v)), 0.0001);
    return `<div class="cmp">${rows.map((x, k) => `<div class="cmp-row">
            <span class="cmp-label">${x.label}</span>
            <span class="cmp-track"><i style="--w:${(Math.abs(x.v) / max).toFixed(3)};--c:${x.v < 0 ? 'var(--danger)' : x.color};--k:${k}"></i></span>
            <span class="cmp-val num ${signClass(x.v)}">${fmtPct(x.v, 2)}</span></div>`).join('')}
        <div class="small muted">desde ${fmtDate(r.from)}</div></div>`;
}

function renderKpis(a) {
    const k = a.kpis;
    const delta = (id, v) => { $(id).innerText = v === null || v === undefined ? '' : fmtPct(v, 2); $(id).className = `kpi-delta ${signClass(v)}`; };
    setCount($('kpiValue'), k.total, { format: 'brl' });
    $('kpiValueNote').innerText = `${a.open.length} ativo(s) e ${a.fixed.length} título(s) de renda fixa`;
    const months = a.patrimony?.groups.find(g => g.key === '')?.bars.slice(-24).map(b => b.value) || [];
    drawSpark($('patrimonySpark'), months, months.length < 2 || months.at(-1) >= months[0]);
    setCount($('kpiInvested'), k.invested, { format: 'brl' });
    setCount($('kpiProfit'), k.profit, { format: 'sbrl' });
    delta('kpiProfitPct', k.profitPct);
    toneBySign($('kpiProfitCard'), k.profit);
    $('kpiProfitNote').innerText = `Valorização ${fmtBRL(k.unrealized)} · proventos ${fmtBRL(k.dividendsTotal)} · realizado ${fmtBRL(k.realized)}`;
    setCount($('kpiDay'), k.day ? k.day.value : null, { format: 'sbrl' });
    delta('kpiDayPct', k.day ? k.day.pct : null);
    toneBySign($('kpiDayCard'), k.day ? k.day.value : null);
    $('kpiDayNote').innerText = k.day ? `Pregão de ${fmtDate(k.day.date)}` : '';
    setCount($('kpiTwrAll'), k.sinceStart ? k.sinceStart.portfolio : null, { format: 'pct', decimals: 2 });
    toneBySign($('kpiTwrAllCard'), k.sinceStart ? k.sinceStart.portfolio : null);
    $('kpiTwrAllNote').innerHTML = compareBars(k.sinceStart);
    setCount($('kpiTwr12'), k.last12 ? k.last12.portfolio : null, { format: 'pct', decimals: 2 });
    toneBySign($('kpiTwr12Card'), k.last12 ? k.last12.portfolio : null);
    $('kpiTwr12Note').innerHTML = compareBars(k.last12);
}

// ---------------------------------------------------------------- Aporte
//#endregion

//#region Evolução do patrimônio
const fmtGainPct = (v) => v == null ? '' : ` (${v > 0 ? '+' : ''}${fmtNum(v)}%)`;

// Evolução do patrimônio: barras (mensais, ou diárias em 30D) filtradas por período e classe, em
// dois modos: "Aplicado e ganho" (valor aplicado + ganho ou perda de capital empilhados) e "Por
// classe" (o patrimônio de cada classe empilhado). Tudo vem calculado do servidor (analise.patrimony).
function renderPatrimony() {
    const p = state.analysis?.patrimony;
    const select = $('patrimonyClass');
    state.patrimonyChart?.destroy(); state.patrimonyChart = null; // os modos têm séries diferentes
    if (!p) {
        select.innerHTML = '';
        $('patrimonySummary').innerHTML = '';
        return;
    }
    const byClass = state.patrimonyMode === 'classes';
    if (!p.groups.some(g => g.key === state.patrimonyClass)) state.patrimonyClass = '';
    select.innerHTML = p.groups.map(g => `<option value="${escapeHTML(g.key)}">${escapeHTML(g.label)}</option>`).join('');
    select.value = byClass ? '' : state.patrimonyClass;
    select.disabled = byClass; // no modo por classe, todas aparecem empilhadas
    const group = p.groups.find(g => g.key === (byClass ? '' : state.patrimonyClass));
    const classes = p.groups.filter(g => g.key);
    // 30D: uma barra por pregão; os demais, uma por mês.
    const period = p.periods[state.patrimonyPeriod] || { daily: false, start: 0 };
    const barsOf = (g) => (period.daily ? g.dayBars : g.bars).slice(period.start);
    const bars = barsOf(group);
    const labels = period.daily ? p.days.slice(period.start).map(d => `${d.slice(8, 10)}/${d.slice(5, 7)}`)
        : p.months.slice(period.start).map(fmtMonth);
    renderPatrimonySummary(group.summary[state.patrimonyPeriod], byClass ? classes : []);
    const last = bars.at(-1);
    $('patrimonyChart').setAttribute('aria-label', `${group.label}, de ${labels[0]} a ${labels.at(-1)}: patrimônio ${fmtBRL(last.value)}, aplicado ${fmtBRL(last.invested)}, ganho de capital ${fmtBRL(last.gain)}`);
    if (typeof Chart === 'undefined') return;

    const flowLabel = period.daily ? 'Aportes líquidos no dia' : 'Aportes líquidos no mês';
    const series = byClass
        ? classes.map((g, k) => ({ label: g.label, color: classColor(g.key, k), data: barsOf(g).map(b => b.value) }))
        : PATRIMONY_SERIES.map(s => ({ ...s, data: bars.map(b => b[s.key]) }));
    const classBars = classes.map(barsOf);
    const tooltipLines = (i) => {
        const b = bars[i];
        const lines = [` Patrimônio: ${fmtBRL(b.value)}`];
        if (byClass) {
            classes.forEach((g, k) => {
                const c = classBars[k][i];
                if (c.value > 0.004 || Math.abs(c.flow) > 0.004)
                    lines.push(` ${g.label}: ${fmtBRL(c.value)} · ganho ${signedBRL(c.gain)}${fmtGainPct(c.gainPct)}`);
            });
        } else {
            lines.push(` Valor aplicado: ${fmtBRL(b.invested)}`, ` Ganho de capital: ${signedBRL(b.gain)}${fmtGainPct(b.gainPct)}`);
        }
        lines.push(` ${flowLabel}: ${signedBRL(b.flow)}`);
        return lines;
    };
    const text = cssVar('--text-color'), muted = cssVar('--text-muted'), grid = CHART_GRID;
    // Criado quando o painel aparece na tela (a animação de entrada acontece à vista); um novo
    // desenho antes disso substitui o que estava esperando.
    whenVisible('patrimonyChart', () => {
    state.patrimonyChart?.destroy();
    state.patrimonyChart = new Chart($('patrimonyChart'), {
        type: 'bar',
        data: {
            labels,
            datasets: series.map(s => ({
                label: s.label, data: s.data, backgroundColor: verticalGradient(s.color, 0.95, 0.55), hoverBackgroundColor: s.color,
                borderRadius: 5, borderSkipped: false, maxBarThickness: 36, stack: 'patrimonio'
            }))
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: CHART_ENTER,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    labels: {
                        color: text, usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 12,
                        // Só as séries com valor no período (ex.: "Perda de capital" só quando há perda).
                        filter: (item, d) => d.datasets[item.datasetIndex].data.some(v => v > 0)
                    }
                },
                tooltip: {
                    ...chartTooltip(), displayColors: false,
                    filter: (item) => item.datasetIndex === 0, // um bloco por barra, montado abaixo
                    callbacks: { label: (ctx) => tooltipLines(ctx.dataIndex) }
                }
            },
            scales: {
                x: { stacked: true, ticks: { color: muted, maxRotation: 0, autoSkip: true }, grid: { display: false }, border: { color: grid } },
                y: { stacked: true, beginAtZero: true, ticks: { color: muted, callback: (v) => fmtCompactBRL(v) }, grid: { color: grid }, border: { display: false } }
            }
        }
    });
    });
}

// Resumo do período acima do gráfico: de onde veio a variação do patrimônio (aportes ou
// valorização). No modo por classe, também a valorização de cada classe no período.
function renderPatrimonySummary(s, classes) {
    const kpi = (label, value, note = '', cls = '') => `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value ${cls}">${value}</div><div class="kpi-note">${note}</div></div>`;
    const change = s.endValue - s.startValue;
    let html = `<div class="kpis patrimony-kpis">${[
        kpi('Patrimônio no fim do período', fmtBRL(s.endValue),
            s.startDate ? `era ${fmtBRL(s.startValue)} em ${fmtDate(s.startDate)} · ${signedBRL(change)}` : 'desde o primeiro lançamento'),
        kpi('Aportes líquidos', signedBRL(s.flows), 'compras e aportes − vendas e resgates'),
        kpi('Valorização', signedBRL(s.gain), 'variação − aportes (sem proventos)', signClass(s.gain))
    ].join('')}</div>`;
    const shown = classes.map((g, k) => ({ g, k, s: g.summary[state.patrimonyPeriod] }))
        .filter(c => c.s.endValue > 0.004 || Math.abs(c.s.gain) > 0.004);
    if (shown.length) {
        html += `<p class="patrimony-classes"><span class="muted">Valorização por classe no período:</span> ${shown.map(({ g, k, s: cs }) =>
            `<span class="nowrap"><span class="swatch-box" style="background:${classColor(g.key, k)}"></span>${escapeHTML(g.label)} <strong class="${signClass(cs.gain)}">${signedBRL(cs.gain)}</strong></span>`
        ).join(' · ')}</p>`;
    }
    $('patrimonySummary').innerHTML = html;
}
//#endregion

//#region Rentabilidade acumulada
function renderChart() {
    const chart = state.analysis?.chart;
    const note = $('chartNote');
    if (!chart) {
        note.innerText = 'A rentabilidade aparece a partir do primeiro pregão após a primeira operação.';
        state.chart?.destroy(); state.chart = null;
        return;
    }
    // Séries já reiniciadas em 0% no início do período pelo servidor.
    const period = chart.periods[state.period];
    const labels = period.labels;
    const data = SERIES.map(s => period[s.key]);
    const finals = SERIES.map((s, k) => `${s.label} ${fmtPct(data[k].at(-1), 2)}`).join(', ');
    $('returnChart').setAttribute('aria-label', `Rentabilidade acumulada de ${fmtDate(labels[0])} a ${fmtDate(labels.at(-1))}: ${finals}`);
    note.innerText = (chart.truncated
        ? `Há operações anteriores ao início do histórico disponível (${fmtDate(chart.firstDay)}): elas entram pelo preço informado e a rentabilidade começa nessa data. `
        : '') + `No período: ${finals}.`;

    if (typeof Chart === 'undefined') {
        note.innerText = 'Gráfico indisponível (não foi possível carregar o Chart.js). ' + note.innerText;
        return;
    }
    const text = cssVar('--text-color'), muted = cssVar('--text-muted'), grid = CHART_GRID;
    // A carteira em destaque (linha mais grossa com área em degradê); CDI e BOVA11 tracejados.
    const datasets = SERIES.map((s, k) => ({
        label: s.label, data: data[k], borderColor: s.color, backgroundColor: k === 0 ? verticalGradient(s.color, 0.28, 0) : s.color,
        fill: k === 0 ? 'origin' : false, borderDash: k === 0 ? [] : [5, 4],
        borderWidth: k === 0 ? 2.5 : 1.6, pointRadius: 0, pointHoverRadius: 5, pointHoverBorderWidth: 2,
        pointHoverBackgroundColor: cssVar('--panel-bg'), pointHitRadius: 6, tension: 0.15
    }));
    const xLabels = labels.map(d => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(2, 4)}`);
    if (state.chart) {
        state.chart.data.labels = xLabels;
        state.chart.data.datasets.forEach((ds, k) => { ds.data = data[k]; });
        state.chart.update();
        return;
    }
    whenVisible('returnChart', () => {
    if (state.chart) return;
    state.chart = new Chart($('returnChart'), {
        type: 'line',
        data: { labels: xLabels, datasets },
        plugins: [crosshairPlugin],
        options: {
            responsive: true, maintainAspectRatio: false, animation: { duration: 1100, easing: 'easeOutCubic' },
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: text, usePointStyle: true, pointStyle: 'line', boxWidth: 24 } },
                tooltip: {
                    ...chartTooltip(), usePointStyle: true,
                    callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtPct(ctx.parsed.y, 2)}` }
                }
            },
            scales: {
                x: { ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false }, border: { color: grid } },
                y: { ticks: { color: muted, callback: (v) => `${fmtNum(v, 0)}%` }, grid: { color: grid }, border: { display: false } }
            }
        }
    });
    });
}

// ---------------------------------------------------------------- Alocação e classes
//#endregion

//#region Alocação por classe
// Visão geral: alocação por classe só para leitura (a edição fica em Configurações).
function renderAllocation(a) {
    $('allocationViewBody').innerHTML = a.allocation.map((r, k) => `
        <tr>
            <td class="class-cell"><span class="swatch-box" style="background:${classColor(r.cls, k)}"></span><strong>${escapeHTML(r.cls)}</strong></td>
            <td class="r nowrap">${fmtBRL(r.value)}</td>
            <td class="r nowrap">${fmtNum(r.pct)}%</td>
            <td class="r nowrap">${fmtNum(r.target)}%</td>
            <td class="r nowrap ${signClass(r.diff)}">${r.diff >= 0 ? '+' : ''}${fmtNum(r.diff)} p.p.</td>
            <td class="r nowrap">${Math.abs(r.toTarget) < 0.005 ? '—' : r.toTarget > 0 ? `aportar ${fmtBRL(r.toTarget)}` : `excede ${fmtBRL(-r.toTarget)}`}</td>
            <td>${allocBar(r.cls, r.pct, r.target)}</td>
        </tr>`).join('') + `
        <tr><td><strong>Total</strong></td><td class="r nowrap"><strong>${fmtBRL(a.kpis.total)}</strong></td><td class="r">100%</td>
            <td class="r nowrap"><strong>${fmtNum(a.targetSum)}%</strong></td><td colspan="3"></td></tr>`;
    revealItems($('allocationViewBody'));
    $('allocationNote').innerHTML = (a.targetsOff ? `<span class="warn-text">As metas somam ${fmtNum(a.targetSum)}%, não 100%.</span> ` : '')
        + 'Barra: preenchimento = % atual; traço = % meta.';
    renderAllocationDonut(a);
}

// Rosca da alocação: anel de fora = % de hoje, anel de dentro = % meta, uma cor por classe; o
// patrimônio total no meio.
function renderAllocationDonut(a) {
    setCount($('allocationTotal'), a.kpis.total, { format: 'brl' });
    if (typeof Chart === 'undefined') return;
    const rows = a.allocation;
    const colors = rows.map((r, k) => classColor(r.cls, k));
    whenVisible('allocationDonut', () => {
        state.allocationChart?.destroy();
        state.allocationChart = new Chart($('allocationDonut'), {
            type: 'doughnut',
            data: {
                labels: rows.map(r => r.cls),
                datasets: [
                    { label: 'Hoje', data: rows.map(r => r.pct), backgroundColor: colors, borderColor: cssVar('--panel-bg'), borderWidth: 3, hoverOffset: 8, weight: 1.4 },
                    { label: 'Meta', data: rows.map(r => r.target), backgroundColor: colors.map(c => hexAlpha(c, 0.4)), borderColor: cssVar('--panel-bg'), borderWidth: 2, weight: 0.8 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: '58%',
                animation: { animateRotate: true, animateScale: false, duration: 1100, easing: 'easeOutQuart' },
                plugins: {
                    legend: { display: false },
                    tooltip: { ...chartTooltip(), callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtNum(ctx.parsed)}%` } }
                }
            }
        });
    });
}
//#endregion

//#region Posições
const tradingViewSymbol = (ticker, market) => market === 'us' ? ticker : market === 'crypto' ? `${ticker}USD` : `BMFBOVESPA:${ticker}`;

// Peso na carteira como barra, proporcional à maior posição (a maior enche a barra).
const weightCell = (w, max) => `<div class="weight"><span class="num">${fmtNum(w)}%</span><span class="weight-bar"><i style="--w:${(max ? w / max : 0).toFixed(3)}"></i></span></div>`;

const deltaPill = (v) => v === null || v === undefined ? '—' : `<span class="delta-pill ${signClass(v)}">${fmtPct(v, 2)}</span>`;

function renderPositions(a) {
    const maxWeight = Math.max(0, ...a.open.map(p => p.weight), ...a.fixed.map(f => f.weight));
    const classIndex = (cls) => Math.max(0, a.allocation.findIndex(r => r.cls === cls));
    const avatar = (label, cls) => `<span class="avatar solo" style="--a1:${classColor(cls, classIndex(cls))}" aria-hidden="true">${escapeHTML(label.slice(0, 2).toUpperCase())}</span>`;
    const rows = a.open.map(p => `
        <tr data-reveal class="position-row" tabindex="0" data-ticker="${escapeHTML(p.ticker)}" aria-label="Abrir o gráfico de ${escapeHTML(p.ticker)} com as operações e os alvos">
            <td><div class="asset">${avatar(p.ticker, p.cls)}<div class="asset-text"><a href="https://br.tradingview.com/chart/?symbol=${encodeURIComponent(tradingViewSymbol(p.ticker, p.market))}" target="_blank" rel="noopener" class="ticker-link">${p.ticker}</a>
                ${p.market === 'crypto' ? '' : `<a href="backtest.html?ticker=${encodeURIComponent(p.ticker)}&market=${p.market}" class="inline-link small" aria-label="Backtest de ${p.ticker}">backtest</a>`}
                <div class="small muted">${escapeHTML(p.cls)}${p.market === 'b3' ? '' : ` · ${MARKETS[p.market].label}`}</div></div></div></td>
            <td class="r nowrap">${fmtQty(p.quantity)}</td>
            <td class="r nowrap">${fmtBRL(p.avgPrice)}</td>
            <td class="r nowrap">${fmtBRL(p.price)}${p.hasQuote ? '' : '<div class="small muted">sem cotação</div>'}${p.market !== 'b3' && p.hasQuote ? `<div class="small muted">${fmtMoney(p.priceNative, p.currency)} × ${fmtNum(p.fx, 4)}</div>` : ''}</td>
            <td class="r nowrap ${signClass(p.dayChange)}">${p.dayChange === null ? '—' : fmtPct(p.dayChange, 2)}</td>
            <td class="r nowrap">${fmtBRL(p.value)}</td>
            <td class="r nowrap ${signClass(p.profit)}">${fmtBRL(p.profit)}</td>
            <td class="r nowrap">${deltaPill(p.returnPct)}</td>
            <td class="r nowrap">${fmtBRL(p.dividends)}</td>
            <td class="r nowrap">${weightCell(p.weight, maxWeight)}</td>
            <td class="c">${p.status ? signalBadge(p.status, p.statusReason, STATUS_BADGE[p.statusKey] || 'badge-low') : '<span class="small muted">—</span>'}
                ${p.partial ? `<div class="small muted nowrap">${fmtQty(p.partial.quantity)} ${p.partial.quantity === 1 ? 'cota' : 'cotas'} de ${p.partial.lots} compra(s)</div>` : ''}</td>
        </tr>`);
    const fixedRows = a.fixed.map(f => `
        <tr class="fi-section" data-reveal>
            <td><div class="asset">${avatar(f.name, f.cls)}<div class="asset-text"><strong>${escapeHTML(f.name)}</strong><div class="small muted">${escapeHTML(f.cls)}</div></div></div></td>
            <td colspan="4" class="small muted">Investido líquido ${fmtBRL(f.invested)} · valor atualizado em ${fmtDate(f.lastUpdate)}</td>
            <td class="r nowrap">${fmtBRL(f.value)}</td>
            <td class="r nowrap ${signClass(f.profit)}">${fmtBRL(f.profit)}</td>
            <td class="r nowrap">${deltaPill(f.returnPct)}</td>
            <td class="r muted">—</td>
            <td class="r nowrap">${weightCell(f.weight, maxWeight)}</td>
            <td class="c"><span class="small muted">—</span></td>
        </tr>`);
    $('positionsBody').innerHTML = rows.length || fixedRows.length ? [...rows, ...fixedRows].join('')
        : `<tr><td colspan="11" class="empty-row">Nenhuma posição aberta.</td></tr>`;
    revealItems($('positionsBody'));
}

// ---------------------------------------------------------------- Operações no gráfico (card flutuante)
// Clicar numa linha das Posições abre o gráfico do ativo com as compras (C) e vendas (V) lançadas
// e os alvos das compras abertas (GET /api/carteira/{id}/grafico). Para o gráfico não ficar
// carregado, os alvos próximos já vêm agrupados do servidor e só o mais próximo aparece de início;
// os demais se ligam pelos chips abaixo do gráfico.
//#endregion

//#region Card "Operações no gráfico"
function setupAssetChart() {
    try { state.chartType = localStorage.getItem(CHART_KEY) === 'line' ? 'line' : 'candles'; } catch (e) { /* sem preferência */ }
    const open = (ev) => {
        if (ev.target.closest?.('a, button, .tip')) return; // links, selo da situação e ajuda seguem como antes
        const row = ev.target.closest?.('tr[data-ticker]');
        if (row) openAssetChart(row.dataset.ticker);
    };
    $('positionsBody').addEventListener('click', open);
    $('positionsBody').addEventListener('keydown', (ev) => {
        if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches?.('tr[data-ticker]')) { ev.preventDefault(); open(ev); }
    });
    const dlg = $('assetChartDialog');
    $('assetChartClose').addEventListener('click', () => dlg.close());
    // Toque fora do card (no fundo escurecido) fecha.
    dlg.addEventListener('click', (ev) => {
        const r = dlg.getBoundingClientRect();
        if (ev.target === dlg && (ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom)) dlg.close();
    });
    dlg.addEventListener('close', () => {
        state.assetSeq++;
        state.assetPrice?.chart.remove();
        state.assetPrice = null;
        state.assetLines = [];
        const tipBox = $('tipBox');
        if (tipBox && tipBox.parentElement === dlg) { tipBox.hidden = true; document.body.appendChild(tipBox); }
        state.assetRow?.focus();
    });
    setTypeButtons();
    $('assetChartType').addEventListener('click', (ev) => {
        const type = ev.target.dataset?.type;
        if (!type || type === state.chartType) return;
        state.chartType = type;
        try { localStorage.setItem(CHART_KEY, type); } catch (e) { /* preferência não salva */ }
        setTypeButtons();
        if (state.assetPrice) { showPriceType(state.assetPrice, state.asset.markers, type); drawTargets(); }
    });
    $('assetTargetMode').addEventListener('click', (ev) => {
        const mode = ev.target.dataset?.mode;
        if (!mode || !state.asset) return;
        const all = state.asset.targets.map((t, k) => k);
        state.targetsShown = new Set(mode === 'all' ? all : mode === 'next' ? nextTarget() : []);
        drawTargets();
    });
    $('assetTargetChips').addEventListener('click', (ev) => {
        const chip = ev.target.closest?.('button[data-k]');
        if (!chip) return;
        const k = Number(chip.dataset.k);
        if (state.targetsShown.has(k)) state.targetsShown.delete(k); else state.targetsShown.add(k);
        drawTargets();
    });
}

function setTypeButtons() {
    for (const b of $('assetChartType').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.type === state.chartType));
}

async function openAssetChart(ticker) {
    const dlg = $('assetChartDialog');
    const seq = ++state.assetSeq;
    state.assetRow = document.activeElement?.closest?.('tr[data-ticker]') || $('positionsBody').querySelector(`tr[data-ticker="${CSS.escape(ticker)}"]`);
    state.assetPrice?.chart.remove();
    state.assetPrice = null;
    state.asset = null;
    $('assetChartTitle').innerText = ticker;
    $('assetChartInfo').innerHTML = '';
    $('assetChartStats').innerHTML = '';
    $('assetChartAvatar').textContent = ticker.slice(0, 2).toUpperCase();
    $('assetChartLegend').innerHTML = '';
    $('assetTargets').hidden = true;
    $('assetChartMsg').innerText = `Carregando ${ticker}...`;
    if (!dlg.open) dlg.showModal();
    // A caixa de ajuda flutuante precisa estar dentro do card (camada de cima do diálogo).
    if ($('tipBox')) dlg.appendChild($('tipBox'));
    let data;
    try {
        data = await apiGet(`/carteira/${encodeURIComponent(state.currentId)}/grafico?ticker=${encodeURIComponent(ticker)}`);
    } catch (e) {
        if (seq === state.assetSeq) $('assetChartMsg').innerText = `Não foi possível carregar o gráfico de ${ticker} (${e.message}).`;
        return;
    }
    if (seq !== state.assetSeq || !dlg.open) return;
    state.asset = data;
    state.targetsShown = new Set(nextTarget());
    state.assetLines = [];
    const rule = data.rule.fraction > 0 ? `${data.ruleLabel} (${fmtNum(data.rule.fraction, 0)}% em +${fmtNum(data.rule.target, 1)}%)` : data.ruleLabel;
    const k = Math.max(0, (state.analysis?.allocation || []).findIndex(r => r.cls === data.cls));
    const color = classColor(data.cls, k);
    $('assetChartAvatar').style.setProperty('--a1', color);
    $('assetChartDialog').style.setProperty('--g', color);
    // Informações em pílulas: nome, classe (na cor dela), lançamentos e compras abertas, regra de venda.
    $('assetChartInfo').innerHTML = `<span class="chips">${[
        data.name !== data.ticker ? `<span class="chip">${escapeHTML(data.name)}</span>` : '',
        `<span class="chip chip-class"><span class="swatch-box" style="background:${color}"></span>${escapeHTML(data.cls)}</span>`,
        `<span class="chip"><strong>${data.transactions}</strong> lançamento(s) · <strong>${data.openLots}</strong> compra(s) aberta(s)</span>`,
        `<span class="chip">${escapeHTML(rule)}</span>`].join('')}</span>`;
    renderAssetStats(data);
    $('assetChartLegend').innerHTML = priceLegend([[COLORS.buy, 'C = compra'], [COLORS.exit, 'V = venda'],
        ...(data.targets.length ? [[COLORS.target, 'Alvo da venda parcial']] : [])]);
    $('assetChartMsg').innerText = data.note || '';
    state.assetPrice = priceChart($('assetChartBox'), data);
    showPriceType(state.assetPrice, data.markers, state.chartType);
    if (data.range) state.assetPrice.chart.timeScale().setVisibleRange(data.range);
    else state.assetPrice.chart.timeScale().fitContent();
    drawTargets();
    keepTipsWithText($('assetTargets'));
    // Entrada: o gráfico se desenha da esquerda para a direita a cada ativo aberto.
    const box = $('assetChartBox');
    box.classList.remove('drawing'); void box.offsetWidth; box.classList.add('drawing');
}

// Faixa de números do card: preço de agora, preço médio, resultado da posição (das Posições, já
// calculado no Python), o próximo alvo e a situação. Os valores em reais e % contam ao abrir.
function renderAssetStats(data) {
    const p = (state.analysis?.open || []).find(x => x.ticker === data.ticker);
    const next = data.nextTarget === null || data.nextTarget === undefined ? null : data.targets[data.nextTarget];
    const stat = (label, value, note = '', tone = '') => `<div class="dlg-stat ${tone}"><span class="kpi-label">${label}</span>
        <span class="dlg-stat-value num">${value}</span>${note ? `<span class="kpi-note">${note}</span>` : ''}</div>`;
    const counter = (v, f, d = 2) => `<span data-count="${v}" data-format="${f}" data-decimals="${d}">${COUNT_FORMAT[f](0, d)}</span>`;
    const cards = [stat('Preço atual', fmtMoney(data.lastClose, data.currency), data.currency === 'R$' ? 'último fechamento' : `em ${data.currency}`, 'tone-blue')];
    if (p) {
        cards.push(stat('Preço médio', counter(p.avgPrice, 'brl'), `${fmtQty(p.quantity)} ${p.quantity === 1 ? 'cota' : 'cotas'}`, 'tone-cyan'));
        cards.push(stat('Resultado', p.returnPct === null ? '—' : counter(p.returnPct, 'pct', 2), `lucro ${fmtBRL(p.profit)}`,
            p.returnPct === null ? 'tone-violet' : p.returnPct >= 0 ? 'tone-green' : 'tone-red'));
    }
    if (next) cards.push(stat(next.reached ? 'Alvo atingido' : 'Próximo alvo', fmtMoney(next.price, data.currency),
        next.reached ? 'parte ainda não vendida' : next.distance === null ? '' : `${fmtPct(next.distance, 1)} do preço atual`, 'tone-amber'));
    if (p?.status) cards.push(`<div class="dlg-stat tone-violet"><span class="kpi-label">Situação</span>
        <span class="dlg-stat-badge">${signalBadge(p.status, p.statusReason, STATUS_BADGE[p.statusKey] || 'badge-low')}</span></div>`);
    $('assetChartStats').innerHTML = cards.join('');
    $('assetChartStats').querySelectorAll('[data-count]').forEach(countUp);
}

// O alvo mostrado de início ("Próximo"): o mais próximo ainda não atingido, escolhido no servidor.
const nextTarget = () => state.asset?.nextTarget === null || state.asset?.nextTarget === undefined ? [] : [state.asset.nextTarget];

// Linhas de alvo (tracejadas, na série visível) e os chips. Com mais de 3 alvos à mostra, só o
// próximo leva o rótulo na linha; os outros ficam só com o preço no eixo. Alvo já atingido (parte
// ainda não vendida): linha pontilhada e mais apagada.
function drawTargets() {
    const d = state.asset;
    if (!d || !state.assetPrice) return;
    for (const { series, line } of state.assetLines) series.removePriceLine(line);
    state.assetLines = [];
    const series = state.chartType === 'candles' ? state.assetPrice.candles : state.assetPrice.line;
    const shown = [...state.targetsShown].sort((a, b) => a - b);
    const main = shown.includes(d.nextTarget) ? d.nextTarget : shown[0];
    shown.forEach((k) => {
        const t = d.targets[k];
        const label = k === main || shown.length <= 3;
        const line = series.createPriceLine({
            price: t.price, color: hexAlpha(COLORS.target, t.reached ? 0.45 : k === main ? 0.9 : 0.55), lineWidth: 1,
            lineStyle: t.reached ? LightweightCharts.LineStyle.Dotted : LightweightCharts.LineStyle.Dashed, axisLabelVisible: true,
            title: label ? `${t.reached ? 'Alvo atingido' : 'Alvo'}${t.lots.length > 1 ? ` (${t.lots.length})` : ''}` : ''
        });
        state.assetLines.push({ series, line });
    });
    $('assetTargets').hidden = !d.targets.length;
    const count = d.targets.length;
    const mode = shown.length === 0 ? 'none' : shown.length === count && count > 1 ? 'all' : shown.length === 1 && shown[0] === d.nextTarget ? 'next' : '';
    for (const b of $('assetTargetMode').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
    $('assetTargetMode').querySelector('[data-mode="all"]').hidden = count < 2;
    $('assetTargetChips').innerHTML = d.targets.map((t, k) => `
        <button type="button" class="price-target${t.reached ? ' reached' : ''}" data-k="${k}" aria-pressed="${state.targetsShown.has(k)}"
            data-tip-title="Alvo em ${escapeHTML(fmtMoney(t.price, d.currency))}" data-tip="${escapeHTML(t.detail)}">
            <strong>${fmtMoney(t.price, d.currency)}</strong>
            ${t.reached ? '<span class="price-target-tag">atingido</span>' : `<span class="pos">${t.distance === null ? '' : fmtPct(t.distance, 1)}</span>`}
            <span class="muted">${t.lots.length} compra${t.lots.length > 1 ? 's' : ''}</span>
        </button>`).join('');
}
//#endregion
