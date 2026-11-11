// Carteira → Proventos: "Proventos mês a mês" (KPIs e gráfico), "Quem mais pagou", recebidos e a
// receber. Usa carteira.js.

//#region Proventos mês a mês
// Proventos mês a mês: os recebidos nos últimos 12 meses (data-ex) e, à direita, os anunciados a
// receber por mês de pagamento (confirmados e previstos empilhados; "A definir" por último).
function renderDividendChart() {
    const a = state.analysis;
    if (!a || typeof Chart === 'undefined') return;
    const received = state.receivables?.received.months || [];
    const future = state.receivables?.months || [];
    const keys = received.map(m => m.month);
    for (const m of future) if (!keys.includes(m.month)) keys.push(m.month);
    const label = (k) => k ? fmtMonth(k) : 'A definir';
    const pick = (list, field) => keys.map(k => list.find(m => m.month === k)?.[field] ?? null);
    const series = [
        { label: 'Recebidos', data: pick(received, 'total'), color: '#4ad991' },
        { label: 'A receber · confirmados', data: pick(future, 'confirmed'), color: '#22d3ee' },
        { label: 'A receber · previstos', data: pick(future, 'expected'), color: '#9b7bff' },
    ];
    // Sem nenhum provento no período: uma frase no lugar de um eixo vazio.
    const any = series.some(x => x.data.some(v => v > 0));
    $('dividendChart').parentElement.hidden = !any;
    $('dividendChartEmpty').hidden = any || !state.receivables;  // sem resposta ainda, a frase não vale
    whenVisible('dividendChart', () => {
        state.dividendChart?.destroy();
        state.dividendChart = null;
        if (!any) return;
        state.dividendChart = new Chart($('dividendChart'), {
            type: 'bar',
            data: {
                labels: keys.map(label),
                datasets: series.map(x => ({
                    label: x.label, data: x.data, backgroundColor: verticalGradient(x.color, 0.95, 0.5), hoverBackgroundColor: x.color,
                    borderRadius: 5, borderSkipped: false, maxBarThickness: 34, stack: 'proventos'
                }))
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: CHART_ENTER,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: cssVar('--text-color'), usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 12,
                                        filter: (item, d) => d.datasets[item.datasetIndex].data.some(v => v > 0) } },
                    tooltip: { ...chartTooltip(), filter: (item) => item.parsed.y > 0,
                               callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtBRL(ctx.parsed.y)}` } }
                },
                scales: {
                    x: { stacked: true, ticks: { color: cssVar('--text-muted'), maxRotation: 0, autoSkip: true }, grid: { display: false }, border: { color: CHART_GRID } },
                    y: { stacked: true, beginAtZero: true, ticks: { color: cssVar('--text-muted'), callback: (v) => fmtCompactBRL(v) }, grid: { color: CHART_GRID }, border: { display: false } }
                }
            }
        });
    });
}
//#endregion

//#region Recebidos e quem mais pagou
// "Quem mais pagou": barras dos ativos com mais proventos recebidos (proporcionais ao maior).
const DIVIDEND_RANKING = 8;

function renderDividendRanking(totals) {
    const top = totals.slice(0, DIVIDEND_RANKING);
    const max = top.length ? top[0][1] : 0;
    $('dividendRanking').innerHTML = top.length ? top.map(([t, v], k) => `<div class="rank-bar" style="--k:${k}">
            <span class="rank-bar-label">${escapeHTML(t)}</span>
            <span class="rank-bar-track"><i style="--w:${(max ? v / max : 0).toFixed(3)}"></i></span>
            <span class="rank-bar-val num">${fmtBRL(v)}</span></div>`).join('')
        : '<p class="small muted">Nenhum provento recebido ainda.</p>';
}

// Os proventos já pagos (received de /proventos); sem eles, os campos vazios com a nota.
function renderDividends(received, note) {
    const foreign = state.analysis?.hasForeign ? '; EUA sem os 30% de IR retidos' : '';
    setCount($('kpiDividends'), received ? received.total : null, { format: 'brl' });
    $('kpiDividendsNote').innerText = received
        ? [`Brutos, estimados pela data-com${foreign}`, received.pendingNote].filter(Boolean).join(' · ') : note;
    setCount($('divYear'), received ? received.year : null, { format: 'brl' });
    $('divYearNote').innerText = received ? 'pela data-ex, brutos' : note;
    setCount($('divTotal'), received ? received.total : null, { format: 'brl' });
    $('divTotalNote').innerText = received ? `${received.dividends.length} pagamento(s) desde a primeira compra` : note;
    renderDividendRanking(received ? received.totals : []);
    renderDividendChart();
    const totals = received?.totals || [];
    const parts = [];
    if (totals.length) parts.push(`Total por ativo: ${totals.map(([t, v]) => `<strong>${t}</strong> ${fmtBRL(v)}`).join(' · ')} · <strong>Total ${fmtBRL(received.total)}</strong>`);
    if (received?.warning) parts.push(`<span class="neg">${escapeHTML(received.warning)}</span>`);
    $('dividendTotals').innerHTML = parts.join(' · ');
    const rows = received?.dividends || [];
    $('dividendsBody').innerHTML = !received ? `<tr><td colspan="5" class="empty-row">${escapeHTML(note)}</td></tr>`
        : rows.length ? rows.map(d => `
        <tr>
            <td class="nowrap">${fmtDate(d.comDate)}</td>
            <td><strong>${d.ticker}</strong></td>
            <td class="r nowrap">R$ ${d.amount.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}</td>
            <td class="r nowrap">${fmtQty(d.quantity)}</td>
            <td class="r nowrap">${fmtBRL(d.total)}</td>
        </tr>`).join('')
        : `<tr><td colspan="5" class="empty-row">Nenhum provento no período das posições.</td></tr>`;
    revealItems($('dividendsBody'));
}
//#endregion

//#region A receber
// Proventos anunciados e ainda não pagos (já ordenados por data de pagamento, a definir por último).
function renderReceivables(result) {
    state.receivables = result.ok ? result.r : null;
    setCount($('divToReceive'), result.ok ? result.r.total : null, { format: 'brl' });
    $('divToReceiveNote').innerText = !result.ok ? 'proventos anunciados indisponíveis'
        : result.r.rows.length ? `${fmtBRL(result.r.net)} líquido estimado` : 'nenhum anunciado';
    renderDividends(result.ok ? result.r.received : null, 'Proventos indisponíveis agora');
    if (!result.ok) {
        $('receivableTotals').innerHTML = `<span class="neg">Proventos anunciados indisponíveis (${escapeHTML(result.e.message)}).</span>`;
        $('receivablesBody').innerHTML = `<tr><td colspan="8" class="empty-row">Nenhum provento anunciado a receber.</td></tr>`;
        revealItems($('receivablesBody'));
        return;
    }
    const r = result.r, rows = r.rows;
    const parts = [];
    if (rows.length) {
        parts.push(`Total a receber: <strong>${fmtBRL(r.total)}</strong> bruto · ${fmtBRL(r.net)} líquido estimado (JCP com ${fmtNum(r.jcpTax * 100, 1)}% de IR)`);
        if (r.expected > 0) parts.push(`${fmtBRL(r.expected)} previstos dependem de manter o ativo até a data-com`);
    }
    if (r.failed.length) parts.push(`<span class="neg">Proventos anunciados indisponíveis: ${r.failed.map(escapeHTML).join('; ')}.</span>`);
    $('receivableTotals').innerHTML = parts.join(' · ');
    $('receivablesBody').innerHTML = rows.length ? rows.map(d => `
        <tr>
            <td class="nowrap">${d.paymentDate ? fmtDate(d.paymentDate) : 'A definir'}</td>
            <td class="nowrap">${fmtDate(d.comDate)}</td>
            <td><strong>${d.ticker}</strong></td>
            <td class="nowrap"${d.relatedTo ? ` title="Referente a ${escapeHTML(d.relatedTo)}"` : ''}>${escapeHTML(d.type)}</td>
            <td class="r nowrap">R$ ${d.amount.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}</td>
            <td class="r nowrap">${fmtQty(d.quantity)}</td>
            <td class="r nowrap">${fmtBRL(d.total)}</td>
            <td>${d.confirmed ? '<span class="badge badge-ok">Confirmado</span>' : '<span class="badge badge-fill">Previsto</span>'}</td>
        </tr>`).join('')
        : `<tr><td colspan="8" class="empty-row">Nenhum provento anunciado a receber.</td></tr>`;
    revealItems($('receivablesBody'));
}

// ---------------------------------------------------------------- Formulários
//#endregion
