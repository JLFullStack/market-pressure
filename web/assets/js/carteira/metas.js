// Carteira → Metas: formulário (salvar ou só simular), um cartão por meta (anel de progresso,
// KPIs, marcos, gráficos, cenários, ano a ano) e o realizado × planejado. Usa carteira.js.

//#region Constantes e eventos
// Rótulo do campo de valor da meta, conforme o tipo.
const GOAL_TARGET_LABEL = { renda: 'Renda mensal desejada (R$)', patrimonio: 'Patrimônio desejado (R$)' };
const GOAL_COLORS = { value: '#5aa2ff', contributed: '#a78bfa', goal: '#ffc65c' };

// Formulário de meta e os botões dos cartões.
function setupMetas() {
    $('goalForm').addEventListener('submit', submitGoal);
    $('goalPreviewBtn').addEventListener('click', previewGoal);
    $('goalCancelBtn').addEventListener('click', resetGoalForm);
    $('goalKind').addEventListener('change', updateGoalForm);
    $('goalUsePortfolio').addEventListener('change', updateGoalForm);
    $('goalsList').addEventListener('click', onGoalsClick);
    updateGoalForm();
}
//#endregion

//#region Cartões das metas
// Metas da carteira com a projeção de cada uma (tudo calculado no servidor, goals.py).
async function loadGoals() {
    const portfolio = current();
    if (!portfolio) return;
    const seq = ++state.goalsSeq;
    $('goalsList').innerHTML = '<div class="panel"><p class="muted">Calculando as metas...</p></div>';
    let res;
    try {
        res = await apiGet(`/carteira/${portfolio.id}/metas`);
    } catch (e) {
        if (seq === state.goalsSeq) $('goalsList').innerHTML = `<div class="panel"><p class="neg">Não foi possível calcular as metas (${escapeHTML(e.message)}).</p></div>`;
        return;
    }
    if (seq !== state.goalsSeq || portfolio.id !== state.currentId) return;
    state.goals = res;
    renderGoals(res);
}

function renderGoals(res) {
    for (const c of state.goalCharts) c.destroy();
    state.goalCharts = [];
    $('goalsWarning').innerText = res.warning || '';
    $('goalsWarning').style.display = res.warning ? 'block' : 'none';
    $('goalsList').innerHTML = res.goals.length ? res.goals.map(g => goalCard(g, true)).join('')
        : '<div class="panel"><p class="muted">Nenhuma meta cadastrada. Preencha o formulário acima — por exemplo, a renda mensal que quer ter para se aposentar.</p></div>';
    observeReveal($('goalsList'));
    $('goalsList').querySelectorAll('tbody').forEach(tb => revealItems(tb));
    for (const g of res.goals) drawGoalChart(g, `goalChart-${g.id}`);
}

const money0 = (v) => `R$ ${fmtNum(v, 0)}`;

const rate = (v, d = 2) => `${fmtNum(v, d)}%`;

const monthYear = (iso) => `${iso.slice(5, 7)}/${iso.slice(0, 4)}`;

// value: o texto do valor, ou { n, f } para um valor em reais que conta ao aparecer (f = formato
// do contador: brl0 sem centavos, brl com centavos).
function goalKpi(label, value, note = '') {
    const shown = typeof value === 'object'
        ? `<div class="kpi-value num" data-count="${value.n}" data-format="${value.f}">${COUNT_FORMAT[value.f](0)}</div>`
        : `<div class="kpi-value num">${value}</div>`;
    return `<div class="kpi goal-kpi" data-reveal><div class="kpi-label">${label}</div>${shown}<div class="kpi-note">${note}</div></div>`;
}

