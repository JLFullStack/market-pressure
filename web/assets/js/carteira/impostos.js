// Carteira → Impostos (tax_report.py): KPIs e ano, "O que merece atenção", o DARF com o passo a
// passo, apuração mês a mês, prejuízos anteriores e a declaração anual. Usa carteira.js.

//#region Auxiliares
// ---------------------------------------------------------------- Sub-aba Impostos (tax_report.py)
// Os valores, as frases e a situação de cada mês vêm prontos do servidor; aqui só o desenho.
const TAX_STATUS = {
    none: ['Sem DARF', 'badge-low'], pending: ['A pagar', 'badge-fill'],
    overdue: ['Atrasado', 'badge-hot'], paid: ['Pago', 'badge-ok']
};

const taxQty = (q) => fmtNum(q, Number.isInteger(q) ? 0 : 4);

const taxTable = (cls, head, rows) => `<div class="table-container"><table class="${cls}"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;

const taxCopyBtn = (text, title) => `<button type="button" class="btn-secondary tax-copy" data-copy="${escapeHTML(text)}" data-copy-title="${escapeHTML(title)}">Copiar</button>`;
//#endregion

//#region Carregar e desenhar
async function loadTaxes() {
    const portfolio = current();
    if (!portfolio) return;
    if (state.taxesFor !== portfolio.id) { state.taxesFor = portfolio.id; state.taxYear = null; state.taxDarfMonth = null; }
    const seq = ++state.taxesSeq;
    const busy = busyStart('Calculando impostos...');
    try {
        const q = state.taxYear ? `?ano=${state.taxYear}` : '';
        const data = await apiGet(`/carteira/${portfolio.id}/impostos${q}`);
        if (seq !== state.taxesSeq || portfolio.id !== state.currentId) return;
        state.taxes = data;
        state.taxYear = data.year;
        $('taxError').hidden = true;
        renderTaxes(data);
    } catch (e) {
        if (seq !== state.taxesSeq) return;
        $('taxError').textContent = `Não foi possível calcular os impostos (${e.message}).`;
        $('taxError').hidden = false;
    } finally { busy(); }
}

function renderTaxes(d) {
    $('taxYear').innerHTML = d.years.map(y => `<option value="${y}"${y === d.year ? ' selected' : ''}>${y}</option>`).join('');
    renderTaxKpis(d);
    $('taxAttentionPanel').hidden = !d.attention.length;
    $('taxAttention').innerHTML = d.attention.map(a => `<div class="tax-alert tax-${a.level}"><strong>${escapeHTML(a.title)}</strong>${a.text ? `<p>${escapeHTML(a.text)}</p>` : ''}</div>`).join('');
    renderTaxDarf(d);
    renderTaxMonths(d);
    $('taxOpeningCommon').value = d.opening.common ? fmtNum(d.opening.common, 2) : '';
    $('taxOpeningFunds').value = d.opening.funds ? fmtNum(d.opening.funds, 2) : '';
    renderTaxDeclaration(d.declaration, d.foreign);
    $('taxMethod').innerHTML = d.method.map(m => `<div><dt>${escapeHTML(m.title)}</dt><dd>${m.lines.map(l => `<p>${escapeHTML(l)}</p>`).join('')}</dd></div>`).join('');
}

function renderTaxKpis(d) {
    const k = d.kpis;
    const kpi = (label, value, note = '', cls = '') => `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value ${cls}">${value}</div><div class="kpi-note">${note}</div></div>`;
    const dueNote = k.pendingCount ? `${k.pendingCount} em aberto; o mais antigo vence em ${fmtDate(k.nextDue)}` : 'Nada em aberto';
    $('taxKpis').innerHTML = [
        kpi('DARF a pagar', fmtBRL(k.pending), dueNote, k.level === 'over' ? 'neg' : ''),
        kpi(`Imposto apurado em ${d.year}`, fmtBRL(k.yearTax), k.carry ? `${fmtBRL(k.carry)} aguardando somar R$ 10` : 'Antes do IRRF'),
        kpi(`Lucro isento em ${d.year}`, fmtBRL(k.yearExempt), 'Ações, vendas de até R$ 20 mil no mês'),
        kpi('Prejuízo a compensar', fmtBRL(k.lossCommon), 'Ações, ETFs e BDRs'),
        kpi('Prejuízo em fundos', fmtBRL(k.lossFunds), 'Só abate lucro de fundos imobiliários')
    ].join('');
}

