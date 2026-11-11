// Carteira → Configurações: "Classes e metas" (metas das classes, rosca, mover ativo) e "Meta
// por ativo". O campo de meta (targetField) e o salvar (onAporteTableChange) ficam em carteira.js,
// porque a calculadora de aporte também os usa.

//#region Eventos
// Metas das classes, mover ativo e a tabela de meta por ativo.
function setupConfiguracoes() {
    $('allocationBody').addEventListener('change', onAllocationChange);
    $('moveForm').addEventListener('submit', submitMove);
    $('assetTargetsBody').addEventListener('change', onAporteTableChange);
}
//#endregion

//#region Classes e metas
// Configurações: as classes fixas (as mesmas da aba Configurações do site), com a meta editável, o
// que há em cada uma e a regra de venda que a "Situação" segue. Usa só a estrutura da carteira (não
// precisa de cotações), então funciona antes do primeiro lançamento.
function renderClassConfig(st) {
    const classes = st?.classes || [];
    $('allocationBody').innerHTML = classes.map((c, k) => `
        <tr>
            <td class="nowrap"><span class="swatch-box" style="background:${classColor(c.name, k)}"></span><strong>${escapeHTML(c.name)}</strong></td>
            <td class="r nowrap"><label class="sr-only" for="target-${k}">Meta de ${escapeHTML(c.name)} (%)</label>
                <input type="number" class="select target-input" id="target-${k}" data-class="${escapeHTML(c.name)}" min="0" max="100" step="0.5" value="${c.target}"></td>
            <td class="small" data-label="Ativos e títulos">${c.members.length ? `<span class="member-chips">${c.members.map(m => `<span class="member-chip">${escapeHTML(m)}</span>`).join('')}</span>` : '<span class="muted">vazia</span>'}</td>
            <td class="small muted" data-label="Regra de venda">${escapeHTML(c.rule || '')}</td>
        </tr>`).join('') + `
        <tr><td><strong>Total</strong></td><td class="r nowrap"><strong>${fmtNum(st?.targetSum || 0)}%</strong></td><td colspan="2"></td></tr>`;
    revealItems($('allocationBody'));
    renderTargetsDonut(st);
    const warn = $('targetsWarning');
    const off = !!st?.targetsOff;
    warn.style.color = off ? 'var(--warning)' : 'var(--text-muted)';
    warn.innerText = off ? `As metas somam ${fmtNum(st.targetSum)}%, não 100%. Ajuste os valores para que "para atingir a meta" e o aporte façam sentido.`
        : 'A meta é salva na carteira ao editar.';
}

// Configurações: rosca das metas das classes (a soma, que deve dar 100%, no meio).
function renderTargetsDonut(st) {
    const classes = (st?.classes || []).filter(c => c.target > 0);
    $('targetsTotal').innerText = st ? `${fmtNum(st.targetSum)}%` : '—';
    $('targetsTotal').className = `num ${st?.targetsOff ? 'warn-text' : ''}`;
    if (typeof Chart === 'undefined') return;
    whenVisible('targetsDonut', () => {
        state.targetsChart?.destroy();
        state.targetsChart = null;
        if (!classes.length) return;
        state.targetsChart = new Chart($('targetsDonut'), {
            type: 'doughnut',
            data: {
                labels: classes.map(c => c.name),
                datasets: [{ data: classes.map(c => c.target), backgroundColor: classes.map((c, k) => classColor(c.name, k)),
                             borderColor: cssVar('--panel-bg'), borderWidth: 3, hoverOffset: 8 }]
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: '64%',
                animation: { animateRotate: true, animateScale: false, duration: 1000, easing: 'easeOutQuart' },
                plugins: { legend: { display: false },
                           tooltip: { ...chartTooltip(), callbacks: { label: (ctx) => ` ${ctx.label}: ${fmtNum(ctx.parsed)}%` } } }
            }
        });
    });
}
//#endregion

//#region Meta por ativo
// Configurações: meta de cada ativo com posição, agrupada por classe (mesmo cálculo do Aporte).
function renderAssetTargets(a) {
    if (!a) {
        $('assetTargetsBody').innerHTML = '<tr><td colspan="3" class="empty-row">Registre uma operação para definir a meta de cada ativo.</td></tr>';
        revealItems($('assetTargetsBody'));
        $('assetTargetsWarnings').innerHTML = '';
        return;
    }
    const { groups, warnings } = a.assetTargets;
    $('assetTargetsBody').innerHTML = groups.map(g => {
        const k = Math.max(0, a.allocation.findIndex(r => r.cls === g.cls));
        const color = classColor(g.cls, k);
        // Escala das barras: o maior % (atual ou meta) da classe enche a barra.
        const scale = Math.max(...g.members.flatMap(i => [i.pct, i.target]), 0.01);
        return `<tr class="group-row"><td colspan="3"><span class="sticky-label"><span class="swatch-box" style="background:${color}"></span><strong>${escapeHTML(g.cls)}</strong>
            <span class="target-chip">meta da classe ${fmtNum(g.target, 2)}%</span></span></td></tr>`
        + g.members.map(i => `
        <tr>
            <td><div class="asset"><span class="avatar solo" style="--a1:${color}" aria-hidden="true">${escapeHTML(i.label.slice(0, 2).toUpperCase())}</span>
                <div class="asset-text"><strong>${escapeHTML(i.label)}</strong></div></div></td>
            <td class="r nowrap"><div class="target-cmp">
                <span class="num ${i.above ? 'warn-text' : ''}">${fmtNum(i.pct, 2)}%</span>
                <span class="target-bar${i.above ? ' above' : ''}" role="img" aria-label="${escapeHTML(i.label)}: ${fmtNum(i.pct, 2)}% atual, meta ${fmtNum(i.target, 2)}%${i.above ? ', acima da meta' : ''}">
                    <i style="--w:${(i.pct / scale).toFixed(3)}"></i><b style="left:calc(${(i.target / scale * 100).toFixed(2)}% - 1px)"></b></span>
                <span class="small ${i.above ? 'warn-text' : 'muted'}">${i.above ? 'acima da meta' : 'abaixo ou na meta'}</span></div></td>
            <td class="r nowrap">${targetField(i, 'cfg-tg')}</td>
        </tr>`).join('');
    }).join('') || '<tr><td colspan="3" class="empty-row">Nenhum ativo com posição.</td></tr>';
    revealItems($('assetTargetsBody'));
    $('assetTargetsWarnings').innerHTML = warnings.map(w => `<span class="warn-text">${escapeHTML(w)}</span>`).join('<br>');
}
//#endregion

//#region Alterações
// Edição da meta de uma classe na tabela de Configurações.
async function onAllocationChange(ev) {
    const input = ev.target;
    if (!current() || input.dataset?.class === undefined) return;
    const previous = state.structure?.classes.find(c => c.name === input.dataset.class)?.target ?? 0;
    const res = await act('meta_classe', { cls: input.dataset.class, target: input.value });
    if (res.error && !res.saveFailed) { input.value = previous; setMsg('classMsg', res.error, 'error'); }
}

// Move um ativo (ou título de renda fixa) para outra classe. A meta fixada do ativo é mantida.
async function submitMove(ev) {
    ev.preventDefault();
    const res = await act('mover_ativo', { key: $('moveAsset').value, cls: $('moveClass').value });
    setMsg('classMsg', res.error || res.message, res.error ? 'error' : 'ok');
}

// ---------------------------------------------------------------- Posições, histórico e proventos
//#endregion