// Anel de progresso da meta (0 a 100%), desenhado ao aparecer, com o % no meio.
const goalRing = (pct) => `<span class="ring goal-ring" style="--off:${(100 - Math.max(0, Math.min(100, pct))).toFixed(1)}" role="img" aria-label="${fmtNum(pct, 1)}% da meta alcançado">
    <svg viewBox="0 0 44 44" aria-hidden="true"><circle class="ring-track" cx="22" cy="22" r="18"/><circle class="ring-val" cx="22" cy="22" r="18" pathLength="100"/></svg>
    <span class="ring-num num">${fmtNum(pct, pct >= 99.95 ? 0 : 1)}%</span></span>`;

// Linha da tabela ano a ano: o ano em pílula, os juros em verde, o patrimônio com a barra da
// composição (aportado × juros, proporção para o desenho) e o % da meta com barra de progresso.
function yearlyRow(y) {
    const share = (v) => (y.value > 0 ? Math.max(0, Math.min(1, v / y.value)) : 0).toFixed(3);
    return `<tr class="${y.progress >= 100 ? 'yearly-reached' : ''}">
        <td class="r"><span class="year-pill">${y.year}</span></td>
        <td class="nowrap muted">${monthYear(y.date)}</td>
        <td class="r nowrap">${money0(y.contributed)}</td>
        <td class="r nowrap ${signClass(y.interest)}">${y.interest > 0 ? '+' : ''}${money0(y.interest)}</td>
        <td class="r nowrap"><strong class="yearly-value">${money0(y.value)}</strong>
            <span class="mix-bar" aria-hidden="true"><i style="--w:${share(y.contributed)};background:${GOAL_COLORS.contributed}"></i><i style="--w:${share(y.interest)};background:#4ad991"></i></span></td>
        <td class="r nowrap">${fmtBRL(y.income)}</td>
        <td class="r nowrap"><span class="goal-pct"><span class="num">${fmtNum(y.progress, 1)}%</span>
            <span class="goal-pct-bar"><i style="--w:${(Math.max(0, Math.min(100, y.progress)) / 100).toFixed(3)}"></i></span></span></td>
    </tr>`;
}

