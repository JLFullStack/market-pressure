// Carteira → Aportes e lançamentos: a calculadora de aporte (plano, sugestões do Screener,
// gráficos, registrar compras sugeridas), Nova operação, Renda fixa e o histórico de lançamentos.
// Usa carteira.js.

//#region Constantes, preferências e eventos
const APORTE_KEY = 'marketpressure_aporte_'; // + id da carteira: { amount, mode, selected }
const COLLAPSE_KEY = 'marketpressure_aporte_recolhido'; // "1" = calculadora de aporte recolhida
const HISTORY_LIMIT = 10; // lançamentos mostrados no histórico antes de "Ver todos"
const APORTE_MODES = { todos: 'Todos os ativos da carteira', escolher: 'Escolher ativos', pullback: 'Pelas regras do pullback' };
const PLAN_BADGE = { sell: 'badge-hot', fixed: 'badge-low', setup: 'badge-ok', wait: 'badge-fill', candidate: 'badge-info' };
const AMOUNT_DELAY_MS = 250; // espera após a digitação do valor do aporte antes de recalcular
// Tipo do lançamento no histórico → pílula (compra/aporte entram, venda/resgate saem).
const ENTRY_PILL = { compra: 'badge-ok', aporte: 'badge-info', venda: 'badge-hot', resgate: 'badge-fill' };
// Sentido do dinheiro do lançamento (entra na carteira ou sai dela) → cor do ponto e seta da pílula.
const ENTRY_DIRECTION = { compra: 'in', aporte: 'in', venda: 'out', resgate: 'out', valor: 'update' };
const ENTRY_ARROW = {
    in: '<svg class="entry-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17L17 7M9 7h8v8"/></svg>',
    out: '<svg class="entry-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M17 7L7 17M15 17H7V9"/></svg>',
    update: '<svg class="entry-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4"/></svg>',
};

// ================================================================ Estado

// Preferências do aporte por carteira (só neste navegador): valor, modo e ativos escolhidos.
// Também as opções (teto por compra, máximo de ativos, Indicação, sobra) e os candidatos do
// Screener marcados.
const APORTE_DEFAULTS = { amount: '', mode: 'todos', selected: [], cap: '10', maxAssets: '', advice: true, reuse: true, candidates: [] };
function readAportePrefs(id) {
    try { return { ...APORTE_DEFAULTS, ...JSON.parse(localStorage.getItem(APORTE_KEY + id) || '{}') }; }
    catch (e) { return { ...APORTE_DEFAULTS }; }
}

function writeAportePrefs(id, prefs) {
    try { localStorage.setItem(APORTE_KEY + id, JSON.stringify(prefs)); } catch (e) { /* preferência não salva */ }
}

// ================================================================ Renderização

// Campos da calculadora com as preferências da carteira (ao trocar de carteira).
function applyAportePrefs(portfolio) {
    const prefs = readAportePrefs(portfolio.id);
    $('aporteAmount').value = prefs.amount;
    $('aporteMode').value = APORTE_MODES[prefs.mode] ? prefs.mode : 'todos';
    $('aporteCap').value = prefs.cap;
    if (!$('aporteCap').value) $('aporteCap').value = APORTE_DEFAULTS.cap;
    $('aporteMax').value = prefs.maxAssets;
    $('aporteAdvice').checked = prefs.advice !== false;
    $('aporteReuse').checked = prefs.reuse !== false;
    state.aporteFor = portfolio.id;
}

// Calculadora, lista de compras, Nova operação, Renda fixa e histórico.
function setupAportes() {
    $('txMarket').innerHTML = Object.entries(MARKETS).map(([k, m]) => `<option value="${k}">${m.label}</option>`).join('');
    $('aporteMode').innerHTML = Object.entries(APORTE_MODES).map(([k, label]) => `<option value="${k}">${label}</option>`).join('');
    try { $('aporteBase').value = localStorage.getItem(MODE_KEY) || 'fechamento'; } catch (e) { /* sem preferência salva */ }
    $('txTicker').addEventListener('change', onTxInputChange);
    $('txDate').addEventListener('change', onTxInputChange);
    for (const id of ['txType', 'txDate', 'txQty', 'txPrice', 'txFx', 'txTicker']) {
        $(id).addEventListener('input', scheduleTxTax);
        $(id).addEventListener('change', scheduleTxTax);
    }
    $('txMarket').addEventListener('change', onTxInputChange);
    $('txClass').addEventListener('change', () => { state.classTouched = true; });
    $('txForm').addEventListener('submit', submitTransaction);
    $('fiNewForm').addEventListener('submit', submitFixedIncomeItem);
    $('fiEntryForm').addEventListener('submit', submitFixedIncomeEntry);
    $('historyBody').addEventListener('click', onHistoryClick);
    $('aporteAmount').addEventListener('input', onAporteInput);
    $('aporteMode').addEventListener('change', onAporteInput);
    $('aporteBase').addEventListener('change', () => {
        try { localStorage.setItem(MODE_KEY, $('aporteBase').value); } catch (e) { /* preferência não salva */ }
        closeBatch();
        renderAporte();
    });
    $('aporteCap').addEventListener('change', onAporteInput);
    $('aporteMax').addEventListener('input', onAporteInput);
    $('aporteAdvice').addEventListener('change', onAporteInput);
    $('aporteReuse').addEventListener('change', onAporteInput);
    $('aporteCandidates').addEventListener('change', onCandidateChange);
    $('aporteAssetsBody').addEventListener('change', onAporteTableChange);
    $('aporteToggle').addEventListener('click', () => setAporteCollapsed($('aporteToggle').getAttribute('aria-expanded') === 'true'));
    $('historyToggle').addEventListener('click', () => { state.historyAll = !state.historyAll; renderHistory(state.structure); });
    $('aporteRegisterBtn').addEventListener('click', openBatch);
    $('batchDate').addEventListener('change', onBatchDateChange);
    $('batchBody').addEventListener('input', onBatchInput);
    $('batchBody').addEventListener('change', onBatchInput);
    $('batchConfirmBtn').addEventListener('click', confirmBatch);
    $('batchCancelBtn').addEventListener('click', () => { closeBatch(); $('aporteRegisterBtn').focus(); });
    let collapsed = false;
    try { collapsed = localStorage.getItem(COLLAPSE_KEY) === '1'; } catch (e) { /* sem preferência salva */ }
    setAporteCollapsed(collapsed);
}
//#endregion

