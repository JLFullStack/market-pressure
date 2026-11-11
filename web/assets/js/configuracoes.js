// Aba Configurações: os parâmetros da venda parcial e a regra de venda de cada tipo de ativo.
// A validação e os textos vêm do Python (strategy.py, rotas /api/configuracoes); aqui só se
// preenche e envia o formulário.

//#region Estratégia de venda
const form = document.getElementById('strategyForm');
const msg = document.getElementById('strategyMsg');
let defaults = null;
let options = [];
let saved = {};  // regra salva de cada tipo: {group: {key, text}} (o "Em vigor" de cada cartão)
// Cor de cada tipo (a mesma da classe na Carteira) e ícone.
const GROUP_COLORS = { acoes: '#4f8dff', fundos: '#3ddc97', etfs: '#ffc65c', internacionais: '#a78bfa' };
const GROUP_ICONS = {
    acoes: '<path d="M7 3v18M17 3v18"/><rect x="5" y="7" width="4" height="8" rx="1"/><rect x="15" y="9" width="4" height="7" rx="1"/>',
    fundos: '<path d="M3 11l9-7 9 7M5 10v11h14V10M9 21v-5h6v5"/>',
    etfs: '<path d="M12 3l9 5-9 5-9-5zM3 12.5l9 5 9-5M3 16.5l9 5 9-5"/>',
    internacionais: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
};

function showMsg(ok, text) {
    msg.className = `form-msg ${ok ? 'ok' : 'error'}`;
    msg.textContent = text;
}

// Um cartão por tipo de ativo, na cor da classe, com a regra escolhida ({group, groupLabel, key}) e,
// embaixo, a regra em vigor (a salva, com o texto do Python); "não salvo" quando a escolha difere dela.
function renderKinds(kinds) {
    const grid = document.getElementById('kindsGrid');
    grid.innerHTML = kinds.map(k => `
        <label class="kind-card" style="--g:${GROUP_COLORS[k.group] || '#5aa2ff'}">
            <span class="kind-head"><span class="kind-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${GROUP_ICONS[k.group] || ''}</svg></span>
                <span><strong>${escapeHTML(k.groupLabel)}</strong><span class="small muted">${escapeHTML(k.hint || '')}</span></span></span>
            <select class="select strategy-kind" name="kind_${k.group}" aria-label="Regra de venda: ${escapeHTML(k.groupLabel)}">
                ${options.map(o => `<option value="${o.key}"${o.key === k.key ? ' selected' : ''}>${escapeHTML(o.label)}</option>`).join('')}
            </select>
            <span class="kind-now"><span class="kind-now-head"><b>Em vigor</b><span class="badge badge-fill kind-unsaved">não salvo</span></span>
                <span class="kind-now-text">${escapeHTML(saved[k.group]?.text || '—')}</span></span>
        </label>`).join('');
    revealItems(grid, '.kind-card');
    markUnsaved();
    markDiagrams();
}

// Cartões cuja escolha ainda não foi salva (a regra em vigor continua a anterior).
function markUnsaved() {
    form.querySelectorAll('select.strategy-kind').forEach(sel => {
        const group = sel.name.slice(5);
        sel.closest('.kind-card').classList.toggle('unsaved', !!saved[group] && saved[group].key !== sel.value);
    });
}

// Destaca as ilustrações das regras escolhidas em algum tipo e mostra o alvo digitado nelas.
function markDiagrams() {
    const used = new Set([...form.querySelectorAll('select.strategy-kind')].map(sel => sel.value));
    document.querySelectorAll('.rule-diagram').forEach(fig => fig.classList.toggle('used', used.has(fig.dataset.rule)));
    const target = form.elements.target.value.trim();
    document.querySelectorAll('.target-label').forEach(el => { el.textContent = target ? `+${target}%` : ''; });
}
form.addEventListener('change', (ev) => {
    if (ev.target.matches('select.strategy-kind')) {
        const card = ev.target.closest('.kind-card');
        card.classList.remove('changed'); void card.offsetWidth; card.classList.add('changed'); // pulso de confirmação
        markUnsaved();
    }
    markDiagrams();
});
form.addEventListener('input', markDiagrams);

// Preenche o formulário com as configurações ({targetPct, fractionPct, kinds}).
function fill(s) {
    form.elements.target.value = fmtNum(s.targetPct, Number.isInteger(s.targetPct) ? 0 : 1);
    form.elements.fraction.value = fmtNum(s.fractionPct, Number.isInteger(s.fractionPct) ? 0 : 1);
    renderKinds(s.kinds);
}