// Cartão de uma meta (saved = tem botões de editar e excluir; a prévia não tem).
function goalCard(g, saved) {
    const r = g.result, reach = r.reach, by = r.byDate;
    const head = g.kind === 'renda'
        ? `Renda de ${fmtBRL(g.target)} por mês · retirada de ${rate(g.withdrawal, 1)} ao ano`
        : `Patrimônio de ${fmtBRL(g.target)}`;
    const start = g.usePortfolio ? 'patrimônio atual da carteira' : 'patrimônio inicial informado';
    const kpis = [
        goalKpi('Patrimônio necessário', { n: r.goalValue, f: 'brl0' },
            g.kind === 'renda' ? `para ${fmtBRL(g.target)}/mês com ${rate(g.withdrawal, 1)} ao ano`
                : `renda possível: ${fmtBRL(r.income)}/mês (${rate(g.withdrawal, 1)} a.a.)`),
        goalKpi('Patrimônio de partida', { n: r.current, f: 'brl0' }, `${start} · rende ${fmtBRL(r.incomeNow)}/mês hoje`),
        goalKpi('Falta acumular', { n: r.missing, f: 'brl0' }, `${fmtNum(r.progress, 1)}% da meta já alcançado`),
        goalKpi('Tempo para alcançar', reach ? escapeHTML(reach.text) : '+100 anos',
            reach ? (reach.months ? `previsão: ${monthYear(reach.date)}` : 'meta já alcançada') : 'não alcança no ritmo atual'),
        goalKpi('Aporte mensal', { n: g.monthly, f: 'brl' }, g.adjust ? 'corrigido pela inflação todo ano' : 'valor fixo (perde poder de compra)'),
        goalKpi('Rentabilidade real', `${rate(r.rates.realYear)} a.a.`,
            `${rate(r.rates.realMonth, 3)} a.m. · nominal ${rate(r.rates.nominalYear)} a.a. (${rate(r.rates.nominalMonth, 3)} a.m.) · inflação ${rate(r.rates.inflationYear)} a.a.`),
        reach ? goalKpi('Total aportado até a meta', { n: reach.contributed + r.current, f: 'brl0' },
            `aportes ${money0(reach.contributed)} + partida ${money0(r.current)} · aportes nominais ${money0(reach.nominalContributed)}`) : '',
        reach ? goalKpi('Juros compostos', { n: reach.interest, f: 'brl0' }, `${fmtNum(reach.interestShare, 1)}% do patrimônio na meta vem dos juros`) : '',
        goalKpi('Renda passiva na meta', `${fmtBRL(r.income)}/mês`, `juros reais de um mês na meta: ${fmtBRL(r.monthlyInterestAtGoal)}`),
        goalKpi('Ponto de virada', r.turningPoint ? escapeHTML(r.turningPoint.text) : '—',
            r.turningPoint ? `em ${monthYear(r.turningPoint.date)} os juros do mês passam o aporte` : 'os juros do mês não passam o aporte no horizonte'),
        reach && reach.months ? goalKpi('Valor nominal na data', { n: reach.nominalValue, f: 'brl0' }, 'o patrimônio necessário corrigido pela inflação até lá') : ''
    ].join('');
    const milestones = r.milestones.map(m => `<span class="goal-chip${m.reached ? ' done' : ''}" data-reveal>${m.pct}% · ${escapeHTML(m.text)}${m.date && m.months ? ` (${monthYear(m.date)})` : ''}</span>`).join('');
    const scen = r.scenarios;
    const scenarios = `<table class="goal-scenarios"><thead><tr><th>Aporte mensal</th>${scen.rates.map(x => `<th class="r">${rate(x, 1)} a.a.</th>`).join('')}</tr></thead>
        <tbody>${scen.rows.map(row => `<tr><td class="nowrap">${fmtBRL(row.monthly)}</td>${row.cells.map(c => `<td class="r nowrap${c.current ? ' goal-current' : ''}">${escapeHTML(c.text)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    const yearly = `<table class="sticky-first"><thead><tr><th class="r">Ano</th><th>Data</th><th class="r">Aportado (com a partida)</th><th class="r">Juros</th><th class="r">Patrimônio</th><th class="r">Renda possível/mês</th><th class="r">% da meta</th></tr></thead>
        <tbody>${r.yearly.map(yearlyRow).join('')}</tbody></table>
        <div class="bt-legend"><span><span class="swatch-dot" style="background:${GOAL_COLORS.contributed}"></span>aportado</span><span><span class="swatch-dot" style="background:#4ad991"></span>juros</span><span class="muted">barra sob o patrimônio: de onde ele vem · linhas em verde: meta já alcançada</span></div>`;
    const actions = saved ? `<div class="batch-buttons"><button type="button" class="btn-secondary" data-goal-edit="${escapeHTML(g.id)}">Editar</button>
        <button type="button" class="btn-secondary" data-goal-delete="${escapeHTML(g.id)}">Excluir</button></div>` : '<span class="badge badge-info">Prévia, não salva</span>';
    return `<div class="panel goal-card" data-reveal>
        <div class="panel-head"><div class="goal-head">${goalRing(r.progress)}<div><h2>${escapeHTML(g.name)}</h2><div class="small muted">${escapeHTML(g.kindLabel)} · ${head}</div></div></div>${actions}</div>
        <p class="goal-summary" data-reveal>${escapeHTML(r.summary)}</p>
        <div class="goal-progress" role="img" aria-label="${fmtNum(r.progress, 1)}% da meta alcançado"><div style="width:${Math.max(0, Math.min(100, r.progress))}%"></div></div>
        <div class="small muted">${fmtNum(r.progress, 1)}% · ${fmtBRL(r.current)} de ${fmtBRL(r.goalValue)}</div>
        ${by ? `<p class="goal-bydate ${by.onTrack ? 'pos' : 'warn-text'}">${escapeHTML(by.summary)}</p>` : ''}
        ${r.warnings.map(w => `<p class="warn-text small">${escapeHTML(w)}</p>`).join('')}
        <div class="kpis goal-kpis">${kpis}</div>
        ${goalTracking(g)}
        ${goalProjections(r.projections)}
        <h3 data-reveal>Marcos</h3><div class="goal-chips">${milestones}</div>
        <div class="chart-card" data-reveal>
            <div class="chart-card-head"><h3>Evolução do patrimônio</h3><span class="small muted">valores de hoje, ano a ano</span></div>
            <div class="chart-wrap goal-chart" data-reveal="late"><canvas id="goalChart-${escapeHTML(g.id)}" role="img" aria-label="Patrimônio projetado, valor aportado e meta, ano a ano"></canvas></div>
        </div>
        <h3 data-reveal>Tempo para alcançar em outros cenários</h3>
        <div class="table-container">${scenarios}</div>
        <details class="goal-details"><summary>Tabela ano a ano</summary><div class="table-container">${yearly}</div></details>
    </div>`;
}

// Realizado × planejado desde a criação da meta (só nas que partem da carteira).
function goalTracking(g) {
    const t = g.result.tracking;
    if (!t) return '';
    if (!t.started) return `<h3 data-reveal>Realizado × planejado</h3><p class="small muted">${escapeHTML(t.summary)}</p>`;
    return `<h3 data-reveal>Realizado × planejado <span class="small muted">desde ${fmtDate(t.created)}</span></h3>
        <div class="kpis goal-kpis">
            ${goalKpi('Planejado para hoje', { n: t.planned, f: 'brl' }, `partindo de ${fmtBRL(t.startValue)} em ${fmtDate(t.created)}`)}
            ${goalKpi('Patrimônio real', { n: t.actual, f: 'brl' }, 'carteira com as cotações de agora')}
            ${goalKpi(t.ahead ? 'À frente do plano' : 'Atrás do plano', `<span class="${t.ahead ? 'pos' : 'neg'}">${fmtBRL(Math.abs(t.diff))}</span>`,
                t.diffPct === null ? '' : `${fmtPct(t.diffPct, 1)} em relação ao planejado`)}
        </div>
        <p class="goal-bydate ${t.ahead ? 'pos' : 'warn-text'}">${escapeHTML(t.summary)}</p>
        <div class="chart-card" data-reveal>
            <div class="chart-card-head"><h3>Patrimônio real × planejado</h3><span class="small muted">desde a criação da meta</span></div>
            <div class="chart-wrap goal-chart goal-chart-small" data-reveal="late"><canvas id="goalTrack-${escapeHTML(g.id)}" role="img" aria-label="Patrimônio real da carteira e o planejado pela meta, mês a mês"></canvas></div>
        </div>`;
}

// Tempo para alcançar pelo plano e pelos dados reais da carteira (aporte médio, rentabilidade).
function goalProjections(rows) {
    if (!rows || rows.length < 2) return '';
    return `<h3 data-reveal>Previsão pelo plano e pelos dados reais da carteira</h3>
        <div class="table-container"><table>
            <thead><tr><th>Cenário</th><th class="r">Aporte mensal</th><th class="r">Rentabilidade</th><th class="r">Tempo para alcançar</th><th class="r">Previsão</th></tr></thead>
            <tbody>${rows.map(p => `<tr><td><strong>${escapeHTML(p.label)}</strong><div class="small muted">${escapeHTML(p.note)}</div></td>
                <td class="r nowrap">${fmtBRL(p.monthly)}</td><td class="r nowrap">${rate(p.rate, 1)} a.a.</td>
                <td class="r nowrap">${escapeHTML(p.text)}</td><td class="r nowrap">${p.date && p.months ? monthYear(p.date) : '—'}</td></tr>`).join('')}</tbody>
        </table></div>`;
}
//#endregion

//#region Gráficos das metas
// Linha principal dos gráficos das metas: como a da carteira na Rentabilidade acumulada.
const GOAL_LINE = { borderWidth: 2.6, pointRadius: 0, pointHoverRadius: 5, pointHoverBorderWidth: 2, pointHitRadius: 8, tension: 0.25 };

// Eixos e legenda comuns aos gráficos das metas (tema escuro novo).
const goalChartOptions = (yTick) => ({
    responsive: true, maintainAspectRatio: false, animation: { duration: 1100, easing: 'easeOutCubic' },
    interaction: { mode: 'index', intersect: false },
    layout: { padding: { top: 4, right: 6 } },
    plugins: { legend: { labels: { color: cssVar('--text-color'), usePointStyle: true, pointStyle: 'line', boxWidth: 24, padding: 16 } },
               tooltip: { ...chartTooltip(), callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtBRL(ctx.parsed.y)}` } } },
    scales: { x: { ticks: { color: cssVar('--text-muted'), maxTicksLimit: 12, maxRotation: 0 }, grid: { display: false }, border: { color: CHART_GRID } },
              y: { ticks: { color: cssVar('--text-muted'), callback: yTick }, grid: { color: CHART_GRID }, border: { display: false } } }
});