//#region Calculadora de aporte
function onAporteInput(ev) {
    const portfolio = current();
    if (!portfolio) return;
    writeAportePrefs(portfolio.id, {
        ...readAportePrefs(portfolio.id), amount: $('aporteAmount').value, mode: $('aporteMode').value,
        cap: $('aporteCap').value, maxAssets: $('aporteMax').value, advice: $('aporteAdvice').checked, reuse: $('aporteReuse').checked
    });
    closeBatch(); // a lista em conferência era do plano anterior
    // Valor sendo digitado: espera uma pausa antes de pedir o novo plano ao servidor.
    clearTimeout(state.amountTimer);
    if (ev?.type === 'input') state.amountTimer = setTimeout(renderAporte, AMOUNT_DELAY_MS);
    else renderAporte();
}

// Cartões do topo da calculadora: montados uma vez (cada um aparece ao rolar) e depois só os
// valores mudam, contando do valor anterior para o novo enquanto o aporte é digitado.
function renderAporteKpis(plan) {
    const box = $('aporteKpis');
    if (!$('apKpiTotal')) {
        const card = (id, label, tone, svg) => `<div class="kpi kpi-card ${tone}" id="${id}Card" data-reveal>
            <div class="kpi-top"><span class="kpi-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${svg}</svg></span><span class="kpi-label">${label}</span></div>
            <div class="kpi-value num" id="${id}">—</div><div class="kpi-note" id="${id}Note"></div></div>`;
        box.innerHTML = [
            card('apKpiTotal', 'Patrimônio atual', 'tone-blue', '<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M7 6V4h10v2"/>'),
            card('apKpiEstimated', 'Valor estimado após o aporte', 'tone-cyan', '<path d="M3 17l5-5 4 3 8-8M15 7h5v5"/>'),
            card('apKpiSuggested', 'Sugestão de aporte', 'tone-violet', '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>'),
        ].join('');
        observeReveal(box);
    }
    setCount($('apKpiTotal'), plan.total, { format: 'brl' });
    setCount($('apKpiEstimated'), plan.estimated, { format: 'brl' });
    $('apKpiEstimatedNote').innerText = `aporte de ${fmtBRL(plan.amount)}`;
    const suggested = $('apKpiSuggested');
    if (plan.waiting) { setCount(suggested, null); suggested.textContent = 'Aguardar'; }
    else setCount(suggested, plan.suggested, { format: 'brl' });
    $('apKpiSuggestedNote').innerText = plan.waiting ? 'nenhum ativo elegível agora'
        : `efetivo em cotas ${fmtBRL(plan.effective)} · sobra ${fmtBRL(plan.leftover)}`
          + (plan.unallocated > 0.004 ? ` · sem destino (teto) ${fmtBRL(plan.unallocated)}` : '');
    $('apKpiSuggestedCard').classList.toggle('tone-red', !!plan.waiting);
    $('apKpiSuggestedCard').classList.toggle('tone-violet', !plan.waiting);
}

