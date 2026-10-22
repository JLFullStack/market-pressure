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
// Cores fixas por série, nesta ordem (sobre o fundo #0e1527 do painel).
const SERIES = [
    { key: 'portfolio', label: 'Carteira', color: '#5aa2ff' },
    { key: 'cdi', label: 'CDI', color: '#ff8a5b' },
    { key: 'bova', label: 'BOVA11', color: '#4ad991' }
];
const PERIODS = ['6M', '1A', '2A', 'Tudo']; // os mesmos do servidor (portfolio.PERIODS)
const PATRIMONY_PERIODS = ['30D', '1A', '2A', '5A', '10A', 'Tudo']; // os mesmos do servidor (portfolio.PATRIMONY_PERIODS)
// Barras da evolução do patrimônio: pedaços empilhados (portfolio.patrimony_bars), de baixo para cima.
const PATRIMONY_SERIES = [
    { key: 'base', label: 'Valor aplicado', color: '#4f8dff' },
    { key: 'up', label: 'Ganho de capital', color: '#3ddc97' },
    { key: 'down', label: 'Perda de capital', color: '#ff7b72' }
];
// Modo "Por classe": uma cor fixa por classe (portfolio.CLASSES); outras usam EXTRA_COLORS.
const CLASS_COLORS = { 'Ações': '#4f8dff', 'Fundos imobiliários': '#3ddc97', 'ETFs Brasil': '#ffc65c', 'Internacionais': '#a78bfa', 'Renda fixa': '#22d3ee' };
const EXTRA_COLORS = ['#f472b6', '#ff8a5b', '#94a3b8'];
const classColor = (name, k = 0) => CLASS_COLORS[name] || EXTRA_COLORS[k % EXTRA_COLORS.length];
const PATRIMONY_MODES = { valor: 'Aplicado e ganho', classes: 'Por classe' };
const APORTE_MODES = { todos: 'Todos os ativos da carteira', escolher: 'Escolher ativos', pullback: 'Pelas regras do pullback' };
// Indicação da posição (COMPRAR, VENDER, MANTER; portfolio.position_signal: statusKey) → badge.
const STATUS_BADGE = { sell: 'badge-hot', partial: 'badge-hot', buy: 'badge-ok', attention: 'badge-fill', hold: 'badge-info' };
const PLAN_BADGE = { sell: 'badge-hot', fixed: 'badge-low', setup: 'badge-ok', wait: 'badge-fill', candidate: 'badge-info' };
const AMOUNT_DELAY_MS = 250; // espera após a digitação do valor do aporte antes de recalcular
// Rótulo do campo de valor da meta, conforme o tipo.
const GOAL_TARGET_LABEL = { renda: 'Renda mensal desejada (R$)', patrimonio: 'Patrimônio desejado (R$)' };
const GOAL_COLORS = { value: '#5aa2ff', contributed: '#a78bfa', goal: '#ffc65c' };
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