function renderTaxDarf(d) {
    const box = $('taxDarf');
    const m = d.months.find(x => x.month === state.taxDarfMonth && x.darf) || d.nextDarf;
    const guide = (m && d.guide[m.month]) || d.guideGeneral;
    const steps = guide.map((s, i) => `<li class="tax-step">
            <span class="tax-step-n" aria-hidden="true">${i + 1}</span>
            <div class="tax-step-body">
                <strong>${escapeHTML(s.title)}</strong>
                <p>${escapeHTML(s.text)}</p>
                ${s.link ? `<a class="btn-secondary tax-link" href="${escapeHTML(d.sicalcUrl)}" target="_blank" rel="noopener noreferrer">Abrir o Sicalc da Receita</a>` : ''}
                ${s.fields ? `<div class="tax-step-fields">${s.fields.map(f => `<div class="tax-step-field"><span class="small muted">${escapeHTML(f.label)}</span><strong>${escapeHTML(f.value)}</strong>${f.copy ? taxCopyBtn(f.copy, f.label) : ''}</div>`).join('')}</div>` : ''}
                ${s.warn ? `<p class="tax-step-warn">${escapeHTML(s.warn)}</p>` : ''}
            </div>
        </li>`).join('');
    const tutorial = `<details class="tax-guide"${m ? ' open' : ''}><summary>Como emitir e pagar, passo a passo</summary><ol class="tax-stepper">${steps}</ol></details>`;
    if (!m) {
        $('taxDarfTitle').textContent = 'Emitir o DARF';
        box.innerHTML = `<p class="muted">Não há DARF em aberto. Quando houver, os dados dele aparecem aqui.</p>${tutorial}`;
        return;
    }
    $('taxDarfTitle').textContent = `DARF de ${m.label}`;
    const field = (label, value) => `<div class="tax-field"><span class="kpi-label">${label}</span><strong>${escapeHTML(value)}</strong></div>`;
    const late = m.status === 'overdue' ? field('Multa estimada até hoje', fmtBRL(m.lateFine)) : '';
    const action = m.status === 'paid'
        ? `<button type="button" class="btn-secondary" data-paid-month="${m.month}" data-paid="0">Desfazer pagamento</button>`
        : `<button type="button" class="btn" data-paid-month="${m.month}" data-paid="1">Marcar como pago</button>`;
    box.innerHTML = `<div class="tax-fields">
            ${field('Período de apuração', m.period)}
            ${field('Vencimento', fmtDate(m.dueDate))}
            ${field('Valor principal', fmtBRL(m.darf))}
            ${late}
        </div>
        ${tutorial}
        <div class="tax-actions">${action}</div>`;
}

function renderTaxMonths(d) {
    const body = $('taxMonths');
    if (!d.months.length) {
        body.innerHTML = `<tr><td colspan="11"><span class="sticky-label muted">Nenhuma venda na B3 em ${d.year}.</span></td></tr>`;
        return;
    }
    body.innerHTML = d.months.map(m => {
        const [label, badge] = TAX_STATUS[m.status];
        const taxable = m.taxableCommon + m.fundGain;
        const buttons = [`<button type="button" class="btn-secondary tax-copy" data-tax-toggle="${m.month}" aria-expanded="false">Ver conta</button>`];
        if (m.darf) buttons.push(`<button type="button" class="btn-secondary tax-copy" data-tax-darf="${m.month}">DARF</button>`);
        const sales = m.sales.map(s => `<tr><td>${fmtDate(s.date)}</td><td>${escapeHTML(s.ticker)}</td><td>${escapeHTML(s.categoryLabel)}</td><td class="r">${taxQty(s.quantity)}</td><td class="r">${fmtBRL(s.gross)}</td><td class="r">${fmtBRL(s.cost)}</td><td class="r ${s.gain < 0 ? 'neg' : ''}">${fmtBRL(s.gain)}</td></tr>`).join('');
        return `<tr>
            <td>${escapeHTML(m.label)}</td>
            <td class="r">${fmtBRL(m.stockSold)}</td>
            <td class="r">${fmtBRL(m.exemptGain)}</td>
            <td class="r ${taxable < 0 ? 'neg' : ''}">${fmtBRL(taxable)}</td>
            <td class="r">${fmtBRL(m.lossUsedCommon + m.lossUsedFunds)}</td>
            <td class="r">${fmtBRL(m.tax)}</td>
            <td class="r">${fmtBRL(m.creditUsed)}</td>
            <td class="r"><strong>${fmtBRL(m.darf)}</strong></td>
            <td>${m.darf ? fmtDate(m.dueDate) : '—'}</td>
            <td><span class="badge ${badge}">${label}</span>${m.paidDiff ? `<div class="small neg">Pago: ${fmtBRL(m.paid.amount)}</div>` : ''}</td>
            <td><div class="tax-row-actions">${buttons.join('')}</div></td>
        </tr>
        <tr id="taxDetail-${m.month}" hidden><td colspan="11"><div class="sticky-label tax-detail">
            <ol class="tax-steps">${m.steps.map(s => `<li>${escapeHTML(s)}</li>`).join('')}</ol>
            ${taxTable('', '<th>Data</th><th>Ativo</th><th>Tipo</th><th class="r">Qtd</th><th class="r">Venda</th><th class="r">Custo</th><th class="r">Resultado</th>', sales)}
        </div></td></tr>`;
    }).join('');
}