// Pede ao servidor o plano dos campos do painel e desenha.
async function renderAporte() {
    const portfolio = current();
    if (!portfolio || !state.analysis) return;
    const prefs = readAportePrefs(portfolio.id);
    const mode = $('aporteMode').value;
    const seq = ++state.aporteSeq;
    let plan;
    try {
        plan = await apiPost(`/carteira/${portfolio.id}/aporte`, {
            amount: $('aporteAmount').value, mode, base: $('aporteBase').value, selected: prefs.selected,
            candidates: prefs.candidates, cap: $('aporteCap').value, maxAssets: $('aporteMax').value,
            advice: $('aporteAdvice').checked, reuse: $('aporteReuse').checked
        });
    } catch (e) {
        if (seq === state.aporteSeq) $('aporteNote').innerHTML = `<p class="warn-text">Não foi possível calcular o aporte (${escapeHTML(e.message)}).</p>`;
        return;
    }
    if (seq !== state.aporteSeq || portfolio !== current()) return; // os campos mudaram nesse meio-tempo
    state.plan = plan;
    const selected = new Set(prefs.selected);
    $('aporteBaseField').hidden = mode !== 'pullback';
    $('aporteTable').classList.toggle('picking', mode === 'escolher');

    renderAporteKpis(plan);

    // Explicação do resultado: modo, base da distribuição, redirecionamento e espera.
    const notes = [];
    if (!plan.amount) notes.push('Informe o valor que irá investir para ver a sugestão.');
    else if (plan.waiting) {
        notes.push(mode === 'pullback'
            ? `<strong>Aguardar:</strong> nenhum ativo da carteira está em pullback agora (base ${$('aporteBase').value === 'intradiario' ? 'intradiária' : 'último fechamento'}) e não há renda fixa elegível. Guarde os ${fmtBRL(plan.amount)} até surgir um setup.`
            : mode === 'escolher' ? '<strong>Aguardar:</strong> nenhum ativo elegível foi escolhido. Marque os ativos na tabela abaixo.'
            : '<strong>Aguardar:</strong> nenhum ativo elegível (todos com sinal de venda).');
    } else {
        notes.push(plan.basis === 'falta'
            ? 'Rebalanceamento só por aporte: o valor vai para as classes abaixo da meta, proporcional ao que falta a cada uma, e dentro da classe para os ativos mais longe do ideal. Nada é vendido: quem está acima da meta só deixa de receber até os outros alcançarem.'
            : 'Todas as classes com ativos elegíveis já estão na meta ou acima: a sugestão segue as metas delas (só compra, nada é vendido).');
        if (plan.redirected?.total > 0.004) {
            const where = plan.redirected.byClass.map(c => `${escapeHTML(c.cls)} ${fmtBRL(c.value)}`).join(', ');
            notes.push(`<strong>${fmtBRL(plan.redirected.total)}</strong> do aporte foram redirecionados para os elegíveis — é o que iria, no modo "todos", para ativos que ficaram de fora (${where}).`);
        }
        notes.push(...plan.notes.map(escapeHTML));
    }
    if (plan.sells.length) notes.push(`Sem aporte por sinal de venda: ${plan.sells.map(escapeHTML).join(', ')}.`);
    if (plan.targetsOff) notes.push(`<span class="warn-text">As metas das classes somam ${fmtNum(plan.targetSum, 2)}%, não 100%.</span>`);
    $('aporteNote').innerHTML = notes.map(n => `<p>${n}</p>`).join('');
    $('aporteWarnings').innerHTML = plan.warnings.map(w => `<span class="warn-text">${escapeHTML(w)}</span>`).join('<br>');

    // "Falta para o ideal": acima da meta não é venda, só não recebe aporte.
    const money = (v, above) => above ? '<span class="muted">acima da meta</span>'
        : `<span class="${signClass(Math.round(v * 100))}">${fmtBRL(v)}</span>`;
    // Cor de cada classe (a mesma da Alocação por classe e de Classes e metas), pela ordem do plano.
    const colorOf = new Map(plan.classRows.map((c, k) => [c.cls, classColor(c.cls, k)]));
    $('aporteClassBody').innerHTML = plan.classRows.map(c => `
        <tr>
            <td class="nowrap"><span class="swatch-box" style="background:${colorOf.get(c.cls)}"></span><strong>${escapeHTML(c.cls)}</strong></td>
            <td class="r nowrap">${fmtBRL(c.value)}</td>
            <td class="r nowrap">${fmtNum(c.pct, 2)}%</td>
            <td class="r nowrap">${fmtNum(c.target, 2)}%</td>
            <td class="r nowrap">${fmtBRL(c.ideal)}</td>
            <td class="r nowrap">${money(c.diff, c.above)}</td>
            <td class="r nowrap"><strong>${fmtBRL(c.suggestion)}</strong></td>
            <td>${allocBar(c.cls, c.pct, c.target, plan.amount ? c.pctAfter : null)}</td>
        </tr>`).join('') + `
        <tr class="total-row"><td><strong>Total</strong></td><td class="r nowrap"><strong>${fmtBRL(plan.total)}</strong></td><td class="r">100%</td>
            <td class="r nowrap">${fmtNum(plan.targetSum, 2)}%</td><td class="r nowrap"><strong>${fmtBRL(plan.estimated)}</strong></td>
            <td class="r nowrap">${fmtBRL(plan.gap)}</td><td class="r nowrap"><strong>${fmtBRL(plan.suggested)}</strong></td><td></td></tr>`;
    revealItems($('aporteClassBody'));

    const assetRow = (r, color) => {
        const candidate = r.status.key === 'candidate'; // ativo novo das sugestões do Screener
        const pick = `<td class="c pick"><input type="checkbox" data-pick="${escapeHTML(r.key)}" ${selected.has(r.key) || candidate ? 'checked' : ''} ${r.status.key === 'sell' || candidate ? 'disabled' : ''}
            aria-label="Incluir ${escapeHTML(r.label)} no aporte"></td>`;
        const native = r.kind === 'quote' && r.market !== 'b3' ? `<div class="small muted">${MARKETS[r.market].label}</div>`
            : candidate && r.name ? `<div class="small muted">${escapeHTML(r.name)}</div>` : '';
        const priority = r.priorityApplied ? ' <span class="badge badge-ok">Prioridade</span>' : '';
        const target = candidate ? `${fmtNum(r.target, 2)}%<div class="small muted">automática</div>` : targetField(r, 'tg');
        let suggestion;
        if (r.excluded) {
            suggestion = `<span class="muted">—</span><div class="small muted">${escapeHTML(r.excluded)}${r.wouldReceive > 0 ? `; receberia ${fmtBRL(r.wouldReceive)} no modo "todos"` : ''}</div>`;
        } else if (plan.waiting) {
            suggestion = '<span class="muted">Aguardar</span>';
        } else {
            suggestion = `<strong>${fmtBRL(r.suggestion)}</strong>`;
        }
        const shares = r.kind !== 'quote' ? '<span class="muted">—</span>'
            : r.shares === null ? '<span class="small muted">sem cotação</span>'
            : `${fmtQty(r.shares)}<div class="small muted">a ${fmtBRL(r.priceBRL)}</div>`;
        const extra = r.extraShares ? `<div class="small muted">+${fmtQty(r.extraShares)} cota(s) com a sobra</div>` : '';
        const effective = r.effective > 0 ? `${fmtBRL(r.effective)}${r.leftover > 0.004 ? `<div class="small muted">sobra ${fmtBRL(r.leftover)}</div>` : ''}${extra}` : '<span class="muted">—</span>';
        return `
        <tr>
            ${pick}
            <td><div class="asset"><span class="avatar solo" style="--a1:${color}" aria-hidden="true">${escapeHTML(r.label.slice(0, 2).toUpperCase())}</span>
                <div class="asset-text"><strong>${escapeHTML(r.label)}</strong>${priority}${native}</div></div></td>
            <td><span class="badge ${PLAN_BADGE[r.status.key]}">${escapeHTML(r.status.label)}</span></td>
            <td class="r nowrap">${fmtBRL(r.value)}</td>
            <td class="r nowrap">${fmtNum(r.pct, 2)}%</td>
            <td class="r nowrap">${target}</td>
            <td class="r nowrap">${fmtBRL(r.ideal)}</td>
            <td class="r nowrap">${money(r.diff, r.above)}</td>
            <td class="r">${suggestion}</td>
            <td class="r nowrap">${shares}</td>
            <td class="r nowrap">${effective}</td>
        </tr>`;
    };
    const groups = plan.classRows.filter(c => c.members.length).map(c => `
        <tr class="group-row"><td colspan="11"><span class="sticky-label"><span class="swatch-box" style="background:${colorOf.get(c.cls)}"></span><strong>${escapeHTML(c.cls)}</strong>
            <span class="target-chip">meta ${fmtNum(c.target, 2)}%</span> <span class="small muted">${c.members.length} ativo(s)</span></span></td></tr>
        ${c.members.map(r => assetRow(r, colorOf.get(c.cls))).join('')}
        <tr class="total-row">
            <td class="pick"></td><td><strong>Total ${escapeHTML(c.cls)}</strong></td><td></td>
            <td class="r nowrap"><strong>${fmtBRL(c.value)}</strong></td><td class="r nowrap">${fmtNum(c.pct, 2)}%</td>
            <td class="r nowrap">${fmtNum(c.target, 2)}%</td><td class="r nowrap">${fmtBRL(c.ideal)}</td>
            <td class="r nowrap">${money(c.diff, c.above)}</td><td class="r nowrap"><strong>${fmtBRL(c.suggestion)}</strong></td>
            <td></td><td class="r nowrap">${fmtBRL(c.effective)}</td>
        </tr>`);
    $('aporteAssetsBody').innerHTML = groups.length ? groups.join('')
        : `<tr><td colspan="11" class="empty-row">Nenhum ativo com posição.</td></tr>`;
    revealItems($('aporteAssetsBody'));

    // "Registrar compras sugeridas": só com ao menos um ativo cotado elegível com cotas > 0.
    $('aporteRegisterBtn').hidden = !plan.canRegister;
    if (!plan.canRegister && state.batch) closeBatch();
    renderCandidates(plan);
    renderAporteCharts(plan);
    renderAportePath(plan);
    renderAporteHistory(plan);
}
//#endregion

