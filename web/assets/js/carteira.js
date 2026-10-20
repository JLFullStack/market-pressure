// Aba Carteira. Depende de core.js (apiGet, apiPost e formatadores), carregado antes deste script.
//
// Todos os cálculos e todas as alterações ficam no servidor (portfolio.py e portfolio_store.py).
// A página só pede e exibe:
//   GET  /api/portfolios                 -> carteiras (seletor)
//   GET  /api/carteira/{id}/estrutura    -> classes, histórico de lançamentos, seletores
//   GET  /api/carteira/{id}/analise      -> posições, KPIs, gráfico, alocação, proventos, metas
//   GET  /api/carteira/{id}/proventos    -> proventos anunciados a receber
//   POST /api/carteira/{id}/aporte       -> plano da calculadora de aporte
//   GET  /api/carteira/{id}/ativo, /api/cotacao  -> ticker novo, classe sugerida, preço e câmbio do dia
//   POST /api/lote/totais                -> totais da lista de compras sugeridas
//   GET  /api/carteira/{id}/metas        -> metas com a projeção de cada uma
//   POST /api/carteira/{id}/metas/simular -> projeção de uma meta ainda não salva
//   POST /api/carteira/acao              -> qualquer alteração (validada e gravada no servidor)

// Mercados de um ativo (rótulo e moeda exibidos). Fora da B3 o preço é em US$.
const MARKETS = {
    b3: { label: 'B3', currency: 'R$' },
    us: { label: 'EUA', currency: 'US$' },
    crypto: { label: 'Cripto', currency: 'US$' }
};
const SELECTED_KEY = 'marketpressure_carteira_atual'; // só a preferência; os dados ficam no servidor
const APORTE_KEY = 'marketpressure_aporte_'; // + id da carteira: { amount, mode, selected }
const TAB_KEY = 'marketpressure_carteira_aba'; // última sub-aba, usada quando a URL não tem hash
const COLLAPSE_KEY = 'marketpressure_aporte_recolhido'; // "1" = calculadora de aporte recolhida
// Sub-abas da página, na ordem da barra. Cada uma é #tab-{nome} (botão) e #panel-{nome} (seção);
// a ativa fica no hash da URL (#visao, #aportes...), então recarregar e voltar/avançar a mantêm.
const TABS = ['visao', 'aportes', 'proventos', 'metas', 'configuracoes'];
// Hashes (e valores salvos) das sub-abas antigas, que foram juntadas em "Aportes e lançamentos".
const TAB_ALIASES = { aportar: 'aportes', lancamentos: 'aportes' };
const HISTORY_LIMIT = 10; // lançamentos mostrados no histórico antes de "Ver todos"
// Cores fixas por série, nesta ordem (validadas sobre o fundo #161b22 do painel).
const SERIES = [
    { key: 'portfolio', label: 'Carteira', color: '#3987e5' },
    { key: 'cdi', label: 'CDI', color: '#d95926' },
    { key: 'bova', label: 'BOVA11', color: '#199e70' }
];
const PERIODS = ['6M', '1A', '2A', 'Tudo']; // os mesmos do servidor (portfolio.PERIODS)
const PATRIMONY_PERIODS = ['30D', '1A', '2A', '5A', '10A', 'Tudo']; // os mesmos do servidor (portfolio.PATRIMONY_PERIODS)
// Barras da evolução do patrimônio: pedaços empilhados (portfolio.patrimony_bars), de baixo para cima.
const PATRIMONY_SERIES = [
    { key: 'base', label: 'Valor aplicado', color: '#3987e5' },
    { key: 'up', label: 'Ganho de capital', color: '#199e70' },
    { key: 'down', label: 'Perda de capital', color: '#d95926' }
];
// Modo "Por classe": uma cor fixa por classe (portfolio.CLASSES); outras usam EXTRA_COLORS.
const CLASS_COLORS = { 'Ações': '#3987e5', 'Fundos imobiliários': '#199e70', 'ETFs Brasil': '#e3b341', 'Internacionais': '#a371f7', 'Renda fixa': '#8b949e' };
const EXTRA_COLORS = ['#db61a2', '#d95926', '#56d4dd'];
const PATRIMONY_MODES = { valor: 'Aplicado e ganho', classes: 'Por classe' };
const APORTE_MODES = { todos: 'Todos os ativos da carteira', escolher: 'Escolher ativos', pullback: 'Pelas regras do pullback' };
// Indicação da posição (COMPRAR, VENDER, MANTER; portfolio.position_signal: statusKey) → badge.
const STATUS_BADGE = { sell: 'badge-hot', partial: 'badge-hot', buy: 'badge-ok', attention: 'badge-fill', hold: 'badge-info' };
const PLAN_BADGE = { sell: 'badge-hot', fixed: 'badge-low', setup: 'badge-ok', wait: 'badge-fill' };
const AMOUNT_DELAY_MS = 250; // espera após a digitação do valor do aporte antes de recalcular
// Rótulo do campo de valor da meta, conforme o tipo.
const GOAL_TARGET_LABEL = { renda: 'Renda mensal desejada (R$)', patrimonio: 'Patrimônio desejado (R$)' };
const GOAL_COLORS = { value: '#3987e5', contributed: '#8b949e', goal: '#e3b341' };

// ================================================================ Estado

const state = {
    doc: { version: 2, portfolios: [] },
    currentId: null,
    structure: null,     // resposta de /estrutura da carteira atual
    analysis: null,      // resposta de /analise
    plan: null,          // último plano de aporte exibido
    period: '1A',
    patrimonyPeriod: '1A',
    patrimonyClass: '',  // classe do gráfico de patrimônio ('' = todas)
    patrimonyMode: 'valor', // 'valor' (aplicado e ganho) ou 'classes' (por classe)
    patrimonyChart: null,
    run: 0,
    aporteSeq: 0,        // descarta respostas de aporte fora de ordem
    txSeq: 0,            // idem para a consulta de ticker/preço do formulário
    batchSeq: 0,         // idem para os totais da lista de compras
    amountTimer: null,
    classTouched: false, // a classe sugerida só substitui a escolha enquanto o usuário não mexe no seletor
    aporteFor: null,     // id da carteira cujas preferências de aporte estão nos campos
    tab: 'visao',        // sub-aba ativa
    historyAll: false,   // histórico completo (true) ou só os HISTORY_LIMIT mais recentes
    batch: null,         // lista de compras sugeridas em conferência: { date, amount, rows }
    chart: null,
    goals: null,         // resposta de /metas (null = ainda não carregada para a carteira atual)
    goalsSeq: 0,         // descarta respostas de metas fora de ordem
    goalCharts: [],      // gráficos dos cartões de meta (destruídos a cada redesenho)
    editingGoal: null    // id da meta em edição no formulário (null = nova)
};

