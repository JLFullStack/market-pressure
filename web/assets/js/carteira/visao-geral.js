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
    bindChartRows($('positionsTables'), openPositionChart);
    // ★ das Posições: adiciona ou tira o ativo dos favoritos.
    $('positionsTables').addEventListener('click', (ev) => {
        const star = ev.target.closest?.('.fav-star');
        if (star) onFavoriteStar(star);
    });
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

//#region Risco
// Bloco "Risco" (carteira/risco.py): maior queda, queda atual, VaR/CVaR de 1 dia e de 1 mês e a
// volatilidade, da carteira e do BOVA11; gráfico da queda desde o topo, dia a dia.
function renderRisk(a) {
    const r = a.risk;
    $('riskPanel').hidden = !r;
    if (!r) { state.riskChart?.destroy(); state.riskChart = null; return; }
    const p = r.portfolio, b = r.bova;
    const pct = (v, d = 1) => v === null || v === undefined ? '—' : fmtPct(v, d);
    const unsigned = (v) => v === null || v === undefined ? '—' : `${fmtNum(v, 1)}%`;  // volatilidade: sem sinal
    const sd = (d) => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(2, 4)}`;
    $('riskSub').innerText = `${fmtDate(r.from)} a ${fmtDate(r.to)} · ${fmtNum(r.sessions, 0)} pregões`;
    $('riskWarning').hidden = !r.warning;
    $('riskWarning').innerText = r.warning || '';
    const dd = p.maxDrawdown;
    const back = dd.recovered ? `recuperada em ${sd(dd.recovered)} (${fmtNum(dd.recoverySessions, 0)} pregões)` : 'ainda não recuperada';
    const card = (label, value, note, tone, help) => `<div class="kpi kpi-card ${tone}" data-reveal>
            <div class="kpi-top"><span class="kpi-label">${label} ${tip(help)}</span></div>
            <div class="kpi-value num">${value}</div><div class="kpi-note">${note}</div></div>`;
    const tail = (x, bx, unit) => card(`${unit === 'day' ? 'Dia' : 'Mês'} ruim (VaR ${r.level}%)`,
        `${pct(x.var)} <span class="kpi-delta">${fmtBRL(-x.varBRL)}</span>`,
        `média nos ruins (CVaR): ${pct(x.cvar)} · ${fmtBRL(-x.cvarBRL)}<br>BOVA11: ${pct(bx.var)} · CVaR ${pct(bx.cvar)}`,
        'tone-red', unit === 'day'
            ? `Em 1 de cada 20 dias a carteira perdeu isso ou mais. CVaR: a perda média nesses dias. Em R$ sobre o patrimônio de hoje.`
            : `Em 1 de cada 20 meses (21 pregões, janelas móveis) a carteira perdeu isso ou mais. CVaR: a perda média nesses meses.`);
    $('riskKpis').innerHTML = [
        card('Maior queda', pct(dd.pct), `${dd.pct < -0.05 ? `de ${sd(dd.peak)} a ${sd(dd.trough)} · ${back}` : 'sem queda no período'}<br>BOVA11: ${pct(b.maxDrawdown.pct)}`, dd.pct < -0.05 ? 'tone-red' : 'tone-green',
            'A pior perda de um topo até o fundo seguinte, pela rentabilidade da cota (os aportes não contam como ganho).'),
        card('Queda atual', pct(p.current.pct), p.current.pct < -0.05 ? `abaixo do topo de ${sd(p.current.peak)}` : 'no topo ou perto dele',
            p.current.pct < -0.05 ? 'tone-red' : 'tone-green', 'Quanto a carteira está abaixo do seu último topo hoje.'),
        tail(p.day, b.day, 'day'),
        tail(p.month, b.month, 'month'),
        card('Volatilidade', `${unsigned(p.volatility)} <span class="kpi-delta">ao ano</span>`, `BOVA11: ${unsigned(b.volatility)} ao ano`, 'tone-violet',
            'Quanto a rentabilidade oscila: o desvio-padrão dos retornos diários, anualizado. Quanto maior, maiores os sobes e desces.')
    ].join('');
    $('riskKpis')._itemsSeen = false;
    revealItems($('riskKpis'), '.kpi-card');
    $('riskText').innerHTML = r.text.map(t => `<li>${escapeHTML(t)}</li>`).join('');

    if (typeof Chart === 'undefined') return;
    const labels = r.chart.labels.map(sd);
    const data = [r.chart.portfolio, r.chart.bova];
    if (state.riskChart) {
        state.riskChart.data.labels = labels;
        state.riskChart.data.datasets.forEach((ds, k) => { ds.data = data[k]; });
        state.riskChart.update();
        return;
    }
    const muted = cssVar('--text-muted'), text = cssVar('--text-color');
    whenVisible('riskChart', () => {
        if (state.riskChart) return;
        state.riskChart = new Chart($('riskChart'), {
            type: 'line',
            data: { labels, datasets: [
                { label: 'Carteira', data: data[0], borderColor: '#ff7b72', backgroundColor: verticalGradient('#ff7b72', 0, 0.3), fill: 'origin',
                  borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 6, tension: 0.1 },
                { label: 'BOVA11', data: data[1], borderColor: '#4ad991', borderDash: [5, 4], borderWidth: 1.4, pointRadius: 0, pointHoverRadius: 4, fill: false, tension: 0.1 }
            ] },
            plugins: [crosshairPlugin],
            options: {
                responsive: true, maintainAspectRatio: false, animation: { duration: 900, easing: 'easeOutCubic' },
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: text, usePointStyle: true, pointStyle: 'line', boxWidth: 24 } },
                    tooltip: { ...chartTooltip(), callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtPct(ctx.parsed.y, 1)}` } }
                },
                scales: {
                    x: { ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false }, border: { color: CHART_GRID } },
                    y: { max: 0, ticks: { color: muted, callback: (v) => `${fmtNum(v, 0)}%` }, grid: { color: CHART_GRID }, border: { display: false } }
                }
            }
        });
    });
}
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
        + (a.zeroTargets ? `<span class="warn-text">${escapeHTML(a.zeroTargets)}</span> ` : '')
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