const state = {
    doc: { version: 2, portfolios: [] },
    currentId: null,
    structure: null,     // resposta de /estrutura da carteira atual
    analysis: null,      // resposta de /analise
    plan: null,          // último plano de aporte exibido
    aporteCharts: {},    // gráficos da calculadora de aporte por canvas (atualizados no lugar a cada novo plano)
    period: '1A',
    patrimonyPeriod: '1A',
    patrimonyClass: '',  // classe do gráfico de patrimônio ('' = todas)
    patrimonyMode: 'valor', // 'valor' (aplicado e ganho) ou 'classes' (por classe)
    patrimonyChart: null,
    chartSeq: {},        // por canvas: descarta o gráfico que esperava aparecer na tela e já foi substituído
    allocationChart: null,
    dividendChart: null,
    targetsChart: null,
    receivables: null,   // última resposta de /proventos (gráfico mês a mês)
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

document.addEventListener('DOMContentLoaded', init);

async function init() {
    observeReveal();
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
    $('aporteCap').addEventListener('change', onAporteInput);
    $('aporteMax').addEventListener('input', onAporteInput);
    $('aporteAdvice').addEventListener('change', onAporteInput);
    $('aporteReuse').addEventListener('change', onAporteInput);
    $('aporteCandidates').addEventListener('change', onCandidateChange);
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
    $('portfolioTitle').innerText = current()?.name || 'Carteira';
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
        $('aporteCap').value = prefs.cap;
        if (!$('aporteCap').value) $('aporteCap').value = APORTE_DEFAULTS.cap;
        $('aporteMax').value = prefs.maxAssets;
        $('aporteAdvice').checked = prefs.advice !== false;
        $('aporteReuse').checked = prefs.reuse !== false;
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

    $('livePill').hidden = !a.live;
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
    revealItems($('receivablesBody'));
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
    if (has && (name === 'visao' || name === 'aportes')) {
        const charts = name === 'visao' ? [state.chart, state.patrimonyChart] : Object.values(state.aporteCharts);
        for (const c of charts) if (c) { c.resize(); c.update('none'); }
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

// Cor do cartão pelo sinal do valor (verde, vermelho, ou o tom neutro quando não há valor).
function toneBySign(card, v, neutral = 'tone-blue') {
    card.classList.remove('tone-green', 'tone-red', 'tone-blue', 'tone-cyan', 'tone-violet');
    card.classList.add(v === null || v === undefined ? neutral : v >= 0 ? 'tone-green' : 'tone-red');
}

// Carteira × CDI × BOVA11 num período: uma barra por série, proporcional ao maior retorno em
// módulo (as negativas em vermelho), com o valor ao lado.
function compareBars(r) {
    if (!r) return '';
    const rows = SERIES.map(s => ({ ...s, v: r[s.key] }));
    const max = Math.max(...rows.map(x => Math.abs(x.v)), 0.0001);
    return `<div class="cmp">${rows.map((x, k) => `<div class="cmp-row">
            <span class="cmp-label">${x.label}</span>
            <span class="cmp-track"><i style="--w:${(Math.abs(x.v) / max).toFixed(3)};--c:${x.v < 0 ? 'var(--danger)' : x.color};--k:${k}"></i></span>
            <span class="cmp-val num ${signClass(x.v)}">${fmtPct(x.v, 2)}</span></div>`).join('')}
        <div class="small muted">desde ${fmtDate(r.from)}</div></div>`;
}

function renderKpis(a) {
    const k = a.kpis;
    const delta = (id, v) => { $(id).innerText = v === null || v === undefined ? '' : fmtPct(v, 2); $(id).className = `kpi-delta ${signClass(v)}`; };
    setCount($('kpiValue'), k.total, { format: 'brl' });
    $('kpiValueNote').innerText = `${a.open.length} ativo(s) e ${a.fixed.length} título(s) de renda fixa`;
    const months = a.patrimony?.groups.find(g => g.key === '')?.bars.slice(-24).map(b => b.value) || [];
    drawSpark($('patrimonySpark'), months, months.length < 2 || months.at(-1) >= months[0]);
    setCount($('kpiInvested'), k.invested, { format: 'brl' });
    setCount($('kpiProfit'), k.profit, { format: 'sbrl' });
    delta('kpiProfitPct', k.profitPct);
    toneBySign($('kpiProfitCard'), k.profit);
    $('kpiProfitNote').innerText = `Valorização ${fmtBRL(k.unrealized)} · proventos ${fmtBRL(k.dividendsTotal)} · realizado ${fmtBRL(k.realized)}`;
    setCount($('kpiDividends'), k.dividendsTotal, { format: 'brl' });
    $('kpiDividendsNote').innerText = 'Brutos, estimados pela data-com' + (a.hasForeign ? '; EUA sem os 30% de IR retidos' : '');
    setCount($('kpiDay'), k.day ? k.day.value : null, { format: 'sbrl' });
    delta('kpiDayPct', k.day ? k.day.pct : null);
    toneBySign($('kpiDayCard'), k.day ? k.day.value : null);
    $('kpiDayNote').innerText = k.day ? `Pregão de ${fmtDate(k.day.date)}` : '';
    setCount($('kpiTwrAll'), k.sinceStart ? k.sinceStart.portfolio : null, { format: 'pct', decimals: 2 });
    toneBySign($('kpiTwrAllCard'), k.sinceStart ? k.sinceStart.portfolio : null);
    $('kpiTwrAllNote').innerHTML = compareBars(k.sinceStart);
    setCount($('kpiTwr12'), k.last12 ? k.last12.portfolio : null, { format: 'pct', decimals: 2 });
    toneBySign($('kpiTwr12Card'), k.last12 ? k.last12.portfolio : null);
    $('kpiTwr12Note').innerHTML = compareBars(k.last12);
}

// ---------------------------------------------------------------- Aporte

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

// Campo da meta de um ativo (% do patrimônio): vazio = automática. Aparece na tabela do Aporte
// (prefixo "tg") e na de Configurações ("cfg-tg"); as duas salvam por onAporteTableChange.
function targetField(r, prefix) {
    const id = escapeHTML(`${prefix}-${r.key}`);
    return `<label class="sr-only" for="${id}">Meta de ${escapeHTML(r.label)} (% do patrimônio)</label>
            <input type="number" class="select target-input" id="${id}" data-target-key="${escapeHTML(r.key)}" min="0" max="100" step="0.01"
                value="${r.autoTarget ? '' : r.target}" placeholder="${fmtNum(r.target, 2)}">
            <div class="small"><span class="target-mode ${r.autoTarget ? 'auto' : 'fixed'}">${r.autoTarget ? 'automática' : 'fixada'}</span></div>`;
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


// Cria o gráfico do canvas quando o painel dele aparece na tela (onReveal, em core.js), para a
// animação de entrada acontecer à vista. Um novo pedido para o mesmo canvas antes disso
// substitui o anterior (cada canvas tem a sua contagem: os gráficos não se cancelam entre si).
function whenVisible(canvasId, draw) {
    const seq = (state.chartSeq[canvasId] || 0) + 1;
    state.chartSeq[canvasId] = seq;
    onReveal($(canvasId), () => { if (state.chartSeq[canvasId] === seq) draw(); });
}

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
    const series = byClass
        ? classes.map((g, k) => ({ label: g.label, color: classColor(g.key, k), data: barsOf(g).map(b => b.value) }))
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
    const text = cssVar('--text-color'), muted = cssVar('--text-muted'), grid = CHART_GRID;
    // Criado quando o painel aparece na tela (a animação de entrada acontece à vista); um novo
    // desenho antes disso substitui o que estava esperando.
    whenVisible('patrimonyChart', () => {
    state.patrimonyChart?.destroy();
    state.patrimonyChart = new Chart($('patrimonyChart'), {
        type: 'bar',
        data: {
            labels,
            datasets: series.map(s => ({
                label: s.label, data: s.data, backgroundColor: verticalGradient(s.color, 0.95, 0.55), hoverBackgroundColor: s.color,
                borderRadius: 5, borderSkipped: false, maxBarThickness: 36, stack: 'patrimonio'
            }))
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: CHART_ENTER,
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
                    ...chartTooltip(), displayColors: false,
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
            `<span class="nowrap"><span class="swatch-box" style="background:${classColor(g.key, k)}"></span>${escapeHTML(g.label)} <strong class="${signClass(cs.gain)}">${signedBRL(cs.gain)}</strong></span>`
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
    const text = cssVar('--text-color'), muted = cssVar('--text-muted'), grid = CHART_GRID;
    // A carteira em destaque (linha mais grossa com área em degradê); CDI e BOVA11 tracejados.
    const datasets = SERIES.map((s, k) => ({
        label: s.label, data: data[k], borderColor: s.color, backgroundColor: k === 0 ? verticalGradient(s.color, 0.28, 0) : s.color,
        fill: k === 0 ? 'origin' : false, borderDash: k === 0 ? [] : [5, 4],
        borderWidth: k === 0 ? 2.5 : 1.6, pointRadius: 0, pointHoverRadius: 5, pointHoverBorderWidth: 2,
        pointHoverBackgroundColor: cssVar('--panel-bg'), pointHitRadius: 6, tension: 0.15
    }));
    const xLabels = labels.map(d => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(2, 4)}`);
    if (state.chart) {
        state.chart.data.labels = xLabels;
        state.chart.data.datasets.forEach((ds, k) => { ds.data = data[k]; });
        state.chart.update();
        return;
    }
    whenVisible('returnChart', () => {
    if (state.chart) return;
    state.chart = new Chart($('returnChart'), {
        type: 'line',
        data: { labels: xLabels, datasets },
        plugins: [crosshairPlugin],
        options: {
            responsive: true, maintainAspectRatio: false, animation: { duration: 1100, easing: 'easeOutCubic' },
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: text, usePointStyle: true, pointStyle: 'line', boxWidth: 24 } },
                tooltip: {
                    ...chartTooltip(), usePointStyle: true,
                    callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtPct(ctx.parsed.y, 2)}` }
                }
            },
            scales: {
                x: { ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false }, border: { color: grid } },
                y: { ticks: { color: muted, callback: (v) => `${fmtNum(v, 0)}%` }, grid: { color: grid }, border: { display: false } }
            }
        }
    });
    });
}