//#region Sugestões do Screener
// Sugestões do Screener: COMPRAR de hoje que a carteira não tem, por classe abaixo da meta.
function renderCandidates(plan) {
    const box = $('aporteCandidates');
    const groups = plan.candidates || [];
    if (!plan.screenerReady) {
        box.hidden = !groups.length && !plan.classRows.some(c => c.diff > 0.005 && c.target > 0);
        box.innerHTML = '<h3>Sugestões do Screener</h3><p class="small muted">Ranking do Screener indisponível agora: abra a aba Screener para gerar o de hoje e ver ativos para as classes abaixo da meta.</p>';
        return;
    }
    box.hidden = !groups.length;
    box.innerHTML = '<h3>Sugestões do Screener</h3>' + groups.map(g => {
        const head = `<p><strong>${escapeHTML(g.cls)}</strong> está ${fmtBRL(g.gap)} abaixo da meta${g.empty ? ' e ainda não tem ativos' : ''}. Com COMPRAR no ranking de hoje:</p>`;
        if (!g.rows.length) return `<div class="cand-group">${head}<p class="small muted">Nenhum ativo desta classe com COMPRAR no Screener hoje.</p></div>`;
        return `<div class="cand-group">${head}<div class="cand-list">${g.rows.map(r => `
            <label class="cand${r.chosen ? ' chosen' : ''}">
                <input type="checkbox" data-cand="${escapeHTML(r.ticker)}" ${r.chosen ? 'checked' : ''} aria-label="Incluir ${escapeHTML(r.ticker)} no aporte">
                <span class="cand-body">
                    <strong>${escapeHTML(r.ticker)}</strong>
                    <span>${signalBadge('COMPRAR', r.reason, 'badge-ok')}</span>
                    <span class="cand-name">${escapeHTML(r.name || '')}${r.sector ? ` · ${escapeHTML(r.sector)}` : ''}</span>
                    <span class="small muted">${escapeHTML(r.kindLabel)} · ${r.rank}º · Score ${fmtNum(r.score)} · ${fmtBRL(r.price)}</span>
                </span>
            </label>`).join('')}</div></div>`;
    }).join('');
    revealItems(box, '.cand');
}

function onCandidateChange(ev) {
    const portfolio = current();
    const ticker = ev.target.dataset?.cand;
    if (!portfolio || !ticker) return;
    const prefs = readAportePrefs(portfolio.id);
    const set = new Set(prefs.candidates);
    if (ev.target.checked) set.add(ticker); else set.delete(ticker);
    writeAportePrefs(portfolio.id, { ...prefs, candidates: [...set] });
    closeBatch();
    renderAporte();
}

// ---------------------------------------------------------------- Gráficos do aporte
//#endregion

//#region Gráficos da calculadora
// Gráficos da calculadora: cada um é criado quando aparece na tela (whenVisible, com a animação de
// entrada) e depois atualizado no lugar a cada novo plano, então barras e fatias deslizam até os
// valores novos enquanto o valor do aporte é digitado.
const APORTE_ANIMATION = { duration: 700, easing: 'easeOutQuart' };

function aporteChart(canvasId, config) {
    config.options.animation = APORTE_ANIMATION;
    const chart = state.aporteCharts[canvasId];
    if (chart) {
        chart.data = config.data;
        chart.options = config.options;
        chart.update();
        return;
    }
    whenVisible(canvasId, () => {
        state.aporteCharts[canvasId]?.destroy();
        state.aporteCharts[canvasId] = new Chart($(canvasId), config);
    });
}

function dropAporteChart(canvasId) {
    if (state.aporteCharts[canvasId] && $('chartTip')) $('chartTip').hidden = true; // tooltip flutuante das roscas
    state.aporteCharts[canvasId]?.destroy();
    delete state.aporteCharts[canvasId];
    whenVisible(canvasId, () => {}); // cancela uma criação que esperava aparecer
}

const pctTick = (v) => `${fmtNum(v, 0)}%`;

const chartTheme = () => ({ text: cssVar('--text-color'), muted: cssVar('--text-muted'), grid: CHART_GRID });

const tooltipStyle = () => chartTooltip();

// Tooltip em HTML (position: fixed) para gráficos pequenos demais para o tooltip do canvas:
// "Classe: 12,34%", preso dentro da tela. Some ao rolar a página.
function floatingTooltip({ chart, tooltip }) {
    let el = $('chartTip');
    if (!el) {
        el = document.createElement('div');
        el.id = 'chartTip';
        el.className = 'chart-tip';
        el.setAttribute('role', 'tooltip');
        document.body.appendChild(el);
        window.addEventListener('scroll', () => { el.hidden = true; }, { passive: true });
        // No celular, tocar fora do gráfico fecha (o Chart.js só vê toques no próprio canvas).
        document.addEventListener('pointerdown', (ev) => { if (ev.target.tagName !== 'CANVAS') el.hidden = true; });
    }
    // Só o gráfico que abriu o tooltip o fecha: ao tocar de uma rosca para outra, a primeira
    // manda "esconder" depois que a segunda já mostrou o dela.
    if (!tooltip.opacity || !tooltip.dataPoints?.length) {
        if (el.owner === chart) el.hidden = true;
        return;
    }
    el.owner = chart;
    el.innerHTML = tooltip.dataPoints.map(dp =>
        `<span class="swatch-box" style="background:${dp.dataset.backgroundColor[dp.dataIndex]}"></span><strong>${escapeHTML(dp.label)}</strong>: ${fmtNum(dp.raw)}%`).join('<br>');
    el.hidden = false;
    const box = chart.canvas.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight, margin = 8;
    const left = Math.min(Math.max(margin, box.left + tooltip.caretX - w / 2), window.innerWidth - w - margin);
    const above = box.top + tooltip.caretY - h - 10;
    el.style.left = `${left}px`;
    el.style.top = `${above >= margin ? above : box.top + tooltip.caretY + 10}px`;
}

// Gráficos A (classe: atual × após × meta), D (roscas) e B (por ativo). Tudo vem do plano.
const APORTE_CHART_IDS = ['aporteClassChart', 'aporteDonutNow', 'aporteDonutAfter', 'aporteDonutTarget', 'aporteAssetChart'];