// Uma tabela por classe (na ordem das classes da carteira), só das classes com posição aberta ou
// título de renda fixa; cada uma paginada (paginate, chave "pos:{classe}").
const POSITION_COLUMNS = `
    <tr>
        <th>Ativo</th>
        <th class="r">Quantidade</th>
        <th class="r">Preço médio</th>
        <th class="r">Preço atual</th>
        <th class="r">Var. dia</th>
        <th class="r">Valor</th>
        <th class="r">Lucro</th>
        <th class="r">Rentab.</th>
        <th class="r">Proventos</th>
        <th class="r">% carteira</th>
        <th class="c">Situação</th>
    </tr>`;

function renderPositions(a) {
    const maxWeight = Math.max(0, ...a.open.map(p => p.weight), ...a.fixed.map(f => f.weight));
    const classIndex = (cls) => Math.max(0, a.allocation.findIndex(r => r.cls === cls));
    const avatar = (label, cls) => `<span class="avatar solo" style="--a1:${classColor(cls, classIndex(cls))}" aria-hidden="true">${escapeHTML(label.slice(0, 2).toUpperCase())}</span>`;
    const openRow = (p) => `
        <tr data-reveal class="chart-row" tabindex="0" data-ticker="${escapeHTML(p.ticker)}" data-market="${p.market}" aria-label="Abrir o gráfico de ${escapeHTML(p.ticker)} com as operações e os alvos">
            <td><div class="asset">${avatar(p.ticker, p.cls)}<div class="asset-text">${favoriteStar(p.ticker, p.market, state.favSet.has(`${p.ticker}|${p.market}`))}<a href="https://br.tradingview.com/chart/?symbol=${encodeURIComponent(tradingViewSymbol(p.ticker, p.market))}" target="_blank" rel="noopener" class="ticker-link">${p.ticker}</a>
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
            <td class="c">${withBottle(p, a.bottleLevels, `${p.status ? signalBadge(p.status, p.statusReason, STATUS_BADGE[p.statusKey] || 'badge-low') : '<span class="small muted">—</span>'}
                ${p.partial ? `<div class="small muted nowrap">${fmtQty(p.partial.quantity)} ${p.partial.quantity === 1 ? 'cota' : 'cotas'} de ${p.partial.lots} compra(s)</div>` : ''}`)}</td>
        </tr>`;
    const fixedRow = (f) => `
        <tr class="fi-section" data-reveal>
            <td><div class="asset">${avatar(f.name, f.cls)}<div class="asset-text"><strong>${escapeHTML(f.name)}</strong><div class="small muted">${escapeHTML(f.cls)}</div></div></div></td>
            <td colspan="4" class="small muted">Investido líquido ${fmtBRL(f.invested)} · valor atualizado em ${fmtDate(f.lastUpdate)}</td>
            <td class="r nowrap">${fmtBRL(f.value)}</td>
            <td class="r nowrap ${signClass(f.profit)}">${fmtBRL(f.profit)}</td>
            <td class="r nowrap">${deltaPill(f.returnPct)}</td>
            <td class="r muted">—</td>
            <td class="r nowrap">${weightCell(f.weight, maxWeight)}</td>
            <td class="c"><span class="small muted">—</span></td>
        </tr>`;
    // Classes na ordem da alocação (a das classes da carteira); uma classe sem posição não aparece.
    const order = [...a.allocation.map(r => r.cls), ...a.open.map(p => p.cls), ...a.fixed.map(f => f.cls)];
    const groups = [...new Set(order)].map(cls => {
        const open = a.open.filter(p => p.cls === cls);
        const fixed = a.fixed.filter(f => f.cls === cls);
        const alloc = a.allocation.find(r => r.cls === cls);
        const value = alloc ? alloc.value : [...open, ...fixed].reduce((s, x) => s + x.value, 0);
        return { cls, count: open.length + fixed.length, value, pct: alloc?.pct, rows: [...open.map(openRow), ...fixed.map(fixedRow)] };
    }).filter(g => g.rows.length);
    $('positionsTables').innerHTML = groups.length ? groups.map((g, k) => `
        <section class="position-group" style="--g:${classColor(g.cls, classIndex(g.cls))}" aria-label="Posições em ${escapeHTML(g.cls)}">
            <div class="position-group-head">
                <span class="swatch-box" aria-hidden="true"></span><h3>${escapeHTML(g.cls)}</h3>
                <span class="small muted">${g.count} ${g.count === 1 ? 'ativo' : 'ativos'} · ${fmtBRL(g.value)}${g.pct === undefined ? '' : ` · ${fmtNum(g.pct)}% da carteira`}</span>
            </div>
            <div class="table-container">
                <table class="sticky-first">
                    <thead>${POSITION_COLUMNS}</thead>
                    <tbody></tbody>
                </table>
            </div>
            <nav class="pager" data-pager="pos:${escapeHTML(g.cls)}" aria-label="Páginas da tabela de ${escapeHTML(g.cls)}" hidden></nav>
        </section>`).join('')
        : `<p class="empty-row muted">Nenhuma posição aberta.</p>`;
    const bodies = $('positionsTables').querySelectorAll('tbody');
    groups.forEach((g, k) => paginate(`pos:${g.cls}`, bodies[k], g.rows, ''));
}
//#endregion