// ---------------------------------------------------------------- Alocação e classes

// Visão geral: alocação por classe só para leitura (a edição fica em Configurações).
function renderAllocation(a) {
    $('allocationViewBody').innerHTML = a.allocation.map((r, k) => `
        <tr>
            <td class="nowrap"><span class="swatch-box" style="background:${classColor(r.cls, k)}"></span><strong>${escapeHTML(r.cls)}</strong></td>
            <td class="r nowrap">${fmtBRL(r.value)}</td>
            <td class="r nowrap">${fmtNum(r.pct)}%</td>
            <td class="r nowrap">${fmtNum(r.target)}%</td>
            <td class="r nowrap ${signClass(r.diff)}">${r.diff >= 0 ? '+' : ''}${fmtNum(r.diff)} p.p.</td>
            <td class="r nowrap">${Math.abs(r.toTarget) < 0.005 ? '—' : r.toTarget > 0 ? `aportar ${fmtBRL(r.toTarget)}` : `excede ${fmtBRL(-r.toTarget)}`}</td>
            <td>${allocBar(r.cls, r.pct, r.target)}</td>
        </tr>`).join('') + `
        <tr><td><strong>Total</strong></td><td class="r nowrap"><strong>${fmtBRL(a.kpis.total)}</strong></td><td class="r">100%</td>
            <td class="r nowrap"><strong>${fmtNum(a.targetSum)}%</strong></td><td colspan="3"></td></tr>`;
    revealItems($('allocationViewBody'));
    $('allocationNote').innerHTML = (a.targetsOff ? `<span class="warn-text">As metas somam ${fmtNum(a.targetSum)}%, não 100%.</span> ` : '')
        + 'Barra: preenchimento = % atual; traço = % meta.';
    renderAllocationDonut(a);
}