function show(data) {
    defaults = data.default;
    options = data.options;
    saved = Object.fromEntries(data.strategy.kinds.map(k => [k.group, { key: k.key, text: k.text }]));
    fill(data.strategy);
    document.getElementById('reversalText').textContent = `Reversão de tendência: ${data.strategy.reversal}.`;
}

document.getElementById('defaultBtn').addEventListener('click', () => {
    if (defaults) fill(defaults);
    showMsg(true, 'Valores padrão preenchidos; clique em "Salvar estratégia" para aplicar.');
});

form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    const busy = busyStart('Salvando estratégia...');
    try {
        const kinds = {};
        form.querySelectorAll('select.strategy-kind').forEach(sel => { kinds[sel.name.slice(5)] = sel.value; });
        const data = await apiPost('/configuracoes', {
            target: form.elements.target.value,
            fraction: form.elements.fraction.value,
            kinds,
        });
        show(data);
        showMsg(true, data.message);
    } catch (e) {
        showMsg(false, e.message);
    } finally {
        busy();
        button.disabled = false;
    }
});
//#endregion

//#region Aviso de imposto
// Aviso de imposto (tax_alert.py, rotas /api/configuracoes/avisos).
const alertForm = document.getElementById('alertForm');
const warnRange = document.getElementById('warnRange');
let taxLimit = 20000;
// Régua do aviso: de R$ 0 até um pouco além do limite; as faixas (sem aviso, com aviso, acima do
// limite) e as marcas acompanham o valor digitado ou arrastado.
const TAX_SCALE = 1.25;
function syncTaxScale() {
    const max = taxLimit * TAX_SCALE;
    const warn = Math.min(taxLimit, Math.max(0, Number(alertForm.elements.warnAt.value) || 0));
    const bar = alertForm.querySelector('.tax-bar');
    bar.style.setProperty('--warn', `${(warn / max * 100).toFixed(2)}%`);
    bar.style.setProperty('--limit', `${(taxLimit / max * 100).toFixed(2)}%`);
    alertForm.querySelector('.tax-warn-mark span').textContent = `aviso ${fmtBRL(warn).replace(',00', '')}`;
    alertForm.querySelector('.tax-panel').classList.toggle('off', !alertForm.elements.enabled.checked);
}
function showAlert(data) {
    taxLimit = data.limit;
    const [lo, hi] = data.range;
    for (const el of [alertForm.elements.warnAt, warnRange]) { el.min = lo; el.max = hi; }
    alertForm.elements.enabled.checked = data.alert.enabled;
    alertForm.elements.warnAt.value = warnRange.value = Math.round(data.alert.warnAt);
    alertForm.elements.warnAt.disabled = warnRange.disabled = !data.alert.enabled;
    document.getElementById('taxLimit').textContent = fmtBRL(data.limit).replace(',00', '');
    alertForm.querySelector('.tax-limit-mark span').textContent = `limite ${fmtBRL(data.limit).replace(',00', '')}`;
    document.getElementById('alertText').textContent = data.text;
    syncTaxScale();
}
alertForm.elements.enabled.addEventListener('change', () => {
    alertForm.elements.warnAt.disabled = warnRange.disabled = !alertForm.elements.enabled.checked;
    syncTaxScale();
});
alertForm.elements.warnAt.addEventListener('input', () => { warnRange.value = alertForm.elements.warnAt.value; syncTaxScale(); });
warnRange.addEventListener('input', () => { alertForm.elements.warnAt.value = warnRange.value; syncTaxScale(); });
alertForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const out = document.getElementById('alertMsg');
    const busy = busyStart('Salvando aviso...');
    try {
        const data = await apiPost('/configuracoes/avisos', { enabled: alertForm.elements.enabled.checked, warnAt: alertForm.elements.warnAt.value });
        showAlert(data);
        out.className = 'form-msg ok';
        out.textContent = data.message;
    } catch (e) {
        out.className = 'form-msg error';
        out.textContent = e.message;
    } finally {
        busy();
    }
});
//#endregion

//#region Feriados
// Feriados (holidays.py, rotas /api/configuracoes/feriados). As regras ficam no Python; aqui só a lista e o formulário.
const holidayForm = document.getElementById('holidayForm');
const holidayOut = document.getElementById('holidayMsg');
let holidayEditKey = null;
let holidayRows = {};
let holidayDefaultKind = 'municipal';
const HOLIDAY_BADGE = { nacional: 'badge-info', estadual: 'badge-ok', municipal: 'badge-fill', bancario: 'badge-low' };