function renderAporteCharts(plan) {
    const show = plan.amount > 0 && !plan.waiting && plan.total + plan.amount > 0;
    $('aporteChartsBlock').hidden = !show;
    if (!show) { APORTE_CHART_IDS.forEach(dropAporteChart); return; }
    const t = chartTheme();
    const classes = plan.classRows.filter(c => c.target > 0 || c.value > 0 || c.effective > 0);
    const labels = classes.map(c => c.cls);

    aporteChart('aporteClassChart', {
        type: 'bar',
        data: {
            labels,
            datasets: [
                ...[['Atual', 'pct', '#94a3b8'], ['Após o aporte', 'pctAfter', '#5aa2ff'], ['Meta', 'target', '#ffc65c']].map(([label, key, color]) => ({
                    label, data: classes.map(c => c[key]), backgroundColor: horizontalGradient(color, 0.45, 1), hoverBackgroundColor: color,
                    borderRadius: 6, borderSkipped: false, maxBarThickness: 16, categoryPercentage: 0.7, barPercentage: 0.85
                }))
            ]
        },
        options: {
            indexAxis: 'y', responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: t.text, usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 12 } },
                tooltip: { ...tooltipStyle(t), callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtNum(ctx.raw)}%` } }
            },
            scales: {
                x: { beginAtZero: true, ticks: { color: t.muted, callback: pctTick }, grid: { color: t.grid }, border: { display: false } },
                y: { ticks: { color: t.text }, grid: { display: false }, border: { color: t.grid } }
            }
        }
    });

    const donut = (canvasId, values) => {
        if (!values.some(v => v > 0)) { dropAporteChart(canvasId); return; }
        aporteChart(canvasId, {
            type: 'doughnut',
            data: { labels, datasets: [{ data: values, backgroundColor: classes.map((c, k) => classColor(c.cls, k)), borderColor: cssVar('--panel-bg'), borderWidth: 3, hoverOffset: 6, borderRadius: 4 }] },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: '68%', layout: { padding: 6 },
                // A rosca é pequena (no celular, ~100 px): o tooltip vai fora do canvas, sem ser cortado.
                plugins: { legend: { display: false }, tooltip: { enabled: false, external: floatingTooltip } }
            }
        });
    };
    donut('aporteDonutNow', classes.map(c => c.pct));
    donut('aporteDonutAfter', classes.map(c => c.pctAfter));
    donut('aporteDonutTarget', classes.map(c => c.target));
    $('aporteDonutLegend').innerHTML = classes.map((c, k) =>
        `<span class="nowrap"><span class="swatch-box" style="background:${classColor(c.cls, k)}"></span>${escapeHTML(c.cls)}</span>`).join('');

    // Por ativo: saldo atual + aporte + o que ainda falta para o ideal, empilhados.
    const assets = plan.classRows.flatMap(c => c.members).filter(r => r.value > 0 || r.effective > 0 || r.gapAfter > 0.004);
    $('aporteAssetWrap').style.height = `${Math.max(200, assets.length * 30 + 80)}px`;
    aporteChart('aporteAssetChart', {
        type: 'bar',
        data: {
            labels: assets.map(r => r.label),
            datasets: [
                { label: 'Saldo atual', data: assets.map(r => r.value), backgroundColor: horizontalGradient('#4f8dff', 0.55, 1), hoverBackgroundColor: '#4f8dff' },
                { label: 'Aporte', data: assets.map(r => r.effective), backgroundColor: horizontalGradient('#3ddc97', 0.6, 1), hoverBackgroundColor: '#3ddc97' },
                { label: 'Ainda falta para o ideal', data: assets.map(r => r.gapAfter), backgroundColor: 'rgba(148, 163, 184, 0.16)', hoverBackgroundColor: 'rgba(148, 163, 184, 0.3)' }
            ].map(d => ({ ...d, stack: 'a', borderRadius: 5, borderSkipped: false, maxBarThickness: 18, borderWidth: 0 }))
        },
        options: {
            indexAxis: 'y', responsive: true, maintainAspectRatio: false,
            // Barras deitadas: o toque escolhe o ativo pela posição vertical (axis 'y').
            interaction: { mode: 'index', axis: 'y', intersect: false },
            plugins: {
                legend: { labels: { color: t.text, usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 12 } },
                tooltip: { ...tooltipStyle(t), callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtBRL(ctx.raw)}` } }
            },
            scales: {
                x: { stacked: true, beginAtZero: true, ticks: { color: t.muted, maxRotation: 0, autoSkip: true, callback: (v) => fmtCompactBRL(v) }, grid: { color: t.grid }, border: { display: false } },
                y: { stacked: true, ticks: { color: t.text }, grid: { display: false }, border: { color: t.grid } }
            }
        }
    });
}