const $ = (id) => document.getElementById(id);
const current = () => state.doc.portfolios.find(p => p.id === state.currentId) || null;
const signClass = (v) => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
// Quantidade: inteira sem casas; fracionária (cripto, EUA) com até 8 casas.
const fmtQty = (q) => Number.isInteger(q) ? fmtNum(q, 0) : q.toLocaleString('pt-BR', { maximumFractionDigits: 8 });
const fmtMoney = (v, currency = 'R$') => currency === 'R$' ? fmtBRL(v)
    : `${currency} ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;

// Envia uma alteração ao servidor, que valida e grava. Antes de redesenhar, before(res) pode
// ajustar o estado da página (ex.: a carteira selecionada). Retorna { error } ou a resposta.
async function act(action, params = {}, before = null) {
    let res;
    const busy = busyStart('Salvando...');
    try {
        res = await apiPost('/carteira/acao', { action, id: state.currentId, ...params });
    } catch (e) {
        busy();
        if (e.status === 400) return { error: e.message, data: e.data };
        showError(`Não foi possível salvar a carteira (${e.message}). Nada foi alterado.`);
        return { error: `Falha ao salvar: ${e.message}`, saveFailed: true, data: {} };
    }
    state.doc = res.doc;
    before?.(res);
    hideError();
    try { await refresh(); } finally { busy(); } // o indicador segue aberto até redesenhar
    return res;
}

// Preferências do aporte por carteira (só neste navegador): valor, modo e ativos escolhidos.
function readAportePrefs(id) {
    try { return { amount: '', mode: 'todos', selected: [], ...JSON.parse(localStorage.getItem(APORTE_KEY + id) || '{}') }; }
    catch (e) { return { amount: '', mode: 'todos', selected: [] }; }
}

function writeAportePrefs(id, prefs) {
    try { localStorage.setItem(APORTE_KEY + id, JSON.stringify(prefs)); } catch (e) { /* preferência não salva */ }
}

// ================================================================ Renderização

document.addEventListener('DOMContentLoaded', init);

async function init() {
    $('txMarket').innerHTML = Object.entries(MARKETS).map(([k, m]) => `<option value="${k}">${m.label}</option>`).join('');
    $('aporteMode').innerHTML = Object.entries(APORTE_MODES).map(([k, label]) => `<option value="${k}">${label}</option>`).join('');
    try { $('aporteBase').value = localStorage.getItem(MODE_KEY) || 'fechamento'; } catch (e) { /* sem preferência salva */ }
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

    $('portfolioSelect').addEventListener('change', (ev) => selectPortfolio(ev.target.value));
    $('newPortfolioBtn').addEventListener('click', createPortfolio);
    $('emptyCreateBtn').addEventListener('click', createPortfolio);
    $('renamePortfolioBtn').addEventListener('click', renamePortfolio);
    $('deletePortfolioBtn').addEventListener('click', deletePortfolio);
    $('refreshBtn').addEventListener('click', () => refresh());
    $('analyzePortfolioBtn').addEventListener('click', analyzePortfolio);
    $('periodButtons').addEventListener('click', (ev) => {
        const period = ev.target.dataset?.period;
        if (!period) return;
        state.period = period;
        for (const b of $('periodButtons').querySelectorAll('button')) b.setAttribute('aria-pressed', b.dataset.period === period);
        renderChart();
    });
    $('txTicker').addEventListener('change', onTxInputChange);
    $('txDate').addEventListener('change', onTxInputChange);
    $('txMarket').addEventListener('change', onTxInputChange);
    $('txClass').addEventListener('change', () => { state.classTouched = true; });
    $('txForm').addEventListener('submit', submitTransaction);
    $('fiNewForm').addEventListener('submit', submitFixedIncomeItem);
    $('fiEntryForm').addEventListener('submit', submitFixedIncomeEntry);
    $('allocationBody').addEventListener('change', onAllocationChange);
    $('moveForm').addEventListener('submit', submitMove);
    $('historyBody').addEventListener('click', onHistoryClick);
    $('aporteAmount').addEventListener('input', onAporteInput);
    $('aporteMode').addEventListener('change', onAporteInput);
    $('aporteBase').addEventListener('change', () => {
        try { localStorage.setItem(MODE_KEY, $('aporteBase').value); } catch (e) { /* preferência não salva */ }
        closeBatch();
        renderAporte();
    });
    $('aporteAssetsBody').addEventListener('change', onAporteTableChange);
    $('assetTargetsBody').addEventListener('change', onAporteTableChange);
    $('subtabs').addEventListener('click', (ev) => { const tab = ev.target.closest?.('[data-tab]'); if (tab) goTab(tab.dataset.tab); });
    $('subtabs').addEventListener('keydown', onTabKey);
    window.addEventListener('hashchange', onHashChange);
    $('firstOpBtn').addEventListener('click', () => { goTab('aportes'); $('txTicker').focus(); });
    $('aporteToggle').addEventListener('click', () => setAporteCollapsed($('aporteToggle').getAttribute('aria-expanded') === 'true'));
    $('historyToggle').addEventListener('click', () => { state.historyAll = !state.historyAll; renderHistory(state.structure); });
    $('aporteRegisterBtn').addEventListener('click', openBatch);
    $('batchDate').addEventListener('change', onBatchDateChange);
    $('batchBody').addEventListener('input', onBatchInput);
    $('batchBody').addEventListener('change', onBatchInput);
    $('batchConfirmBtn').addEventListener('click', confirmBatch);
    $('batchCancelBtn').addEventListener('click', () => { closeBatch(); $('aporteRegisterBtn').focus(); });
    $('goalForm').addEventListener('submit', submitGoal);
    $('goalPreviewBtn').addEventListener('click', previewGoal);
    $('goalCancelBtn').addEventListener('click', resetGoalForm);
    $('goalKind').addEventListener('change', updateGoalForm);
    $('goalUsePortfolio').addEventListener('change', updateGoalForm);
    $('goalsList').addEventListener('click', onGoalsClick);
    updateGoalForm();
    let collapsed = false;
    try { collapsed = localStorage.getItem(COLLAPSE_KEY) === '1'; } catch (e) { /* sem preferência salva */ }
    setAporteCollapsed(collapsed);
    state.tab = tabFromHash() || savedTab() || 'visao';

    const busy = busyStart('Carregando carteiras...');
    try {
        state.doc = await apiGet('/portfolios');
    } catch (e) {
        showError(`Não foi possível ler as carteiras (${e.message}).`);
    }
    let saved = null;
    try { saved = localStorage.getItem(SELECTED_KEY); } catch (e) { /* sem preferência salva */ }
    try {
        await selectPortfolio(state.doc.portfolios.some(p => p.id === saved) ? saved : state.doc.portfolios[0]?.id || null);
    } finally {
        busy();
    }
    // #lancamentos (endereço antigo) rola até "Nova operação" depois que a página foi montada.
    if (location.hash === '#lancamentos' && state.tab === 'aportes') $('operationsPanel').scrollIntoView();
}

function selectPortfolio(id) {
    state.currentId = id;
    state.batch = null;
    resetGoalForm();
    state.historyAll = false;
    try { if (id) localStorage.setItem(SELECTED_KEY, id); } catch (e) { /* preferência não salva */ }
    return refresh();
}

function renderSelector() {
    const select = $('portfolioSelect');
    select.innerHTML = state.doc.portfolios.map(p =>
        `<option value="${p.id}" ${p.id === state.currentId ? 'selected' : ''}>${escapeHTML(p.name)}</option>`).join('');
    const has = state.doc.portfolios.length > 0;
    select.disabled = !has;
    $('renamePortfolioBtn').disabled = $('deletePortfolioBtn').disabled = !has;
}

// Seletores de classe dos formulários (operação, renda fixa, mover ativo).
function renderClassSelects(st) {
    const classes = st?.classes || [];
    const options = (selected) => classes.map(c => `<option value="${escapeHTML(c.name)}" ${c.name === selected ? 'selected' : ''}>${escapeHTML(c.name)}</option>`).join('');
    const keep = (id, fallback) => { const v = $(id).value; $(id).innerHTML = options(classes.some(c => c.name === v) ? v : fallback); };
    keep('txClass', st?.defaultClass);
    keep('fiClass', st?.fixedIncomeClass);
    keep('moveClass', classes[0]?.name);
    const assets = st?.moveOptions || [];
    const v = $('moveAsset').value;
    $('moveAsset').innerHTML = assets.length ? assets.map(o => `<option value="${escapeHTML(o.key)}">${escapeHTML(o.label)}</option>`).join('')
        : '<option value="">Nenhum ativo</option>';
    if (assets.some(o => o.key === v)) $('moveAsset').value = v;
}

async function createPortfolio() {
    const name = (prompt('Nome da nova carteira:', state.doc.portfolios.length ? '' : 'Principal') || '').trim();
    if (!name) return;
    const res = await act('criar_carteira', { name }, (r) => {
        state.currentId = r.id;
        try { localStorage.setItem(SELECTED_KEY, r.id); } catch (e) { /* preferência não salva */ }
    });
    if (res.error && !res.saveFailed) alert(res.error);
}

async function renamePortfolio() {
    const p = current();
    if (!p) return;
    const name = (prompt('Novo nome da carteira:', p.name) || '').trim();
    if (!name || name === p.name) return;
    const res = await act('renomear_carteira', { name });
    if (res.error && !res.saveFailed) alert(res.error);
}

async function deletePortfolio() {
    const p = current();
    if (!p || !confirm(`Excluir a carteira "${p.name}" e todos os seus lançamentos? Esta ação não pode ser desfeita.`)) return;
    const res = await act('excluir_carteira', {}, (r) => { state.currentId = r.id; });
    if (res.error && !res.saveFailed) alert(res.error);
}

// Busca a estrutura e a análise da carteira atual e redesenha tudo, com o indicador de
// carregamento até a análise chegar (os proventos a receber têm o próprio aviso no card).
async function refresh() {
    const busy = busyStart('Carregando carteira...');
    try { await loadPortfolio(busy); } finally { busy(); }
}

async function loadPortfolio(busy) {
    renderSelector();
    const portfolio = current();
    const run = ++state.run;
    let st = null;
    if (portfolio) {
        try {
            st = await apiGet(`/carteira/${portfolio.id}/estrutura`);
        } catch (e) {
            showError(`Não foi possível ler a carteira (${e.message}).`);
        }
        if (run !== state.run) return; // uma atualização mais nova já está em andamento
    }
    state.structure = st;
    const hasEntries = !!st?.hasEntries;
    // Sem carteira: só o estado vazio (as sub-abas somem). Com carteira e sem lançamentos: as
    // seções que dependem de posições ([data-needs-entries]) dão lugar às chamadas [data-no-entries].
    $('emptyState').hidden = !!portfolio;
    for (const el of document.querySelectorAll('[data-needs-entries]')) el.hidden = !hasEntries;
    for (const el of document.querySelectorAll('[data-no-entries]')) el.hidden = hasEntries;
    if (portfolio) $('firstOpText').innerText = `A carteira "${portfolio.name}" ainda não tem lançamentos. Registre uma compra ou um título de renda fixa para ver o resumo, a rentabilidade e a alocação.`;
    if (st) {
        for (const id of ['txDate', 'fiDate']) { if (!$(id).value) $(id).value = st.today; $(id).max = st.today; }
    }
    state.goals = null; // as metas são pedidas de novo ao abrir a sub-aba (showTab)
    showTab(state.tab);
    renderFixedIncomeSelect(st);
    renderClassSelects(st);
    renderClassConfig(st);
    renderHistory(st);
    if (portfolio && state.aporteFor !== portfolio.id) {
        const prefs = readAportePrefs(portfolio.id);
        $('aporteAmount').value = prefs.amount;
        $('aporteMode').value = APORTE_MODES[prefs.mode] ? prefs.mode : 'todos';
        state.aporteFor = portfolio.id;
    }
    if (!hasEntries) {
        state.analysis = null;
        renderAssetTargets(null);
        $('lastUpdate').innerText = 'Posições, proventos e rentabilidade comparada ao CDI e ao BOVA11';
        return;
    }

    $('refreshBtn').disabled = true;
    $('lastUpdate').innerText = 'Carregando cotações...';
    busy.text('Carregando cotações e calculando a carteira...');
    // Proventos a receber em paralelo; o card é preenchido depois do resto da página.
    const receivables = apiGet(`/carteira/${portfolio.id}/proventos`).then(r => ({ ok: true, r }), e => ({ ok: false, e }));
    let a;
    try {
        a = await apiGet(`/carteira/${portfolio.id}/analise`);
    } catch (e) {
        if (run !== state.run) return;
        $('refreshBtn').disabled = false;
        $('lastUpdate').innerText = 'Cotações indisponíveis';
        showError(`Não foi possível analisar a carteira (${e.message}).`);
        return;
    }
    if (run !== state.run) return;
    $('refreshBtn').disabled = false;
    state.analysis = a;
    if (a.failed.length) showError(`Dados indisponíveis: ${a.failed.join('; ')}. Os valores desses itens usam o último preço informado.`);
    else hideError();

    $('lastUpdate').innerText = `Cotações de ${fmtDate(a.lastDay)}${a.live ? ' (pregão em andamento)' : ''} · atualizado em ${new Date(a.loadedAt).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`;
    renderKpis(a);
    renderAporte();
    renderPatrimony();
    renderChart();
    renderAllocation(a);
    renderPositions(a);
    renderAssetTargets(a);
    renderDividends(a);
    $('receivableTotals').innerText = '';
    $('receivablesBody').innerHTML = `<tr><td colspan="8" class="empty-row">Carregando proventos anunciados...</td></tr>`;
    busy();
    const result = await receivables;
    if (run !== state.run) return;
    renderReceivables(result);
}

// ---------------------------------------------------------------- Sub-abas

// Nome válido de sub-aba (aceita os antigos #aportar e #lancamentos) ou null.
const resolveTab = (t) => { t = TAB_ALIASES[t] || t; return TABS.includes(t) ? t : null; };
const tabFromHash = () => resolveTab(location.hash.slice(1));

function savedTab() {
    try { return resolveTab(localStorage.getItem(TAB_KEY)); }
    catch (e) { return null; }
}

// Mostra a sub-aba name e esconde as demais (sem mexer na URL). Sem carteira, nenhuma aparece.
function showTab(name) {
    state.tab = name;
    const has = !!current();
    $('subtabs').hidden = !has;
    for (const t of TABS) {
        const active = t === name;
        $(`tab-${t}`).setAttribute('aria-selected', String(active));
        $(`tab-${t}`).tabIndex = active ? 0 : -1; // tabindex móvel: Tab entra só na aba ativa
        $(`panel-${t}`).hidden = !has || !active;
    }
    try { localStorage.setItem(TAB_KEY, name); } catch (e) { /* preferência não salva */ }
    // Um canvas desenhado enquanto a seção estava escondida fica com tamanho zero.
    if (has && name === 'visao') {
        for (const c of [state.chart, state.patrimonyChart]) if (c) { c.resize(); c.update('none'); }
    }
    if (has && name === 'metas' && !state.goals) loadGoals();
}

// Troca de sub-aba pela URL: o hash vira uma entrada do histórico (voltar/avançar).
function goTab(name, focus = false) {
    showTab(name);
    if (location.hash !== `#${name}`) location.hash = name;
    if (focus) $(`tab-${name}`).focus();
}