function holidayTable(rows, empty) {
    if (!rows.length) return `<p class="small muted">${empty}</p>`;
    return `<div class="table-container"><table><thead><tr><th>Data</th><th>Feriado</th><th>Tipo</th><th></th></tr></thead><tbody>${rows.map(r => `<tr>
        <td>${escapeHTML(r.label)}</td><td>${escapeHTML(r.name)}</td>
        <td><span class="badge ${HOLIDAY_BADGE[r.kind] || 'badge-low'}">${escapeHTML(r.kindLabel)}</span></td>
        <td class="r holiday-actions"><button type="button" class="btn-secondary tax-copy" data-holiday-edit="${escapeHTML(r.key)}">Editar</button>
            <button type="button" class="btn-secondary tax-copy" data-holiday-remove="${escapeHTML(r.key)}">Remover</button></td>
    </tr>`).join('')}</tbody></table></div>`;
}

function setHolidayEdit(row) {
    holidayEditKey = row ? row.key : null;
    holidayForm.elements.date.value = row ? row.editDate : '';
    holidayForm.elements.name.value = row ? row.name : '';
    holidayForm.elements.annual.value = !row || row.annual ? '1' : '0';
    holidayForm.elements.kind.value = row ? row.kind : holidayDefaultKind;
    document.getElementById('holidaySubmit').textContent = row ? 'Salvar alteração' : 'Adicionar';
    document.getElementById('holidayCancel').hidden = !row;
}

function showHolidays(data) {
    holidayDefaultKind = data.defaultKind;
    if (!holidayForm.elements.kind.options.length) {
        holidayForm.elements.kind.innerHTML = data.kinds.map(k => `<option value="${k.key}">${escapeHTML(k.label)}</option>`).join('');
        holidayForm.elements.kind.value = holidayDefaultKind;
    }
    holidayRows = Object.fromEntries([...data.annual, ...data.dated].map(r => [r.key, r]));
    document.getElementById('holidayAnnual').innerHTML = holidayTable(data.annual, 'Nenhum feriado de data fixa na lista.');
    document.getElementById('holidayDated').innerHTML = holidayTable(data.dated, 'Nenhum feriado nesta parte da lista.');
    const year = document.getElementById('holidayYear');
    const chosen = year.value;
    year.innerHTML = data.years.map(y => `<option value="${y}"${String(y) === chosen ? ' selected' : ''}>${y}</option>`).join('');
}

async function postHoliday(body) {
    const busy = busyStart('Salvando feriados...');
    try {
        const data = await apiPost('/configuracoes/feriados', body);
        showHolidays(data);
        setHolidayEdit(null);
        holidayOut.className = 'form-msg ok';
        holidayOut.textContent = data.message;
    } catch (e) {
        holidayOut.className = 'form-msg error';
        holidayOut.textContent = e.message;
    } finally {
        busy();
    }
}

holidayForm.addEventListener('submit', (event) => {
    event.preventDefault();
    postHoliday({ op: holidayEditKey ? 'edit' : 'add', key: holidayEditKey, date: holidayForm.elements.date.value,
                  name: holidayForm.elements.name.value, annual: holidayForm.elements.annual.value === '1',
                  kind: holidayForm.elements.kind.value });
});
document.getElementById('holidayCancel').addEventListener('click', () => setHolidayEdit(null));
document.getElementById('holidayRestore').addEventListener('click', () => postHoliday({ op: 'restore' }));
document.getElementById('holidayMoving').addEventListener('click', () => postHoliday({ op: 'moving', year: document.getElementById('holidayYear').value }));
document.getElementById('feriados').addEventListener('click', (ev) => {
    const btn = ev.target.closest?.('button');
    if (btn?.dataset.holidayRemove) postHoliday({ op: 'remove', key: btn.dataset.holidayRemove });
    if (btn?.dataset.holidayEdit) {
        setHolidayEdit(holidayRows[btn.dataset.holidayEdit]);
        holidayForm.scrollIntoView({ block: 'center' });
        holidayForm.elements.name.focus();
    }
});
//#endregion

//#region Início
observeReveal();
apiGet('/configuracoes/feriados').then(showHolidays, (e) => {
    holidayOut.className = 'form-msg error';
    holidayOut.textContent = `Não foi possível carregar os feriados (${e.message}).`;
});
apiGet('/configuracoes/avisos').then(showAlert, (e) => {
    document.getElementById('alertMsg').className = 'form-msg error';
    document.getElementById('alertMsg').textContent = `Não foi possível carregar o aviso de imposto (${e.message}).`;
});
(async () => {
    const busy = busyStart('Carregando configurações...');
    try {
        show(await apiGet('/configuracoes'));
    } catch (e) {
        showMsg(false, `Não foi possível carregar as configurações (${e.message}).`);
    } finally {
        busy();
    }
})();
//#endregion
