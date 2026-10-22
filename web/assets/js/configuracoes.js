// Aba Configurações: os parâmetros da venda parcial e a regra de venda de cada tipo de ativo.
// A validação e os textos vêm do Python (strategy.py, rotas /api/configuracoes); aqui só se
// preenche e envia o formulário.

const form = document.getElementById('strategyForm');
const msg = document.getElementById('strategyMsg');
let defaults = null;
let options = [];
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

// Um cartão por tipo de ativo, na cor da classe, com a regra escolhida ({group, groupLabel, key}).
function renderKinds(kinds) {
    const grid = document.getElementById('kindsGrid');
    grid.innerHTML = kinds.map(k => `
        <label class="kind-card" style="--g:${GROUP_COLORS[k.group] || '#5aa2ff'}">
            <span class="kind-head"><span class="kind-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${GROUP_ICONS[k.group] || ''}</svg></span>
                <span><strong>${escapeHTML(k.groupLabel)}</strong><span class="small muted">${escapeHTML(k.hint || '')}</span></span></span>
            <select class="select strategy-kind" name="kind_${k.group}" aria-label="Regra de venda: ${escapeHTML(k.groupLabel)}">
                ${options.map(o => `<option value="${o.key}"${o.key === k.key ? ' selected' : ''}>${escapeHTML(o.label)}</option>`).join('')}
            </select>
        </label>`).join('');
    revealItems(grid, '.kind-card');
    markDiagrams();
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
    fill(data.strategy);
    const current = document.getElementById('currentText');
    current.innerHTML = data.strategy.kinds
        .map(k => `<li style="--g:${GROUP_COLORS[k.group] || '#5aa2ff'}"><strong>${escapeHTML(k.groupLabel)}</strong><span>${escapeHTML(k.text)}</span></li>`).join('');
    current._itemsSeen = false;
    revealItems(current, ':scope > li');
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

observeReveal();
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