// Voltar/avançar, recarregar e links internos (href="#configuracoes", "#visao"...).
function onHashChange() {
    const t = tabFromHash();
    if (!t || !current()) return;
    if (t !== state.tab) showTab(t);
    if (location.hash === '#lancamentos') return $('operationsPanel').scrollIntoView(); // endereço antigo
    const bar = $('subtabs');
    if (bar.getBoundingClientRect().top < 0) bar.scrollIntoView(); // link clicado no meio da página
}

// Setas esquerda/direita (circulares), Home e End movem o foco e ativam a sub-aba.
function onTabKey(ev) {
    const k = TABS.indexOf(ev.target.dataset?.tab);
    const next = { ArrowRight: k + 1, ArrowLeft: k - 1, Home: 0, End: TABS.length - 1 }[ev.key];
    if (k < 0 || next === undefined) return;
    ev.preventDefault();
    goTab(TABS[(next + TABS.length) % TABS.length], true);
}

function renderKpis(a) {
    const k = a.kpis;
    const colored = (id, text, v) => { $(id).innerText = text; $(id).className = `kpi-value ${signClass(v)}`; };
    $('kpiValue').innerText = fmtBRL(k.total);
    $('kpiValueNote').innerText = `${a.open.length} ativo(s) e ${a.fixed.length} título(s) de renda fixa`;
    $('kpiInvested').innerText = fmtBRL(k.invested);
    colored('kpiProfit', `${fmtBRL(k.profit)}${k.profitPct === null ? '' : ` (${fmtPct(k.profitPct, 2)})`}`, k.profit);
    $('kpiProfitNote').innerText = `Valorização ${fmtBRL(k.unrealized)} · proventos ${fmtBRL(k.dividendsTotal)} · realizado ${fmtBRL(k.realized)}`;
    $('kpiDividends').innerText = fmtBRL(k.dividendsTotal);
    $('kpiDividendsNote').innerText = 'Brutos, estimados pela data-com' + (a.hasForeign ? '; EUA sem os 30% de IR retidos' : '');
    if (k.day) {
        colored('kpiDay', `${fmtBRL(k.day.value)} (${fmtPct(k.day.pct, 2)})`, k.day.value);
        $('kpiDayNote').innerText = `Pregão de ${fmtDate(k.day.date)}`;
    } else {
        colored('kpiDay', '—', 0);
        $('kpiDayNote').innerText = '';
    }
    const compare = (r) => r ? `<span class="swatch" style="background:${SERIES[1].color}"></span>CDI ${fmtPct(r.cdi, 2)} · <span class="swatch" style="background:${SERIES[2].color}"></span>BOVA11 ${fmtPct(r.bova, 2)}<br>desde ${fmtDate(r.from)}` : '';
    colored('kpiTwrAll', k.sinceStart ? fmtPct(k.sinceStart.portfolio, 2) : '—', k.sinceStart?.portfolio || 0);
    $('kpiTwrAllNote').innerHTML = compare(k.sinceStart);
    colored('kpiTwr12', k.last12 ? fmtPct(k.last12.portfolio, 2) : '—', k.last12?.portfolio || 0);
    $('kpiTwr12Note').innerHTML = compare(k.last12);
}