function renderTaxDeclaration(decl, foreign) {
    $('taxDeclarationTitle').textContent = `Declaração de ${decl.deliveredIn} (ano-calendário ${decl.year})`;
    const parts = [`<p class="small muted">Use junto com os informes de rendimentos das corretoras e bancos, que têm os valores oficiais e os CNPJs. Os códigos marcados com * variam entre as fontes: confira na lista do programa da Receita.</p>`];
    const holdings = decl.holdings.map(r => `<tr>
        <td>${escapeHTML(r.ticker)}<div class="small muted">${escapeHTML(r.categoryLabel)}</div></td>
        <td>${escapeHTML(r.code)}${r.verify ? ' *' : ''}</td>
        <td class="r">${taxQty(r.quantityAfter)}</td>
        <td class="r">${fmtBRL(r.costBefore)}</td>
        <td class="r">${fmtBRL(r.costAfter)}</td>
        <td class="tax-text">${escapeHTML(r.text)}</td>
        <td>${taxCopyBtn(r.text, `Descrição de ${r.ticker}`)}</td>
    </tr>`).join('');
    parts.push(`<h3>Bens e Direitos</h3>`);
    parts.push(holdings ? taxTable('sticky-first', `<th>Ativo</th><th>Grupo / código</th><th class="r">Qtd em 31/12</th><th class="r">Custo em 31/12/${decl.year - 1}</th><th class="r">Custo em 31/12/${decl.year}</th><th>Descrição</th><th></th>`, holdings)
        : `<p class="muted">Nenhuma posição em 31/12/${decl.year - 1} ou 31/12/${decl.year}.</p>`);
    parts.push(`<ul class="notes"><li>Declare pelo <strong>custo</strong>, não pelo valor de mercado.</li><li>Troque o texto entre colchetes pelos dados da sua corretora.</li></ul>`);
    if (decl.fixedIncome.length) {
        parts.push(`<h3>Renda fixa</h3>`);
        parts.push(taxTable('', `<th>Título</th><th class="r">Aplicado em 31/12/${decl.year - 1}</th><th class="r">Aplicado em 31/12/${decl.year}</th>`,
            decl.fixedIncome.map(f => `<tr><td>${escapeHTML(f.name)}</td><td class="r">${fmtBRL(f.before)}</td><td class="r">${fmtBRL(f.after)}</td></tr>`).join('')));
        parts.push(`<ul class="notes"><li>Use os valores e os códigos do <strong>informe do banco</strong>: ele traz o saldo e os rendimentos oficiais.</li></ul>`);
    }
    const months = state.taxes.months;
    parts.push(`<h3>Renda Variável (mês a mês)</h3>`);
    parts.push(months.length ? taxTable('sticky-first', '<th>Mês</th><th class="r">Operações comuns</th><th class="r">Saldo de prejuízo</th><th class="r">Fundos imobiliários</th><th class="r">Saldo em fundos</th><th class="r">IRRF</th><th class="r">DARF pago</th>',
        months.map(m => `<tr><td>${escapeHTML(m.label)}</td><td class="r">${fmtBRL(m.taxableCommon)}</td><td class="r">${fmtBRL(m.lossCommon)}</td><td class="r">${fmtBRL(m.fundGain)}</td><td class="r">${fmtBRL(m.lossFunds)}</td><td class="r">${fmtBRL(m.sourceTax)}</td><td class="r">${m.paid ? fmtBRL(m.paid.amount) : '—'}</td></tr>`).join(''))
        : `<p class="muted">Nenhuma venda na B3 em ${decl.year}.</p>`);
    parts.push(`<ul class="notes">
        <li><strong>Lucro isento de ações:</strong> ${fmtBRL(decl.exemptGain)}. ${escapeHTML(decl.exemptWhere)}</li>
        ${decl.sourceLeft ? `<li><strong>IRRF sem uso no ano:</strong> ${fmtBRL(decl.sourceLeft)}. Confira como informar no programa.</li>` : ''}
        <li>Mesmo sem lucro, é preciso informar os <strong>prejuízos</strong> para mantê-los no ano seguinte.</li>
    </ul>`);
    parts.push(`<h3>Proventos</h3>`);
    if (!decl.incomeLoaded) parts.push(`<p class="muted">As cotações não carregaram; tente atualizar a página.</p>`);
    else if (!decl.income.length) parts.push(`<p class="muted">Nenhum provento estimado em ${decl.year}.</p>`);
    else {
        parts.push(taxTable('sticky-first', '<th>Ativo</th><th>Tipo</th><th class="r">Total bruto estimado</th><th>Onde declarar</th>',
            decl.income.map(r => `<tr><td>${escapeHTML(r.ticker)}</td><td>${escapeHTML(r.categoryLabel)}</td><td class="r">${fmtBRL(r.total)}</td><td class="tax-text">${escapeHTML(r.where)}</td></tr>`).join('')));
        parts.push(`<ul class="notes"><li>Estimativa pela data-ex, sem separar dividendo de JCP. Declare pelos <strong>valores do informe</strong>, que seguem a data de pagamento.</li></ul>`);
    }
    if (foreign) {
        parts.push(`<h3>Exterior</h3>`);
        parts.push(`<ul class="notes">
            <li><strong>Resultado das vendas:</strong> ${fmtBRL(foreign.gain)}${foreign.lossUsed ? `, com ${fmtBRL(foreign.lossUsed)} de prejuízo anterior abatido` : ''}.</li>
            <li><strong>Imposto estimado sobre as vendas:</strong> ${fmtBRL(foreign.tax)}, pago na declaração.</li>
            <li><strong>Dividendos brutos:</strong> ${fmtBRL(foreign.dividends)}. O imposto retido lá pode virar crédito; confirme com um contador.</li>
            ${foreign.lossOut ? `<li><strong>Prejuízo para os próximos anos:</strong> ${fmtBRL(foreign.lossOut)}.</li>` : ''}
            <li>Informe na ficha de Aplicações Financeiras e Lucros e Dividendos no Exterior.</li>
        </ul>`);
    }
    $('taxDeclaration').innerHTML = parts.join('');
}
//#endregion