//#region Card "Operações no gráfico"
// Clicar numa linha das Posições abre o gráfico do ativo com as compras (C) e vendas (V) lançadas
// e os alvos das compras abertas (grafico-ativo.js; GET /api/carteira/{id}/grafico).
function openPositionChart(ticker, market, row) {
    const p = (state.analysis?.open || []).find(x => x.ticker === ticker);
    const a = state.analysis;
    openCarteiraChart(ticker, market, row, p ? {
        position: p, status: p.status ? { label: p.status, reason: p.statusReason, badge: STATUS_BADGE[p.statusKey] || 'badge-low' } : null,
        pressure: p.pressure != null ? { value: p.pressure, state: p.bottle, parts: p.pressureParts, levels: a?.bottleLevels, states: a?.bottleStates } : null,
        noPressure: a?.noPressureText
    } : {});
}

// O card de qualquer ativo pela carteira aberta (Posições, Favoritos e ativos já vendidos): com as
// operações dela, quando houver, na cor da classe.
function openCarteiraChart(ticker, market, row, extra = {}) {
    const classIndex = (cls) => Math.max(0, (state.analysis?.allocation || []).findIndex(r => r.cls === cls));
    openAssetChart(ticker, {
        url: `/carteira/${encodeURIComponent(state.currentId)}/grafico?ticker=${encodeURIComponent(ticker)}&market=${encodeURIComponent(market || 'b3')}`,
        colorFor: (cls) => classColor(cls, classIndex(cls)), back: row, ...extra
    });
}
//#endregion
