// Aba Configurações: os parâmetros da venda parcial e a regra de venda de cada tipo de ativo.
// A validação e os textos vêm do Python (strategy.py, rotas /api/configuracoes); aqui só se
// preenche e envia o formulário.

const form = document.getElementById('strategyForm');
const msg = document.getElementById('strategyMsg');
let defaults = null;
let options = [];

function showMsg(ok, text) {
    msg.className = `form-msg ${ok ? 'ok' : 'error'}`;
    msg.textContent = text;
}

// Uma linha por tipo de ativo, com a regra escolhida ({group, groupLabel, key}).
function renderKinds(kinds) {
    document.getElementById('kindsBody').innerHTML = kinds.map(k => `
        <tr>
            <td><strong>${escapeHTML(k.groupLabel)}</strong><div class="small muted">${escapeHTML(k.hint || '')}</div></td>
            <td><select class="select strategy-kind" name="kind_${k.group}" aria-label="Regra de venda: ${escapeHTML(k.groupLabel)}">
                ${options.map(o => `<option value="${o.key}"${o.key === k.key ? ' selected' : ''}>${escapeHTML(o.label)}</option>`).join('')}
            </select></td>
        </tr>`).join('');
}

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
    document.getElementById('currentText').innerHTML = data.strategy.kinds
        .map(k => `<li><strong>${escapeHTML(k.groupLabel)}:</strong> ${escapeHTML(k.text)}</li>`).join('');
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