// Plano de vários aportes (gráfico C e a tabela mês a mês), por classe.
function renderAportePath(plan) {
    const path = plan.path;
    const show = !!path && !plan.waiting;
    $('aportePathBlock').hidden = !show;
    if (!show) { dropAporteChart('aportePathChart'); return; }
    const t = chartTheme();
    const classes = plan.classRows.filter(c => c.target > 0 || c.value > 0);
    const steps = [path.start, ...path.months];
    const labels = ['Hoje', ...path.months.map(m => `${m.n}º`)];
    $('aportePathSummary').innerText = path.summary;
    // Sem aportes até o equilíbrio (as classes já estão na meta): o gráfico teria só o ponto
    // "Hoje"; fica só a frase e a tabela.
    $('aportePathChart').parentElement.hidden = !path.months.length;
    if (!path.months.length) dropAporteChart('aportePathChart');
    const lines = classes.flatMap((c, k) => [
        { label: c.cls, data: steps.map(s => s.pct[c.cls]), borderColor: classColor(c.cls, k), backgroundColor: classColor(c.cls, k), borderWidth: 2.5,
          pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: cssVar('--panel-bg'), pointBorderWidth: 2, tension: 0.25 },
        { label: `${c.cls} (meta)`, data: steps.map(() => c.target), borderColor: hexAlpha(classColor(c.cls, k), 0.6), borderWidth: 1.2, borderDash: [5, 4], pointRadius: 0 }
    ]);
    if (path.months.length) aporteChart('aportePathChart', {
        type: 'line',
        data: { labels, datasets: lines },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: t.text, usePointStyle: true, pointStyle: 'line', boxWidth: 24, filter: (item) => !item.text.endsWith('(meta)') } },
                tooltip: {
                    ...tooltipStyle(t), filter: (item) => !item.dataset.label.endsWith('(meta)'),
                    callbacks: {
                        title: (items) => items[0].dataIndex ? `Após o ${items[0].dataIndex}º aporte` : 'Hoje',
                        label: (ctx) => ` ${ctx.dataset.label}: ${fmtNum(ctx.raw)}% (meta ${fmtNum(classes[Math.floor(ctx.datasetIndex / 2)].target)}%)`
                    }
                }
            },
            scales: {
                x: { ticks: { color: t.muted, maxRotation: 0, autoSkip: true }, grid: { display: false }, border: { color: t.grid } },
                y: { beginAtZero: true, ticks: { color: t.muted, callback: pctTick }, grid: { color: t.grid }, border: { display: false } }
            }
        }
    });
    $('aportePathHead').innerHTML = `<tr><th>Aporte</th>${classes.map(c => `<th class="r">${escapeHTML(c.cls)}</th>`).join('')}<th class="r">Maior desvio da meta</th></tr>`;
    $('aportePathBody').innerHTML = path.months.length ? path.months.map(m => `<tr><td>${m.n}º</td>${classes.map(c =>
        `<td class="r nowrap">${fmtBRL(m.parts[c.cls] || 0)}<div class="small muted">${fmtNum(m.pct[c.cls])}%</div></td>`).join('')}
        <td class="r nowrap">${fmtNum(m.deviation, 1)} p.p.</td></tr>`).join('')
        : `<tr><td colspan="${classes.length + 2}" class="empty-row">Nenhum aporte necessário para equilibrar.</td></tr>`;
    revealItems($('aportePathBody'));
}

// Histórico de aportes: os 12 meses mais recentes com lançamentos, por classe.
function renderAporteHistory(plan) {
    const rows = plan.history || [];
    $('aporteHistoryBlock').hidden = !rows.length;
    $('aporteHistoryBody').innerHTML = rows.map(h => {
        const parts = Object.entries(h.byClass).map(([c, v]) => `<span class="nowrap">${escapeHTML(c)} <span class="${signClass(v)}">${signedBRL(v)}</span></span>`).join(' · ');
    revealItems($('aporteHistoryBody'));
        const adherence = h.adherence === null ? '<span class="muted">—</span>'
            : `${fmtNum(h.adherence, 0)}%${h.above.length ? `<div class="small muted">acima da meta: ${h.above.map(escapeHTML).join(', ')}</div>` : ''}`;
        return `<tr><td class="nowrap">${fmtMonth(h.month)}</td><td class="r nowrap ${signClass(h.total)}">${signedBRL(h.total)}</td><td>${parts}</td><td class="r">${adherence}</td></tr>`;
    }).join('');
}

// ---------------------------------------------------------------- Calculadora recolhível
//#endregion

//#region Registrar compras sugeridas
function setAporteCollapsed(collapsed) {
    $('aporteBody').hidden = collapsed;
    $('aporteToggle').setAttribute('aria-expanded', String(!collapsed));
    $('aporteToggle').innerText = collapsed ? 'Expandir' : 'Recolher';
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0'); } catch (e) { /* preferência não salva */ }
    if (!collapsed) for (const c of Object.values(state.aporteCharts)) { c.resize(); c.update('none'); } // desenhados enquanto escondidos
}

// ---------------------------------------------------------------- Registrar compras sugeridas
// Fluxo: openBatch monta a lista com as linhas que vieram no plano exibido → o usuário confere e
// edita (onBatchInput; os totais vêm do servidor) → confirmBatch manda tudo numa única ação; o
// servidor valida cada linha incluída e grava tudo de uma vez, ou devolve os erros por linha.

function openBatch() {
    const plan = state.plan;
    if (!plan || !state.structure) return;
    state.batch = { date: state.structure.today, amount: plan.amount, rows: plan.batchRows.map(r => ({ ...r, error: null })) };
    setMsg('aporteMsg', '', '');
    setMsg('batchMsg', '', '');
    renderBatch();
    $('batchDate').focus();
}

function closeBatch() {
    if (!state.batch) return;
    state.batch = null;
    renderBatch();
}

const numInput = (k, field, value, label, step = 'any') =>
    `<input type="number" class="select num-input" data-batch="${k}" data-field="${field}" min="0" step="${step}" inputmode="decimal" value="${escapeHTML(value ?? '')}" aria-label="${escapeHTML(label)}">`;

function renderBatch() {
    const b = state.batch;
    $('batchPanel').hidden = !b;
    $('aporteRegisterBtn').setAttribute('aria-expanded', String(!!b));
    if (!b) return;
    const today = state.structure.today;
    $('batchDate').value = b.date;
    $('batchDate').max = today;
    $('batchBody').innerHTML = b.rows.length ? b.rows.map((r, k) => {
        const err = `<div class="small neg" id="batch-err-${k}">${r.error ? escapeHTML(r.error) : ''}</div>`;
        const include = `<td class="c"><input type="checkbox" data-batch="${k}" data-field="include" ${r.include ? 'checked' : ''} aria-label="Incluir ${escapeHTML(r.label)}"></td>`;
        const date = `<td><input type="date" class="select" data-batch="${k}" data-field="date" value="${escapeHTML(r.date)}" max="${today}" aria-label="Data de ${escapeHTML(r.label)}"></td>`;
        const total = `<td class="r nowrap" id="batch-total-${k}"></td>`;
        if (r.kind === 'fixed') {
            return `<tr class="${r.error ? 'batch-error' : ''}">${include}
                <td><strong>${escapeHTML(r.label)}</strong><div class="small muted">Aporte em renda fixa</div>${err}</td>${date}
                <td colspan="4" class="r">${numInput(k, 'amount', r.amount, `Valor do aporte em ${r.label} (R$)`, '0.01')}</td>${total}</tr>`;
        }
        const currency = MARKETS[r.market].currency;
        return `<tr class="${r.error ? 'batch-error' : ''}">${include}
            <td><strong>${escapeHTML(r.label)}</strong><div class="small muted">Compra · ${MARKETS[r.market].label}</div>${err}</td>${date}
            <td class="r">${numInput(k, 'qty', r.qty, `Quantidade de ${r.label}`)}</td>
            <td class="r">${numInput(k, 'price', r.price, `Preço de ${r.label} (${currency})`)}<div class="small muted">${currency}</div></td>
            <td class="r">${r.market === 'b3' ? '<span class="muted">—</span>' : numInput(k, 'fx', r.fx, `Câmbio de ${r.label} (R$ por US$)`)}</td>
            <td class="r">${numInput(k, 'fees', r.fees, `Taxas de ${r.label} (${currency})`)}</td>${total}</tr>`;
    }).join('') : '<tr><td colspan="8" class="empty-row">Nenhuma compra sugerida.</td></tr>';
    updateBatchTotals();
}