function drawTrackingChart(g) {
    const t = g.result.tracking;
    const id = `goalTrack-${g.id}`;
    if (!t?.started || !$(id) || typeof Chart === 'undefined') return;
    whenVisible(id, () => {
        if (!$(id)) return;
        state.goalCharts.push(new Chart($(id), {
            type: 'line',
            plugins: [crosshairPlugin],
            data: {
                labels: t.chart.labels.map(fmtDate),
                datasets: [
                    { label: 'Patrimônio real', data: t.chart.actual, borderColor: GOAL_COLORS.value, backgroundColor: verticalGradient(GOAL_COLORS.value, 0.4, 0.02),
                      fill: 'start', ...GOAL_LINE },
                    { label: 'Planejado', data: t.chart.planned, borderColor: GOAL_COLORS.goal, borderDash: [6, 4], borderWidth: 1.8, pointRadius: 0,
                      pointHoverRadius: 4, pointHoverBackgroundColor: cssVar('--panel-bg'), tension: 0.15 }
                ]
            },
            options: goalChartOptions((v) => fmtCompactBRL(v))
        }));
    });
}

function drawGoalChart(g, canvasId) {
    drawTrackingChart(g);
    if (!$(canvasId) || typeof Chart === 'undefined') return;
    const y = g.result.yearly;
    whenVisible(canvasId, () => {
        if (!$(canvasId)) return;
        state.goalCharts.push(new Chart($(canvasId), {
            type: 'line',
            plugins: [crosshairPlugin],
            data: {
                labels: y.map(p => p.date.slice(0, 4)),
                datasets: [
                    { label: 'Patrimônio', data: y.map(p => p.value), borderColor: GOAL_COLORS.value, backgroundColor: verticalGradient(GOAL_COLORS.value, 0.42, 0.02),
                      fill: 'origin', ...GOAL_LINE },
                    { label: 'Aportado (com a partida)', data: y.map(p => p.contributed), borderColor: GOAL_COLORS.contributed,
                      backgroundColor: verticalGradient(GOAL_COLORS.contributed, 0.16, 0), fill: 'origin', ...GOAL_LINE, borderWidth: 1.8 },
                    { label: 'Meta', data: y.map(() => g.result.goalValue), borderColor: GOAL_COLORS.goal, borderDash: [6, 4], borderWidth: 1.8, pointRadius: 0, pointHoverRadius: 0 }
                ]
            },
            options: goalChartOptions((v) => `R$ ${fmtNum(v / 1000, 0)} mil`)
        }));
    });
}
//#endregion