// ---------------------------------------------------------------- Aporte

function onAporteInput(ev) {
    const portfolio = current();
    if (!portfolio) return;
    writeAportePrefs(portfolio.id, { ...readAportePrefs(portfolio.id), amount: $('aporteAmount').value, mode: $('aporteMode').value });
    closeBatch(); // a lista em conferência era do plano anterior
    // Valor sendo digitado: espera uma pausa antes de pedir o novo plano ao servidor.
    clearTimeout(state.amountTimer);
    if (ev?.type === 'input') state.amountTimer = setTimeout(renderAporte, AMOUNT_DELAY_MS);
    else renderAporte();
}

// Barra atual × meta: preenchimento = % atual; com after, uma segunda faixa mostra o % após a
// sugestão de aporte; o traço é a meta.
function allocBar(label, pct, target, after = null) {
    const w = (v) => Math.max(0, Math.min(100, v));
    const aria = `${label}: ${fmtNum(pct)}% atual${after === null ? '' : `, ${fmtNum(after)}% após o aporte`}, meta ${fmtNum(target)}%`;
    return `<div class="alloc-bar${after === null ? '' : ' dual'}" role="img" aria-label="${escapeHTML(aria)}">
        <div class="now" style="width:${w(pct)}%"></div>
        ${after === null ? '' : `<div class="after" style="width:${w(after)}%"></div>`}
        <div class="goal" style="left:calc(${w(target)}% - 1px)"></div></div>`;
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
            amount: $('aporteAmount').value, mode, base: $('aporteBase').value, selected: prefs.selected
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

    const kpi = (label, value, note = '') => `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-note">${note}</div></div>`;
    $('aporteKpis').innerHTML = [
        kpi('Patrimônio atual', fmtBRL(plan.total)),
        kpi('Valor estimado após o aporte', fmtBRL(plan.estimated), `aporte de ${fmtBRL(plan.amount)}`),
        kpi('Sugestão de aporte', plan.waiting ? 'Aguardar' : fmtBRL(plan.suggested),
            plan.waiting ? 'nenhum ativo elegível agora' : `efetivo em cotas ${fmtBRL(plan.effective)} · sobra ${fmtBRL(plan.leftover)}`)
    ].join('');

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
            ? 'Sugestão proporcional ao que falta para cada ativo elegível chegar ao valor ideal (só compra; quem está acima do ideal não recebe).'
            : 'Todos os ativos elegíveis já estão no valor ideal ou acima: a sugestão é proporcional às metas deles.');
        if (plan.redirected?.total > 0.004) {
            const where = plan.redirected.byClass.map(c => `${escapeHTML(c.cls)} ${fmtBRL(c.value)}`).join(', ');
            notes.push(`<strong>${fmtBRL(plan.redirected.total)}</strong> do aporte foram redirecionados para os elegíveis — é o que iria, no modo "todos", para ativos ${mode === 'pullback' ? 'fora do setup' : 'não escolhidos'} (${where}).`);
        }
    }
    if (plan.sells.length) notes.push(`Sem aporte por sinal de venda: ${plan.sells.map(escapeHTML).join(', ')}.`);
    if (plan.targetsOff) notes.push(`<span class="warn-text">As metas das classes somam ${fmtNum(plan.targetSum, 2)}%, não 100%.</span>`);
    $('aporteNote').innerHTML = notes.map(n => `<p>${n}</p>`).join('');
    $('aporteWarnings').innerHTML = plan.warnings.map(w => `<span class="warn-text">${escapeHTML(w)}</span>`).join('<br>');

    const money = (v) => `<span class="${signClass(Math.round(v * 100))}">${fmtBRL(v)}</span>`;
    $('aporteClassBody').innerHTML = plan.classRows.map(c => `
        <tr>
            <td class="nowrap"><strong>${escapeHTML(c.cls)}</strong></td>
            <td class="r nowrap">${fmtBRL(c.value)}</td>
            <td class="r nowrap">${fmtNum(c.pct, 2)}%</td>
            <td class="r nowrap">${fmtNum(c.target, 2)}%</td>
            <td class="r nowrap">${fmtBRL(c.ideal)}</td>
            <td class="r nowrap">${money(c.diff)}</td>
            <td class="r nowrap"><strong>${fmtBRL(c.suggestion)}</strong></td>
            <td>${allocBar(c.cls, c.pct, c.target, plan.amount ? c.pctAfter : null)}</td>
        </tr>`).join('') + `
        <tr class="total-row"><td><strong>Total</strong></td><td class="r nowrap"><strong>${fmtBRL(plan.total)}</strong></td><td class="r">100%</td>
            <td class="r nowrap">${fmtNum(plan.targetSum, 2)}%</td><td class="r nowrap"><strong>${fmtBRL(plan.estimated)}</strong></td>
            <td class="r nowrap">${fmtBRL(plan.classDiff)}</td><td class="r nowrap"><strong>${fmtBRL(plan.suggested)}</strong></td><td></td></tr>`;

    const assetRow = (r) => {
        const pick = `<td class="c pick"><input type="checkbox" data-pick="${escapeHTML(r.key)}" ${selected.has(r.key) ? 'checked' : ''} ${r.status.key === 'sell' ? 'disabled' : ''}
            aria-label="Incluir ${escapeHTML(r.label)} no aporte"></td>`;
        const native = r.kind === 'quote' && r.market !== 'b3' ? `<div class="small muted">${MARKETS[r.market].label}</div>` : '';
        const target = targetField(r, 'tg');
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
        const effective = r.suggestion > 0 ? `${fmtBRL(r.effective)}${r.leftover > 0.004 ? `<div class="small muted">sobra ${fmtBRL(r.leftover)}</div>` : ''}` : '<span class="muted">—</span>';
        return `
        <tr>
            ${pick}
            <td><strong>${escapeHTML(r.label)}</strong>${native}</td>
            <td><span class="badge ${PLAN_BADGE[r.status.key]}">${escapeHTML(r.status.label)}</span></td>
            <td class="r nowrap">${fmtBRL(r.value)}</td>
            <td class="r nowrap">${fmtNum(r.pct, 2)}%</td>
            <td class="r nowrap">${target}</td>
            <td class="r nowrap">${fmtBRL(r.ideal)}</td>
            <td class="r nowrap">${money(r.diff)}</td>
            <td class="r">${suggestion}</td>
            <td class="r nowrap">${shares}</td>
            <td class="r nowrap">${effective}</td>
        </tr>`;
    };
    const groups = plan.classRows.filter(c => c.members.length).map(c => `
        <tr class="group-row"><td colspan="11"><strong>${escapeHTML(c.cls)}</strong> <span class="small muted">meta ${fmtNum(c.target, 2)}% · ${c.members.length} ativo(s)</span></td></tr>
        ${c.members.map(assetRow).join('')}
        <tr class="total-row">
            <td class="pick"></td><td colspan="2"><strong>Total ${escapeHTML(c.cls)}</strong></td>
            <td class="r nowrap"><strong>${fmtBRL(c.value)}</strong></td><td class="r nowrap">${fmtNum(c.pct, 2)}%</td>
            <td class="r nowrap">${fmtNum(c.target, 2)}%</td><td class="r nowrap">${fmtBRL(c.ideal)}</td>
            <td class="r nowrap">${money(c.diff)}</td><td class="r nowrap"><strong>${fmtBRL(c.suggestion)}</strong></td>
            <td></td><td class="r nowrap">${fmtBRL(c.effective)}</td>
        </tr>`);
    $('aporteAssetsBody').innerHTML = groups.length ? groups.join('')
        : `<tr><td colspan="11" class="empty-row">Nenhum ativo com posição.</td></tr>`;

    // "Registrar compras sugeridas": só com ao menos um ativo cotado elegível com cotas > 0.
    $('aporteRegisterBtn').hidden = !plan.canRegister;
    if (!plan.canRegister && state.batch) closeBatch();
}