// Atualiza os totais sem redesenhar os campos (o foco continua onde o usuário está digitando).
async function updateBatchTotals() {
    const b = state.batch;
    if (!b) return;
    const seq = ++state.batchSeq;
    let t;
    try {
        t = await apiPost('/lote/totais', { amount: b.amount, rows: b.rows });
    } catch (e) {
        if (seq === state.batchSeq) $('batchSummary').innerText = `Não foi possível somar a lista (${e.message}).`;
        return;
    }
    if (seq !== state.batchSeq || b !== state.batch) return;
    t.rows.forEach((v, k) => { $(`batch-total-${k}`).innerText = v === null ? '—' : fmtBRL(v); });
    $('batchSummary').innerHTML = `<strong>Total da lista: ${fmtBRL(t.total)}</strong>${t.complete ? '' : ' (há linhas incompletas)'} · ${t.included} lançamento(s) incluído(s) · aporte informado ${fmtBRL(b.amount)} · `
        + (Math.abs(t.diff) < 0.005 ? 'sem diferença' : t.diff > 0 ? `sobram ${fmtBRL(t.diff)}` : `<span class="warn-text">excede em ${fmtBRL(-t.diff)}</span>`);
}

// Guarda o que foi digitado como texto; o servidor interpreta os números.
function onBatchInput(ev) {
    const el = ev.target, b = state.batch;
    const k = el.dataset?.batch;
    if (!b || k === undefined) return;
    const r = b.rows[+k], field = el.dataset.field;
    r[field] = field === 'include' ? el.checked : el.value;
    updateBatchTotals();
}

// Data no topo: vale para todas as linhas (cada uma ainda pode ser alterada depois).
function onBatchDateChange() {
    if (!state.batch) return;
    state.batch.date = $('batchDate').value;
    for (const r of state.batch.rows) r.date = state.batch.date;
    renderBatch();
}

async function confirmBatch() {
    const b = state.batch;
    if (!current() || !b) return;
    setMsg('batchMsg', 'Salvando...', '');
    const res = await act('registrar_lote', { rows: b.rows.map(({ error, ...r }) => r) });
    if (res.error) {
        if (state.batch !== b) return;
        const errors = res.data?.rowErrors || {};
        b.rows.forEach((r, k) => { r.error = errors[k] || null; });
        if (Object.keys(errors).length) renderBatch();
        return setMsg('batchMsg', res.error, 'error');
    }
    closeBatch();
    setSavedMsg('aporteMsg', res.message);
}
//#endregion

//#region Histórico de lançamentos
// Histórico de lançamentos (já em ordem do mais recente, vindo da estrutura da carteira).
function renderHistory(st) {
    const rows = (st?.history || []).map(r => r.kind === 'tx' ? {
        ...r, qty: fmtQty(r.quantity), price: fmtMoney(r.price, r.currency),
        total: fmtMoney(r.gross, r.currency) + (r.fees ? `<div class="small muted">taxas ${fmtMoney(r.fees, r.currency)}</div>` : '')
            + (r.fx !== 1 ? `<div class="small muted">câmbio ${fmtNum(r.fx, 4)} · ${fmtBRL(r.flowBRL)}</div>` : '')
    } : { ...r, qty: '—', price: '—', total: fmtBRL(r.amount) });
    const toggle = $('historyToggle');
    toggle.hidden = rows.length <= HISTORY_LIMIT;
    toggle.setAttribute('aria-expanded', String(state.historyAll));
    toggle.innerText = state.historyAll ? 'Ver menos' : `Ver todos (${rows.length})`;
    const shown = state.historyAll ? rows : rows.slice(0, HISTORY_LIMIT);
    // Classe de cada ativo/título (para a cor do avatar), pela estrutura da carteira.
    const classOf = new Map();
    (st?.classes || []).forEach((c, k) => c.members.forEach(m => classOf.set(m, [c.name, k])));
    $('historyBody').innerHTML = shown.length ? shown.map(r => {
        const kind = r.type.toLowerCase().split(' ')[0];
        const [cls, k] = classOf.get(r.asset) || ['', 0];
        return `
        <tr class="tl-row tl-${ENTRY_DIRECTION[kind] || 'other'}">
            <td class="nowrap"><div class="tl"><span class="tl-dot" aria-hidden="true"></span>
                <div><strong>${r.date.slice(8, 10)}/${r.date.slice(5, 7)}</strong><div class="small muted">${r.date.slice(0, 4)}</div></div></div></td>
            <td><div class="asset"><span class="avatar solo" style="--a1:${classColor(cls, k)}" aria-hidden="true">${escapeHTML(r.asset.slice(0, 2).toUpperCase())}</span>
                <div class="asset-text"><strong>${escapeHTML(r.asset)}</strong>${cls ? `<div class="small muted">${escapeHTML(cls)}</div>` : ''}</div></div></td>
            <td><span class="badge entry-pill ${ENTRY_PILL[kind] || 'badge-low'}">${ENTRY_ARROW[ENTRY_DIRECTION[kind]] || ''}${r.type}</span></td>
            <td class="r nowrap">${r.qty}</td>
            <td class="r nowrap">${r.price}</td>
            <td class="r nowrap"><strong>${r.total}</strong></td>
            <td class="c"><button type="button" class="icon-btn icon-danger" data-kind="${r.kind}" data-id="${escapeHTML(r.id)}" ${r.item ? `data-item="${escapeHTML(r.item)}"` : ''}
                title="Excluir" aria-label="Excluir ${r.type.toLowerCase()} de ${escapeHTML(r.asset)} em ${fmtDate(r.date)}"><svg viewBox="0 0 24 24" aria-hidden="true"><g class="lid"><path d="M4 7h16M9 7V4h6v3"/></g><path d="M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg></button></td>
        </tr>`;
    }).join('')
        : `<tr><td colspan="7" class="empty-row">Nenhum lançamento.</td></tr>`;
    revealItems($('historyBody'));
}

