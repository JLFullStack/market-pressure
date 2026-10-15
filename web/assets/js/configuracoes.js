// Aba Configurações: escolha da estratégia de venda e dos parâmetros da venda parcial.
// A validação e os textos vêm do Python (strategy.py, rotas /api/configuracoes); aqui só se
// preenche e envia o formulário.

const form = document.getElementById('strategyForm');
const msg = document.getElementById('strategyMsg');
let defaults = null;

function showMsg(ok, text) {
    msg.className = `form-msg ${ok ? 'ok' : 'error'}`;
    msg.textContent = text;
}

// Preenche o formulário com uma estratégia ({key, targetPct, fractionPct}).
function fill(s) {
    form.elements.strategy.value = s.key;
    form.elements.target.value = fmtNum(s.targetPct, Number.isInteger(s.targetPct) ? 0 : 1);
    form.elements.fraction.value = fmtNum(s.fractionPct, Number.isInteger(s.fractionPct) ? 0 : 1);
}

function show(data) {
    defaults = data.default;
    fill(data.strategy);
    document.getElementById('currentText').textContent = `${data.strategy.label} — ${data.strategy.text}`;
    document.getElementById('reversalText').textContent = `Reversão de tendência: ${data.strategy.reversal}.`;
}

// Mexer nos números da venda parcial já escolhe essa estratégia.
['target', 'fraction'].forEach(name => form.elements[name].addEventListener('input', () => {
    form.elements.strategy.value = 'parcial';
}));

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
        const data = await apiPost('/configuracoes', {
            strategy: form.elements.strategy.value,
            target: form.elements.target.value,
            fraction: form.elements.fraction.value,
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