// Rosca da alocação: anel de fora = % de hoje, anel de dentro = % meta, uma cor por classe; o
// patrimônio total no meio.
function renderAllocationDonut(a) {
    setCount($('allocationTotal'), a.kpis.total, { format: 'brl' });
    if (typeof Chart === 'undefined') return;
    const rows = a.allocation;
    const colors = rows.map((r, k) => classColor(r.cls, k));
    whenVisible('allocationDonut', () => {
        state.allocationChart?.destroy();
        state.allocationChart = new Chart($('allocationDonut'), {
            type: 'doughnut',
            data: {
                labels: rows.map(r => r.cls),
                datasets: [
                    { label: 'Hoje', data: rows.map(r => r.pct), backgroundColor: colors, borderColor: cssVar('--panel-bg'), borderWidth: 3, hoverOffset: 8, weight: 1.4 },
                    { label: 'Meta', data: rows.map(r => r.target), backgroundColor: colors.map(c => hexAlpha(c, 0.4)), borderColor: cssVar('--panel-bg'), borderWidth: 2, weight: 0.8 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: '58%',
                animation: { animateRotate: true, animateScale: false, duration: 1100, easing: 'easeOutQuart' },
                plugins: {
                    legend: { display: false },
                    tooltip: { ...chartTooltip(), callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtNum(ctx.parsed)}%` } }
                }
            }
        });
    });
}

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

// Peso na carteira como barra, proporcional à maior posição (a maior enche a barra).
const weightCell = (w, max) => `<div class="weight"><span class="num">${fmtNum(w)}%</span><span class="weight-bar"><i style="--w:${(max ? w / max : 0).toFixed(3)}"></i></span></div>`;
const deltaPill = (v) => v === null || v === undefined ? '—' : `<span class="delta-pill ${signClass(v)}">${fmtPct(v, 2)}</span>`;

function renderPositions(a) {
    const maxWeight = Math.max(0, ...a.open.map(p => p.weight), ...a.fixed.map(f => f.weight));
    const classIndex = (cls) => Math.max(0, a.allocation.findIndex(r => r.cls === cls));
    const avatar = (label, cls) => `<span class="avatar solo" style="--a1:${classColor(cls, classIndex(cls))}" aria-hidden="true">${escapeHTML(label.slice(0, 2).toUpperCase())}</span>`;
    const rows = a.open.map(p => `
        <tr data-reveal>
            <td><div class="asset">${avatar(p.ticker, p.cls)}<div class="asset-text"><a href="https://br.tradingview.com/chart/?symbol=${encodeURIComponent(tradingViewSymbol(p.ticker, p.market))}" target="_blank" rel="noopener" class="ticker-link">${p.ticker}</a>
                ${p.market === 'crypto' ? '' : `<a href="backtest.html?ticker=${encodeURIComponent(p.ticker)}&market=${p.market}" class="inline-link small" aria-label="Backtest de ${p.ticker}">backtest</a>`}
                <div class="small muted">${escapeHTML(p.cls)}${p.market === 'b3' ? '' : ` · ${MARKETS[p.market].label}`}</div></div></div></td>
            <td class="r nowrap">${fmtQty(p.quantity)}</td>
            <td class="r nowrap">${fmtBRL(p.avgPrice)}</td>
            <td class="r nowrap">${fmtBRL(p.price)}${p.hasQuote ? '' : '<div class="small muted">sem cotação</div>'}${p.market !== 'b3' && p.hasQuote ? `<div class="small muted">${fmtMoney(p.priceNative, p.currency)} × ${fmtNum(p.fx, 4)}</div>` : ''}</td>
            <td class="r nowrap ${signClass(p.dayChange)}">${p.dayChange === null ? '—' : fmtPct(p.dayChange, 2)}</td>
            <td class="r nowrap">${fmtBRL(p.value)}</td>
            <td class="r nowrap ${signClass(p.profit)}">${fmtBRL(p.profit)}</td>
            <td class="r nowrap">${deltaPill(p.returnPct)}</td>
            <td class="r nowrap">${fmtBRL(p.dividends)}</td>
            <td class="r nowrap">${weightCell(p.weight, maxWeight)}</td>
            <td class="c">${p.status ? signalBadge(p.status, p.statusReason, STATUS_BADGE[p.statusKey] || 'badge-low') : '<span class="small muted">—</span>'}
                ${p.partial ? `<div class="small muted nowrap">${fmtQty(p.partial.quantity)} ${p.partial.quantity === 1 ? 'cota' : 'cotas'} de ${p.partial.lots} compra(s)</div>` : ''}</td>
        </tr>`);
    const fixedRows = a.fixed.map(f => `
        <tr class="fi-section" data-reveal>
            <td><div class="asset">${avatar(f.name, f.cls)}<div class="asset-text"><strong>${escapeHTML(f.name)}</strong><div class="small muted">${escapeHTML(f.cls)}</div></div></div></td>
            <td colspan="4" class="small muted">Investido líquido ${fmtBRL(f.invested)} · valor atualizado em ${fmtDate(f.lastUpdate)}</td>
            <td class="r nowrap">${fmtBRL(f.value)}</td>
            <td class="r nowrap ${signClass(f.profit)}">${fmtBRL(f.profit)}</td>
            <td class="r nowrap">${deltaPill(f.returnPct)}</td>
            <td class="r muted">—</td>
            <td class="r nowrap">${weightCell(f.weight, maxWeight)}</td>
            <td class="c"><span class="small muted">—</span></td>
        </tr>`);
    $('positionsBody').innerHTML = rows.length || fixedRows.length ? [...rows, ...fixedRows].join('')
        : `<tr><td colspan="11" class="empty-row">Nenhuma posição aberta.</td></tr>`;
    revealItems($('positionsBody'));
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

// Proventos mês a mês: os recebidos nos últimos 12 meses (data-ex) e, à direita, os anunciados a
// receber por mês de pagamento (confirmados e previstos empilhados; "A definir" por último).
function renderDividendChart() {
    const a = state.analysis;
    if (!a || typeof Chart === 'undefined') return;
    const received = a.dividendMonths || [];
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
    $('dividendChartEmpty').hidden = any;
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

// "Quem mais pagou": barras dos ativos com mais proventos recebidos (proporcionais ao maior).
const DIVIDEND_RANKING = 8;
function renderDividendRanking(a) {
    const top = a.dividendTotals.slice(0, DIVIDEND_RANKING);
    const max = top.length ? top[0][1] : 0;
    $('dividendRanking').innerHTML = top.length ? top.map(([t, v], k) => `<div class="rank-bar" style="--k:${k}">
            <span class="rank-bar-label">${escapeHTML(t)}</span>
            <span class="rank-bar-track"><i style="--w:${(max ? v / max : 0).toFixed(3)}"></i></span>
            <span class="rank-bar-val num">${fmtBRL(v)}</span></div>`).join('')
        : '<p class="small muted">Nenhum provento recebido ainda.</p>';
}

function renderDividends(a) {
    setCount($('divYear'), a.dividendYear, { format: 'brl' });
    $('divYearNote').innerText = 'pela data-ex, brutos';
    setCount($('divTotal'), a.kpis.dividendsTotal, { format: 'brl' });
    $('divTotalNote').innerText = `${a.dividends.length} pagamento(s) desde a primeira compra`;
    renderDividendRanking(a);
    renderDividendChart();
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
    revealItems($('dividendsBody'));
}

// Proventos anunciados e ainda não pagos (já ordenados por data de pagamento, a definir por último).
function renderReceivables(result) {
    state.receivables = result.ok ? result.r : null;
    setCount($('divToReceive'), result.ok ? result.r.total : null, { format: 'brl' });
    $('divToReceiveNote').innerText = !result.ok ? 'proventos anunciados indisponíveis'
        : result.r.rows.length ? `${fmtBRL(result.r.net)} líquido estimado` : 'nenhum anunciado';
    renderDividendChart();
    if (!result.ok) {
        $('receivableTotals').innerHTML = `<span class="neg">Proventos anunciados indisponíveis (${escapeHTML(result.e.message)}).</span>`;
        $('receivablesBody').innerHTML = `<tr><td colspan="8" class="empty-row">Nenhum provento anunciado a receber.</td></tr>`;
        revealItems($('receivablesBody'));
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
    revealItems($('receivablesBody'));
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