//#region Formulário
// Campos do formulário como digitados (o servidor valida e converte).
function goalFields() {
    return {
        name: $('goalName').value, kind: $('goalKind').value, target: $('goalTarget').value, withdrawal: $('goalWithdrawal').value,
        initial: $('goalInitial').value, usePortfolio: $('goalUsePortfolio').checked, monthly: $('goalMonthly').value,
        rate: $('goalRate').value, inflation: $('goalInflation').value, targetDate: $('goalDate').value, adjust: $('goalAdjust').checked
    };
}

function updateGoalForm() {
    $('goalTargetLabel').innerText = GOAL_TARGET_LABEL[$('goalKind').value];
    $('goalInitial').disabled = $('goalUsePortfolio').checked;
}

function resetGoalForm() {
    state.editingGoal = null;
    $('goalForm').reset();
    $('goalFormTitle').innerText = 'Nova meta';
    $('goalSaveBtn').innerText = 'Salvar meta';
    $('goalCancelBtn').hidden = true;
    $('goalPreview').hidden = true;
    $('goalPreview').innerHTML = '';
    setMsg('goalMsg', '');
    updateGoalForm();
}

function fillGoalForm(g) {
    state.editingGoal = g.id;
    $('goalName').value = g.name;
    $('goalKind').value = g.kind;
    $('goalTarget').value = g.target;
    $('goalWithdrawal').value = g.withdrawal;
    $('goalInitial').value = g.initial;
    $('goalUsePortfolio').checked = g.usePortfolio;
    $('goalMonthly').value = g.monthly;
    $('goalRate').value = g.rate;
    $('goalInflation').value = g.inflation;
    $('goalDate').value = g.targetDate || '';
    $('goalAdjust').checked = g.adjust;
    $('goalFormTitle').innerText = `Editar meta "${g.name}"`;
    $('goalSaveBtn').innerText = 'Salvar alterações';
    $('goalCancelBtn').hidden = false;
    setMsg('goalMsg', '');
    updateGoalForm();
    $('goalFormPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('goalName').focus();
}

async function submitGoal(ev) {
    ev.preventDefault();
    if (!current()) return;
    const res = await act('salvar_meta', { ...goalFields(), goalId: state.editingGoal });
    if (res.error) return setMsg('goalMsg', res.error, 'error');
    resetGoalForm();
    setMsg('goalMsg', res.message, 'ok');
}

// Projeção do que está no formulário, sem gravar.
async function previewGoal() {
    const portfolio = current();
    if (!portfolio) return;
    const busy = busyStart('Simulando a meta...');
    let res;
    try {
        res = await apiPost(`/carteira/${portfolio.id}/metas/simular`, goalFields());
    } catch (e) {
        setMsg('goalMsg', e.message, 'error');
        return;
    } finally {
        busy();
    }
    setMsg('goalMsg', res.warning || '', res.warning ? 'error' : '');
    const box = $('goalPreview');
    box.hidden = false;
    box.innerHTML = goalCard(res.goal, false);
    observeReveal(box);
    box.querySelectorAll('tbody').forEach(tb => revealItems(tb));
    drawGoalChart(res.goal, `goalChart-${res.goal.id}`);
}

async function onGoalsClick(ev) {
    const edit = ev.target.closest?.('[data-goal-edit]');
    const del = ev.target.closest?.('[data-goal-delete]');
    const goals = state.goals?.goals || [];
    if (edit) {
        const g = goals.find(x => x.id === edit.dataset.goalEdit);
        if (g) fillGoalForm(g);
    } else if (del) {
        const g = goals.find(x => x.id === del.dataset.goalDelete);
        if (!g || !confirm(`Excluir a meta "${g.name}"?`)) return;
        const res = await act('excluir_meta', { goalId: g.id });
        setMsg('goalMsg', res.error || res.message, res.error ? 'error' : 'ok');
        if (!res.error && state.editingGoal === g.id) resetGoalForm();
    }
}
//#endregion