//#region Ações e eventos (DARF pago, prejuízos, copiar)
async function postTaxes(path, body, msgEl) {
    const portfolio = current();
    if (!portfolio) return;
    try {
        const res = await apiPost(`/carteira/${portfolio.id}/impostos/${path}`, body);
        renderTaxAlert($('darfAlert'), res.darfAlert);
        await loadTaxes();
        msgEl.className = 'form-msg ok';
        msgEl.textContent = res.message;
    } catch (e) {
        msgEl.className = 'form-msg error';
        msgEl.textContent = e.message;
    }
}

async function copyTaxText(btn) {
    const text = btn.dataset.copy;
    try {
        await navigator.clipboard.writeText(text);
        const label = btn.textContent;
        btn.textContent = 'Copiado ✓';
        setTimeout(() => { btn.textContent = label; }, 1500);
    } catch (e) {
        showCopyDialog(btn.dataset.copyTitle, text);
    }
}

// Ano, prejuízos anteriores e os botões da sub-aba (DARF pago, ver conta, copiar).
function setupImpostos() {
    $('taxYear').addEventListener('change', (ev) => { state.taxYear = ev.target.value; state.taxDarfMonth = null; loadTaxes(); });
    $('taxOpeningForm').addEventListener('submit', (ev) => {
        ev.preventDefault();
        postTaxes('prejuizos', { common: $('taxOpeningCommon').value, funds: $('taxOpeningFunds').value }, $('taxOpeningMsg'));
    });
    $('panel-impostos').addEventListener('click', (ev) => {
        const el = ev.target.closest?.('button');
        if (!el) return;
        if (el.dataset.copy !== undefined) return void copyTaxText(el);
        if (el.dataset.paidMonth) return void postTaxes('darf', { month: el.dataset.paidMonth, paid: el.dataset.paid === '1' }, $('taxMsg'));
        if (el.dataset.taxToggle) {
            const row = $(`taxDetail-${el.dataset.taxToggle}`);
            row.hidden = !row.hidden;
            el.setAttribute('aria-expanded', String(!row.hidden));
            el.textContent = row.hidden ? 'Ver conta' : 'Fechar';
        } else if (el.dataset.taxDarf) {
            state.taxDarfMonth = el.dataset.taxDarf;
            renderTaxDarf(state.taxes);
            $('taxDarfTitle').scrollIntoView({ block: 'center' });
        }
    });
}
//#endregion