async function onHistoryClick(ev) {
    const btn = ev.target.closest?.('button[data-id]');
    if (!btn || !confirm('Excluir este lançamento?')) return;
    const { kind, id, item } = btn.dataset;
    const res = await act('excluir_lancamento', { kind, entryId: id, itemId: item });
    if (res.error && !res.saveFailed) alert(res.error);
}
//#endregion

//#region Nova operação
// Enquanto uma venda é digitada em "Nova operação": como o mês fica com ela (GET /imposto).
let txTaxTimer = null;

function scheduleTxTax() {
    clearTimeout(txTaxTimer);
    txTaxTimer = setTimeout(updateTxTax, 300);
}

async function updateTxTax() {
    const portfolio = current();
    const box = $('txTaxAlert');
    if (!portfolio || $('txType').value !== 'venda') { renderTaxAlert(box, null); return; }
    const seq = ++state.taxSeq;
    const q = new URLSearchParams({ ticker: $('txTicker').value, market: $('txMarket').value, type: 'venda', date: $('txDate').value,
                                    quantity: $('txQty').value, price: $('txPrice').value, fx: $('txFx').value });
    try {
        const r = await apiGet(`/carteira/${portfolio.id}/imposto?${q}`);
        if (seq === state.taxSeq) renderTaxAlert(box, r.alert);
    } catch (e) {
        if (seq === state.taxSeq) renderTaxAlert(box, null);
    }
}

// Ao informar ativo, mercado e data: o servidor normaliza o ticker, diz se é novo (mostra classe
// e mercado), sugere a classe e devolve o fechamento do dia na moeda do ativo e, fora da B3, o
// câmbio USDBRL do dia (ambos editáveis).
async function onTxInputChange() {
    const portfolio = current();
    if (!portfolio) return;
    const seq = ++state.txSeq;
    const date = $('txDate').value;
    const hint = $('txPriceHint');
    let info;
    try {
        info = await apiGet(`/carteira/${portfolio.id}/ativo?ticker=${encodeURIComponent($('txTicker').value)}&market=${$('txMarket').value}`);
    } catch (e) {
        if (seq === state.txSeq) hint.innerText = `Não foi possível consultar o ativo (${e.message})`;
        return;
    }
    if (seq !== state.txSeq) return; // campos mudaram
    $('txTicker').value = info.ticker;
    $('txClassField').hidden = $('txMarketField').hidden = !info.isNew;
    if (info.isNew && !state.classTouched) $('txClass').value = info.suggestedClass;
    const foreign = info.market !== 'b3';
    $('txFxField').hidden = !foreign;
    $('txPriceLabel').innerText = `Preço (${info.currency})`;
    $('txFeesLabel').innerText = `Taxas (${info.currency})`;
    if (!foreign) $('txFx').value = '';
    if (!info.ticker) { hint.innerText = ''; return; }
    let q;
    try {
        q = await apiGet(`/cotacao?ticker=${encodeURIComponent(info.ticker)}&market=${info.market}&date=${encodeURIComponent(date)}`);
    } catch (e) {
        if (seq === state.txSeq) hint.innerText = `${info.ticker}: sem histórico (${e.message})`;
        return;
    }
    if (seq !== state.txSeq) return;
    if (q.error) hint.innerText = `${info.ticker}: sem histórico (${q.error})`;
    else if (q.close) {
        $('txPrice').value = q.close.price.toFixed(foreign ? 4 : 2);
        scheduleTxTax();
        hint.innerText = `Fechamento de ${fmtDate(q.close.date)} (editável)`;
    } else {
        hint.innerText = 'Data anterior ao histórico: informe o preço';
    }
    if (!foreign) return;
    if (q.fxError) $('txFxHint').innerText = `Câmbio indisponível (${q.fxError}): informe-o`;
    else if (q.fx) { $('txFx').value = q.fx.price.toFixed(4); $('txFxHint').innerText = `USDBRL de ${fmtDate(q.fx.date)} (editável)`; }
    else $('txFxHint').innerText = 'Informe o câmbio da operação';
}

async function submitTransaction(ev) {
    ev.preventDefault();
    if (!current()) return;
    setMsg('txMsg', 'Salvando...', '');
    const res = await act('nova_operacao', {
        ticker: $('txTicker').value, market: $('txMarket').value, cls: $('txClass').value, type: $('txType').value,
        date: $('txDate').value, quantity: $('txQty').value, price: $('txPrice').value, fees: $('txFees').value, fx: $('txFx').value
    });
    if (res.error) return setMsg('txMsg', res.error, 'error');
    setSavedMsg('txMsg', res.message);
    renderTaxAlert($('txTaxAlert'), res.taxAlert);   // a venda registrada deixou o mês perto ou acima da isenção
    for (const id of ['txTicker', 'txQty', 'txPrice', 'txFx']) $(id).value = '';
    $('txFees').value = '0';
    $('txPriceHint').innerText = $('txFxHint').innerText = '';
    $('txClassField').hidden = $('txMarketField').hidden = $('txFxField').hidden = true;
    $('txMarket').value = 'b3';
    $('txPriceLabel').innerText = 'Preço (R$)';
    $('txFeesLabel').innerText = 'Taxas (R$)';
    state.classTouched = false;
}
//#endregion

//#region Renda fixa
function renderFixedIncomeSelect(st) {
    const items = st?.fixedIncome || [];
    const select = $('fiItem');
    const keep = select.value;
    select.innerHTML = items.length
        ? items.map(f => `<option value="${escapeHTML(f.id)}">${escapeHTML(f.name)}</option>`).join('')
        : '<option value="">Crie um título primeiro</option>';
    if (items.some(f => f.id === keep)) select.value = keep;
}

async function submitFixedIncomeItem(ev) {
    ev.preventDefault();
    const res = await act('novo_titulo', { name: $('fiName').value, cls: $('fiClass').value });
    if (res.error) return setMsg('fiMsg', res.error, 'error');
    $('fiName').value = '';
    $('fiItem').value = res.itemId;
    setMsg('fiMsg', res.message, 'ok');
}

async function submitFixedIncomeEntry(ev) {
    ev.preventDefault();
    const res = await act('lancamento_titulo', {
        itemId: $('fiItem').value, date: $('fiDate').value, type: $('fiType').value, amount: $('fiAmount').value
    });
    if (res.error) return setMsg('fiMsg', res.error, 'error');
    $('fiAmount').value = '';
    setSavedMsg('fiMsg', res.message);
}

// ---------------------------------------------------------------- Metas
//#endregion