// ---------------------------------------------------------------- Calculadora recolhível

function setAporteCollapsed(collapsed) {
    $('aporteBody').hidden = collapsed;
    $('aporteToggle').setAttribute('aria-expanded', String(!collapsed));
    $('aporteToggle').innerText = collapsed ? 'Expandir' : 'Recolher';
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0'); } catch (e) { /* preferência não salva */ }
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

// Campo da meta de um ativo (% do patrimônio): vazio = automática. Aparece na tabela do Aporte
// (prefixo "tg") e na de Configurações ("cfg-tg"); as duas salvam por onAporteTableChange.
function targetField(r, prefix) {
    const id = escapeHTML(`${prefix}-${r.key}`);
    return `<label class="sr-only" for="${id}">Meta de ${escapeHTML(r.label)} (% do patrimônio)</label>
            <input type="number" class="select target-input" id="${id}" data-target-key="${escapeHTML(r.key)}" min="0" max="100" step="0.01"
                value="${r.autoTarget ? '' : r.target}" placeholder="${fmtNum(r.target, 2)}">
            <div class="small muted">${r.autoTarget ? 'automática' : 'fixada'}</div>`;
}

// Checkbox de "escolher ativos" (só preferência local) ou meta fixada de um ativo (salva),
// vinda da tabela do Aporte ou da de Configurações.
async function onAporteTableChange(ev) {
    const portfolio = current();
    const el = ev.target;
    if (!portfolio) return;
    closeBatch(); // escolha ou meta mudou: a lista em conferência ficou velha
    if (el.dataset?.pick) {
        const prefs = readAportePrefs(portfolio.id);
        const set = new Set(prefs.selected);
        if (el.checked) set.add(el.dataset.pick); else set.delete(el.dataset.pick);
        writeAportePrefs(portfolio.id, { ...prefs, selected: [...set] });
        return renderAporte();
    }
    const key = el.dataset?.targetKey;
    if (!key) return;
    const res = await act('meta_ativo', { key, target: el.value });
    // Valor inválido: volta a mostrar a meta em vigor.
    if (res.error && !res.saveFailed) { renderAporte(); renderAssetTargets(state.analysis); }
}

// ---------------------------------------------------------------- Gráfico

// Linha vertical acompanhando o tooltip (cruz do modo "index").
const crosshairPlugin = {
    id: 'crosshair',
    afterDatasetsDraw(chart) {
        const active = chart.tooltip?.getActiveElements?.() || [];
        if (!active.length) return;
        const x = active[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
        ctx.save();
        ctx.strokeStyle = cssVar('--text-muted');
        ctx.globalAlpha = 0.5;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
        ctx.restore();
    }
};

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// Mês "2026-10" → "out/26".
const fmtMonth = (m) => `${new Date(+m.slice(0, 4), +m.slice(5, 7) - 1, 1).toLocaleString('pt-BR', { month: 'short' }).replace('.', '')}/${m.slice(2, 4)}`;
const fmtCompactBRL = (v) => `R$ ${v.toLocaleString('pt-BR', { notation: 'compact', maximumFractionDigits: 1 })}`;

const signedBRL = (v) => `${v > 0.004 ? '+' : ''}${fmtBRL(v)}`;
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
    const classColor = (g, k) => CLASS_COLORS[g.key] || EXTRA_COLORS[k % EXTRA_COLORS.length];
    const series = byClass
        ? classes.map((g, k) => ({ label: g.label, color: classColor(g, k), data: barsOf(g).map(b => b.value) }))
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
    const text = cssVar('--text-color'), muted = cssVar('--text-muted'), grid = 'rgba(139, 148, 158, 0.12)';
    state.patrimonyChart = new Chart($('patrimonyChart'), {
        type: 'bar',
        data: {
            labels,
            datasets: series.map(s => ({
                label: s.label, data: s.data, backgroundColor: s.color, hoverBackgroundColor: s.color, borderRadius: 2,
                maxBarThickness: 36, stack: 'patrimonio'
            }))
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
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
                    backgroundColor: cssVar('--panel-bg'), borderColor: cssVar('--border-color'), borderWidth: 1,
                    titleColor: text, bodyColor: text, padding: 10, displayColors: false,
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
            `<span class="nowrap"><span class="swatch-box" style="background:${CLASS_COLORS[g.key] || EXTRA_COLORS[k % EXTRA_COLORS.length]}"></span>${escapeHTML(g.label)} <strong class="${signClass(cs.gain)}">${signedBRL(cs.gain)}</strong></span>`
        ).join(' · ')}</p>`;
    }
    $('patrimonySummary').innerHTML = html;
}

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
    const text = cssVar('--text-color'), muted = cssVar('--text-muted'), grid = 'rgba(139, 148, 158, 0.12)';
    const datasets = SERIES.map((s, k) => ({
        label: s.label, data: data[k], borderColor: s.color, backgroundColor: s.color,
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 6, tension: 0
    }));
    const xLabels = labels.map(d => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(2, 4)}`);
    if (state.chart) {
        state.chart.data.labels = xLabels;
        state.chart.data.datasets.forEach((ds, k) => { ds.data = data[k]; });
        state.chart.update();
        return;
    }
    state.chart = new Chart($('returnChart'), {
        type: 'line',
        data: { labels: xLabels, datasets },
        plugins: [crosshairPlugin],
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: text, usePointStyle: true, pointStyle: 'line', boxWidth: 24 } },
                tooltip: {
                    backgroundColor: cssVar('--panel-bg'), borderColor: cssVar('--border-color'), borderWidth: 1,
                    titleColor: text, bodyColor: text, padding: 10, usePointStyle: true,
                    callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtPct(ctx.parsed.y, 2)}` }
                }
            },
            scales: {
                x: { ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false }, border: { color: grid } },
                y: { ticks: { color: muted, callback: (v) => `${fmtNum(v, 0)}%` }, grid: { color: grid }, border: { display: false } }
            }
        }
    });
}

// ---------------------------------------------------------------- Alocação e classes

// Visão geral: alocação por classe só para leitura (a edição fica em Configurações).
function renderAllocation(a) {
    $('allocationViewBody').innerHTML = a.allocation.map(r => `
        <tr>
            <td class="nowrap"><strong>${escapeHTML(r.cls)}</strong></td>
            <td class="r nowrap">${fmtBRL(r.value)}</td>
            <td class="r nowrap">${fmtNum(r.pct)}%</td>
            <td class="r nowrap">${fmtNum(r.target)}%</td>
            <td class="r nowrap ${signClass(r.diff)}">${r.diff >= 0 ? '+' : ''}${fmtNum(r.diff)} p.p.</td>
            <td class="r nowrap">${Math.abs(r.toTarget) < 0.005 ? '—' : r.toTarget > 0 ? `aportar ${fmtBRL(r.toTarget)}` : `excede ${fmtBRL(-r.toTarget)}`}</td>
            <td>${allocBar(r.cls, r.pct, r.target)}</td>
        </tr>`).join('') + `
        <tr><td><strong>Total</strong></td><td class="r nowrap"><strong>${fmtBRL(a.kpis.total)}</strong></td><td class="r">100%</td>
            <td class="r nowrap"><strong>${fmtNum(a.targetSum)}%</strong></td><td colspan="3"></td></tr>`;
    $('allocationNote').innerHTML = (a.targetsOff ? `<span class="warn-text">As metas somam ${fmtNum(a.targetSum)}%, não 100%.</span> ` : '')
        + 'Barra: preenchimento = % atual; traço = % meta.';
}

// Configurações: as classes fixas (as mesmas da aba Configurações do site), com a meta editável, o
// que há em cada uma e a regra de venda que a "Situação" segue. Usa só a estrutura da carteira (não
// precisa de cotações), então funciona antes do primeiro lançamento.
function renderClassConfig(st) {
    const classes = st?.classes || [];
    $('allocationBody').innerHTML = classes.map((c, k) => `
        <tr>
            <td class="nowrap"><strong>${escapeHTML(c.name)}</strong></td>
            <td class="r nowrap"><label class="sr-only" for="target-${k}">Meta de ${escapeHTML(c.name)} (%)</label>
                <input type="number" class="select target-input" id="target-${k}" data-class="${escapeHTML(c.name)}" min="0" max="100" step="0.5" value="${c.target}"></td>
            <td class="small">${c.members.length ? escapeHTML(c.members.join(', ')) : '<span class="muted">vazia</span>'}</td>
            <td class="small muted">${escapeHTML(c.rule || '')}</td>
        </tr>`).join('') + `
        <tr><td><strong>Total</strong></td><td class="r nowrap"><strong>${fmtNum(st?.targetSum || 0)}%</strong></td><td colspan="2"></td></tr>`;
    const warn = $('targetsWarning');
    const off = !!st?.targetsOff;
    warn.style.color = off ? 'var(--warning)' : 'var(--text-muted)';
    warn.innerText = off ? `As metas somam ${fmtNum(st.targetSum)}%, não 100%. Ajuste os valores para que "para atingir a meta" e o aporte façam sentido.`
        : 'A meta é salva na carteira ao editar.';
}

// Configurações: meta de cada ativo com posição, agrupada por classe (mesmo cálculo do Aporte).
function renderAssetTargets(a) {
    if (!a) {
        $('assetTargetsBody').innerHTML = '<tr><td colspan="3" class="empty-row">Registre uma operação para definir a meta de cada ativo.</td></tr>';
        $('assetTargetsWarnings').innerHTML = '';
        return;
    }
    const { groups, warnings } = a.assetTargets;
    $('assetTargetsBody').innerHTML = groups.map(g =>
        `<tr class="group-row"><td colspan="3"><strong>${escapeHTML(g.cls)}</strong> <span class="small muted">meta da classe ${fmtNum(g.target, 2)}%</span></td></tr>`
        + g.members.map(i => `
        <tr>
            <td><strong>${escapeHTML(i.label)}</strong></td>
            <td class="r nowrap">${fmtNum(i.pct, 2)}%</td>
            <td class="r nowrap">${targetField(i, 'cfg-tg')}</td>
        </tr>`).join('')).join('') || '<tr><td colspan="3" class="empty-row">Nenhum ativo com posição.</td></tr>';
    $('assetTargetsWarnings').innerHTML = warnings.map(w => `<span class="warn-text">${escapeHTML(w)}</span>`).join('<br>');
}

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

const tradingViewSymbol = (ticker, market) => market === 'us' ? ticker : market === 'crypto' ? `${ticker}USD` : `BMFBOVESPA:${ticker}`;

function renderPositions(a) {
    const rows = a.open.map(p => `
        <tr>
            <td><a href="https://br.tradingview.com/chart/?symbol=${encodeURIComponent(tradingViewSymbol(p.ticker, p.market))}" target="_blank" rel="noopener" class="ticker-link">${p.ticker}</a>
                ${p.market === 'crypto' ? '' : `<a href="backtest.html?ticker=${encodeURIComponent(p.ticker)}&market=${p.market}" class="inline-link small" aria-label="Backtest de ${p.ticker}">backtest</a>`}
                <div class="small muted">${escapeHTML(p.cls)}${p.market === 'b3' ? '' : ` · ${MARKETS[p.market].label}`}</div></td>
            <td class="r nowrap">${fmtQty(p.quantity)}</td>
            <td class="r nowrap">${fmtBRL(p.avgPrice)}</td>
            <td class="r nowrap">${fmtBRL(p.price)}${p.hasQuote ? '' : '<div class="small muted">sem cotação</div>'}${p.market !== 'b3' && p.hasQuote ? `<div class="small muted">${fmtMoney(p.priceNative, p.currency)} × ${fmtNum(p.fx, 4)}</div>` : ''}</td>
            <td class="r nowrap ${signClass(p.dayChange)}">${p.dayChange === null ? '—' : fmtPct(p.dayChange, 2)}</td>
            <td class="r nowrap">${fmtBRL(p.value)}</td>
            <td class="r nowrap ${signClass(p.profit)}">${fmtBRL(p.profit)}</td>
            <td class="r nowrap ${signClass(p.profit)}">${p.returnPct === null ? '—' : fmtPct(p.returnPct, 2)}</td>
            <td class="r nowrap">${fmtBRL(p.dividends)}</td>
            <td class="r nowrap">${fmtNum(p.weight)}%</td>
            <td class="c">${p.status ? signalBadge(p.status, p.statusReason, STATUS_BADGE[p.statusKey] || 'badge-low') : '<span class="small muted">—</span>'}
                ${p.partial ? `<div class="small muted nowrap">${fmtQty(p.partial.quantity)} ${p.partial.quantity === 1 ? 'cota' : 'cotas'} de ${p.partial.lots} compra(s)</div>` : ''}</td>
        </tr>`);
    const fixedRows = a.fixed.map(f => `
        <tr class="fi-section">
            <td><strong>${escapeHTML(f.name)}</strong><div class="small muted">${escapeHTML(f.cls)}</div></td>
            <td colspan="4" class="small muted">Investido líquido ${fmtBRL(f.invested)} · valor atualizado em ${fmtDate(f.lastUpdate)}</td>
            <td class="r nowrap">${fmtBRL(f.value)}</td>
            <td class="r nowrap ${signClass(f.profit)}">${fmtBRL(f.profit)}</td>
            <td class="r nowrap ${signClass(f.profit)}">${f.returnPct === null ? '—' : fmtPct(f.returnPct, 2)}</td>
            <td class="r muted">—</td>
            <td class="r nowrap">${fmtNum(f.weight)}%</td>
            <td class="c"><span class="small muted">—</span></td>
        </tr>`);
    $('positionsBody').innerHTML = rows.length || fixedRows.length ? [...rows, ...fixedRows].join('')
        : `<tr><td colspan="11" class="empty-row">Nenhuma posição aberta.</td></tr>`;
}

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
    $('historyBody').innerHTML = shown.length ? shown.map(r => `
        <tr>
            <td class="nowrap">${fmtDate(r.date)}</td>
            <td><strong>${escapeHTML(r.asset)}</strong></td>
            <td>${r.type}</td>
            <td class="r nowrap">${r.qty}</td>
            <td class="r nowrap">${r.price}</td>
            <td class="r nowrap">${r.total}</td>
            <td class="c"><button type="button" class="link-btn" data-kind="${r.kind}" data-id="${escapeHTML(r.id)}" ${r.item ? `data-item="${escapeHTML(r.item)}"` : ''}
                aria-label="Excluir ${r.type.toLowerCase()} de ${escapeHTML(r.asset)} em ${fmtDate(r.date)}">Excluir</button></td>
        </tr>`).join('')
        : `<tr><td colspan="7" class="empty-row">Nenhum lançamento.</td></tr>`;
}

async function onHistoryClick(ev) {
    const btn = ev.target.closest?.('button[data-id]');
    if (!btn || !confirm('Excluir este lançamento?')) return;
    const { kind, id, item } = btn.dataset;
    const res = await act('excluir_lancamento', { kind, entryId: id, itemId: item });
    if (res.error && !res.saveFailed) alert(res.error);
}

function renderDividends(a) {
    $('dividendTotals').innerHTML = a.dividendTotals.length
        ? `Total por ativo: ${a.dividendTotals.map(([t, v]) => `<strong>${t}</strong> ${fmtBRL(v)}`).join(' · ')} · <strong>Total ${fmtBRL(a.kpis.dividendsTotal)}</strong>`
        : '';
    $('dividendsBody').innerHTML = a.dividends.length ? a.dividends.map(d => `
        <tr>
            <td class="nowrap">${fmtDate(d.comDate)}</td>
            <td><strong>${d.ticker}</strong></td>
            <td class="r nowrap">R$ ${d.amount.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}</td>
            <td class="r nowrap">${fmtQty(d.quantity)}</td>
            <td class="r nowrap">${fmtBRL(d.total)}</td>
        </tr>`).join('')
        : `<tr><td colspan="5" class="empty-row">Nenhum provento no período das posições.</td></tr>`;
}

// Proventos anunciados e ainda não pagos (já ordenados por data de pagamento, a definir por último).
function renderReceivables(result) {
    if (!result.ok) {
        $('receivableTotals').innerHTML = `<span class="neg">Proventos anunciados indisponíveis (${escapeHTML(result.e.message)}).</span>`;
        $('receivablesBody').innerHTML = `<tr><td colspan="8" class="empty-row">Nenhum provento anunciado a receber.</td></tr>`;
        return;
    }
    const r = result.r, rows = r.rows;
    const parts = [];
    if (rows.length) {
        parts.push(`Total a receber: <strong>${fmtBRL(r.total)}</strong> bruto · ${fmtBRL(r.net)} líquido estimado (JCP com ${fmtNum(r.jcpTax * 100, 0)}% de IR)`);
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
}

// ---------------------------------------------------------------- Formulários

function setMsg(id, text, kind) {
    $(id).innerText = text || '';
    $(id).className = `form-msg ${kind || ''}`;
}

// Confirmação de um lançamento salvo, com atalho para a Visão geral (a aba não muda sozinha).
function setSavedMsg(id, text) {
    setMsg(id, text, 'ok');
    $(id).insertAdjacentHTML('beforeend', ' <a href="#visao" class="inline-link">Ver na visão geral</a>');
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
    for (const id of ['txTicker', 'txQty', 'txPrice', 'txFx']) $(id).value = '';
    $('txFees').value = '0';
    $('txPriceHint').innerText = $('txFxHint').innerText = '';
    $('txClassField').hidden = $('txMarketField').hidden = $('txFxField').hidden = true;
    $('txMarket').value = 'b3';
    $('txPriceLabel').innerText = 'Preço (R$)';
    $('txFeesLabel').innerText = 'Taxas (R$)';
    state.classTouched = false;
}

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
    for (const g of res.goals) drawGoalChart(g, `goalChart-${g.id}`);
}

const money0 = (v) => `R$ ${fmtNum(v, 0)}`;
const rate = (v, d = 2) => `${fmtNum(v, d)}%`;
const monthYear = (iso) => `${iso.slice(5, 7)}/${iso.slice(0, 4)}`;

function goalKpi(label, value, note = '') {
    return `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-note">${note}</div></div>`;
}

// Cartão de uma meta (saved = tem botões de editar e excluir; a prévia não tem).
function goalCard(g, saved) {
    const r = g.result, reach = r.reach, by = r.byDate;
    const head = g.kind === 'renda'
        ? `Renda de ${fmtBRL(g.target)} por mês · retirada de ${rate(g.withdrawal, 1)} ao ano`
        : `Patrimônio de ${fmtBRL(g.target)}`;
    const start = g.usePortfolio ? 'patrimônio atual da carteira' : 'patrimônio inicial informado';
    const kpis = [
        goalKpi('Patrimônio necessário', money0(r.goalValue),
            g.kind === 'renda' ? `para ${fmtBRL(g.target)}/mês com ${rate(g.withdrawal, 1)} ao ano`
                : `renda possível: ${fmtBRL(r.income)}/mês (${rate(g.withdrawal, 1)} a.a.)`),
        goalKpi('Patrimônio de partida', money0(r.current), `${start} · rende ${fmtBRL(r.incomeNow)}/mês hoje`),
        goalKpi('Falta acumular', money0(r.missing), `${fmtNum(r.progress, 1)}% da meta já alcançado`),
        goalKpi('Tempo para alcançar', reach ? escapeHTML(reach.text) : '+100 anos',
            reach ? (reach.months ? `previsão: ${monthYear(reach.date)}` : 'meta já alcançada') : 'não alcança no ritmo atual'),
        goalKpi('Aporte mensal', fmtBRL(g.monthly), g.adjust ? 'corrigido pela inflação todo ano' : 'valor fixo (perde poder de compra)'),
        goalKpi('Rentabilidade real', `${rate(r.rates.realYear)} a.a.`,
            `${rate(r.rates.realMonth, 3)} a.m. · nominal ${rate(r.rates.nominalYear)} a.a. (${rate(r.rates.nominalMonth, 3)} a.m.) · inflação ${rate(r.rates.inflationYear)} a.a.`),
        reach ? goalKpi('Total aportado até a meta', money0(reach.contributed + r.current),
            `aportes ${money0(reach.contributed)} + partida ${money0(r.current)} · aportes nominais ${money0(reach.nominalContributed)}`) : '',
        reach ? goalKpi('Juros compostos', money0(reach.interest), `${fmtNum(reach.interestShare, 1)}% do patrimônio na meta vem dos juros`) : '',
        goalKpi('Renda passiva na meta', `${fmtBRL(r.income)}/mês`, `juros reais de um mês na meta: ${fmtBRL(r.monthlyInterestAtGoal)}`),
        goalKpi('Ponto de virada', r.turningPoint ? escapeHTML(r.turningPoint.text) : '—',
            r.turningPoint ? `em ${monthYear(r.turningPoint.date)} os juros do mês passam o aporte` : 'os juros do mês não passam o aporte no horizonte'),
        reach && reach.months ? goalKpi('Valor nominal na data', money0(reach.nominalValue), 'o patrimônio necessário corrigido pela inflação até lá') : ''
    ].join('');
    const milestones = r.milestones.map(m => `<span class="goal-chip${m.reached ? ' done' : ''}">${m.pct}% · ${escapeHTML(m.text)}${m.date && m.months ? ` (${monthYear(m.date)})` : ''}</span>`).join('');
    const scen = r.scenarios;
    const scenarios = `<table class="goal-scenarios"><thead><tr><th>Aporte mensal</th>${scen.rates.map(x => `<th class="r">${rate(x, 1)} a.a.</th>`).join('')}</tr></thead>
        <tbody>${scen.rows.map(row => `<tr><td class="nowrap">${fmtBRL(row.monthly)}</td>${row.cells.map(c => `<td class="r nowrap${c.current ? ' goal-current' : ''}">${escapeHTML(c.text)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    const yearly = `<table><thead><tr><th class="r">Ano</th><th>Data</th><th class="r">Aportado (com a partida)</th><th class="r">Juros</th><th class="r">Patrimônio</th><th class="r">Renda possível/mês</th><th class="r">% da meta</th></tr></thead>
        <tbody>${r.yearly.map(y => `<tr><td class="r">${y.year}</td><td class="nowrap">${monthYear(y.date)}</td><td class="r nowrap">${money0(y.contributed)}</td>
        <td class="r nowrap">${money0(y.interest)}</td><td class="r nowrap"><strong>${money0(y.value)}</strong></td><td class="r nowrap">${fmtBRL(y.income)}</td><td class="r nowrap">${fmtNum(y.progress, 1)}%</td></tr>`).join('')}</tbody></table>`;
    const actions = saved ? `<div class="batch-buttons"><button type="button" class="btn-secondary" data-goal-edit="${escapeHTML(g.id)}">Editar</button>
        <button type="button" class="btn-secondary" data-goal-delete="${escapeHTML(g.id)}">Excluir</button></div>` : '<span class="badge badge-info">Prévia, não salva</span>';
    return `<div class="panel goal-card">
        <div class="panel-head"><div><h2>${escapeHTML(g.name)}</h2><div class="small muted">${escapeHTML(g.kindLabel)} · ${head}</div></div>${actions}</div>
        <p class="goal-summary">${escapeHTML(r.summary)}</p>
        <div class="goal-progress" role="img" aria-label="${fmtNum(r.progress, 1)}% da meta alcançado"><div style="width:${Math.max(0, Math.min(100, r.progress))}%"></div></div>
        <div class="small muted">${fmtNum(r.progress, 1)}% · ${fmtBRL(r.current)} de ${fmtBRL(r.goalValue)}</div>
        ${by ? `<p class="goal-bydate ${by.onTrack ? 'pos' : 'warn-text'}">${escapeHTML(by.summary)}</p>` : ''}
        ${r.warnings.map(w => `<p class="warn-text small">${escapeHTML(w)}</p>`).join('')}
        <div class="kpis goal-kpis">${kpis}</div>
        ${goalTracking(g)}
        ${goalProjections(r.projections)}
        <h3>Marcos</h3><div class="goal-chips">${milestones}</div>
        <h3>Evolução do patrimônio (valores de hoje)</h3>
        <div class="chart-wrap goal-chart"><canvas id="goalChart-${escapeHTML(g.id)}" role="img" aria-label="Patrimônio projetado, valor aportado e meta, ano a ano"></canvas></div>
        <h3>Tempo para alcançar em outros cenários</h3>
        <div class="table-container">${scenarios}</div>
        <details class="goal-details"><summary>Tabela ano a ano</summary><div class="table-container">${yearly}</div></details>
    </div>`;
}

// Realizado × planejado desde a criação da meta (só nas que partem da carteira).
function goalTracking(g) {
    const t = g.result.tracking;
    if (!t) return '';
    if (!t.started) return `<h3>Realizado × planejado</h3><p class="small muted">${escapeHTML(t.summary)}</p>`;
    return `<h3>Realizado × planejado <span class="small muted">desde ${fmtDate(t.created)}</span></h3>
        <div class="kpis goal-kpis">
            ${goalKpi('Planejado para hoje', fmtBRL(t.planned), `partindo de ${fmtBRL(t.startValue)} em ${fmtDate(t.created)}`)}
            ${goalKpi('Patrimônio real', fmtBRL(t.actual), 'carteira com as cotações de agora')}
            ${goalKpi(t.ahead ? 'À frente do plano' : 'Atrás do plano', `<span class="${t.ahead ? 'pos' : 'neg'}">${fmtBRL(Math.abs(t.diff))}</span>`,
                t.diffPct === null ? '' : `${fmtPct(t.diffPct, 1)} em relação ao planejado`)}
        </div>
        <p class="goal-bydate ${t.ahead ? 'pos' : 'warn-text'}">${escapeHTML(t.summary)}</p>
        <div class="chart-wrap goal-chart goal-chart-small"><canvas id="goalTrack-${escapeHTML(g.id)}" role="img" aria-label="Patrimônio real da carteira e o planejado pela meta, mês a mês"></canvas></div>`;
}

// Tempo para alcançar pelo plano e pelos dados reais da carteira (aporte médio, rentabilidade).
function goalProjections(rows) {
    if (!rows || rows.length < 2) return '';
    return `<h3>Previsão pelo plano e pelos dados reais da carteira</h3>
        <div class="table-container"><table>
            <thead><tr><th>Cenário</th><th class="r">Aporte mensal</th><th class="r">Rentabilidade</th><th class="r">Tempo para alcançar</th><th class="r">Previsão</th></tr></thead>
            <tbody>${rows.map(p => `<tr><td><strong>${escapeHTML(p.label)}</strong><div class="small muted">${escapeHTML(p.note)}</div></td>
                <td class="r nowrap">${fmtBRL(p.monthly)}</td><td class="r nowrap">${rate(p.rate, 1)} a.a.</td>
                <td class="r nowrap">${escapeHTML(p.text)}</td><td class="r nowrap">${p.date && p.months ? monthYear(p.date) : '—'}</td></tr>`).join('')}</tbody>
        </table></div>`;
}

function drawTrackingChart(g) {
    const t = g.result.tracking;
    const canvas = $(`goalTrack-${g.id}`);
    if (!t?.started || !canvas || typeof Chart === 'undefined') return;
    state.goalCharts.push(new Chart(canvas, {
        type: 'line',
        data: {
            labels: t.chart.labels.map(fmtDate),
            datasets: [
                { label: 'Patrimônio real', data: t.chart.actual, borderColor: GOAL_COLORS.value, borderWidth: 2, pointRadius: 2 },
                { label: 'Planejado', data: t.chart.planned, borderColor: GOAL_COLORS.goal, borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0 }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            plugins: { legend: { labels: { color: '#c9d1d9', boxWidth: 12 } },
                       tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtBRL(ctx.parsed.y)}` } } },
            scales: { x: { ticks: { color: '#8b949e', maxTicksLimit: 12 }, grid: { color: '#21262d' } },
                      y: { ticks: { color: '#8b949e', callback: (v) => fmtBRL(v) }, grid: { color: '#21262d' } } }
        }
    }));
}

function drawGoalChart(g, canvasId) {
    drawTrackingChart(g);
    const canvas = $(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;
    const y = g.result.yearly;
    const chart = new Chart(canvas, {
        type: 'line',
        data: {
            labels: y.map(p => p.date.slice(0, 4)),
            datasets: [
                { label: 'Patrimônio', data: y.map(p => p.value), borderColor: GOAL_COLORS.value, backgroundColor: 'rgba(57, 135, 229, 0.15)', fill: true, borderWidth: 2, pointRadius: 0 },
                { label: 'Aportado (com a partida)', data: y.map(p => p.contributed), borderColor: GOAL_COLORS.contributed, borderWidth: 2, pointRadius: 0 },
                { label: 'Meta', data: y.map(() => g.result.goalValue), borderColor: GOAL_COLORS.goal, borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0 }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            plugins: { legend: { labels: { color: '#c9d1d9', boxWidth: 12 } },
                       tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtBRL(ctx.parsed.y)}` } } },
            scales: { x: { ticks: { color: '#8b949e', maxTicksLimit: 12 }, grid: { color: '#21262d' } },
                      y: { ticks: { color: '#8b949e', callback: (v) => `R$ ${fmtNum(v / 1000, 0)} mil` }, grid: { color: '#21262d' } } }
        }
    });
    state.goalCharts.push(chart);
}

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
    $('goalFormTitle').innerText = '🎯 Nova meta';
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
    $('goalFormTitle').innerText = `✏️ Editar meta "${g.name}"`;
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

// "ANALISAR CARTEIRA": o texto para uma IA sobre a carteira e cada ativo dela vem pronto do
// servidor (portfolio_prompt.py); copyServerText (core.js) copia ou abre a janela para copiar.
async function analyzePortfolio() {
    const portfolio = current();
    if (!portfolio) return;
    try {
        await copyServerText($('analyzePortfolioBtn'), apiGet(`/carteira/${portfolio.id}/prompt`).then(r => r.text),
            `Análise da carteira ${portfolio.name}`);
    } catch (e) {
        showError(`Não foi possível montar a análise da carteira (${e.message}).`);
    }
}

function showError(text) { $('errorBanner').innerText = text; $('errorBanner').style.display = 'block'; }
function hideError() { $('errorBanner').style.display = 'none'; }
