// Aba Carteira: o que todas as sub-abas usam. Depende de core.js, animacao.js e graficos.js
// (API, formatadores, efeitos e tema dos gráficos), carregados antes. Cada sub-aba tem o próprio
// arquivo nesta pasta (visao-geral.js, aportes-e-lancamentos.js, proventos.js, metas.js,
// impostos.js, configuracoes.js), carregado depois deste; init() chama o setup de cada uma.
//
// Todos os cálculos e todas as alterações ficam no servidor (marketpressure/carteira/). A página
// só pede e exibe:
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

//#region Constantes e estado
// Mercados de um ativo (rótulo e moeda exibidos). Fora da B3 o preço é em US$.
const MARKETS = {
    b3: { label: 'B3', currency: 'R$' },
    us: { label: 'EUA', currency: 'US$' },
    crypto: { label: 'Cripto', currency: 'US$' }
};
const SELECTED_KEY = 'marketpressure_carteira_atual'; // só a preferência; os dados ficam no servidor
const TAB_KEY = 'marketpressure_carteira_aba'; // última sub-aba, usada quando a URL não tem hash
// Sub-abas da página, na ordem da barra. Cada uma é #tab-{nome} (botão) e #panel-{nome} (seção);
// a ativa fica no hash da URL (#visao, #aportes...), então recarregar e voltar/avançar a mantêm.
const TABS = ['visao', 'aportes', 'proventos', 'metas', 'impostos', 'configuracoes'];
// Hashes (e valores salvos) das sub-abas antigas, que foram juntadas em "Aportes e lançamentos".
const TAB_ALIASES = { aportar: 'aportes', lancamentos: 'aportes' };
// Uma cor fixa por classe (portfolio.CLASSES); outras usam EXTRA_COLORS.
const CLASS_COLORS = { 'Ações': '#4f8dff', 'Fundos imobiliários': '#3ddc97', 'ETFs Brasil': '#ffc65c', 'Internacionais': '#a78bfa', 'Renda fixa': '#22d3ee' };
const EXTRA_COLORS = ['#f472b6', '#ff8a5b', '#94a3b8'];
const classColor = (name, k = 0) => CLASS_COLORS[name] || EXTRA_COLORS[k % EXTRA_COLORS.length];

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
    taxSeq: 0,           // idem para o aviso de imposto da venda digitada (GET /imposto)
    taxes: null,         // resposta de /impostos (pedida ao abrir a sub-aba)
    taxesSeq: 0,         // descarta respostas de /impostos fora de ordem
    taxYear: null,       // ano escolhido na sub-aba Impostos
    taxesFor: null,      // carteira a que taxYear se refere
    taxDarfMonth: null,  // mês mostrado no card do DARF (sem escolha: o mais antigo em aberto)
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
    editingGoal: null,   // id da meta em edição no formulário (null = nova)
    // Card "Operações no gráfico" (linha das Posições): resposta de /grafico, gráfico e séries,
    // linhas de alvo desenhadas, índices dos alvos visíveis e candles/linha.
    asset: null, assetPrice: null, assetLines: [], targetsShown: new Set(), assetSeq: 0, chartType: 'candles'
};

const $ = (id) => document.getElementById(id);
const current = () => state.doc.portfolios.find(p => p.id === state.currentId) || null;
const signClass = (v) => v > 0 ? 'pos' : v < 0 ? 'neg' : '';
// Quantidade: inteira sem casas; fracionária (cripto, EUA) com até 8 casas.
const fmtQty = (q) => Number.isInteger(q) ? fmtNum(q, 0) : q.toLocaleString('pt-BR', { maximumFractionDigits: 8 });
const fmtMoney = (v, currency = 'R$') => currency === 'R$' ? fmtBRL(v)
    : `${currency} ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
//#endregion

//#region Início e carteiras
document.addEventListener('DOMContentLoaded', init);

async function init() {
    observeReveal();
    $('portfolioSelect').addEventListener('change', (ev) => selectPortfolio(ev.target.value));
    $('newPortfolioBtn').addEventListener('click', createPortfolio);
    $('emptyCreateBtn').addEventListener('click', createPortfolio);
    $('renamePortfolioBtn').addEventListener('click', renamePortfolio);
    $('deletePortfolioBtn').addEventListener('click', deletePortfolio);
    $('refreshBtn').addEventListener('click', () => refresh());
    $('analyzePortfolioBtn').addEventListener('click', analyzePortfolio);
    $('subtabs').addEventListener('click', (ev) => { const tab = ev.target.closest?.('[data-tab]'); if (tab) goTab(tab.dataset.tab); });
    $('subtabs').addEventListener('keydown', onTabKey);
    window.addEventListener('hashchange', onHashChange);
    // Cada sub-aba liga os próprios campos e botões (no arquivo dela).
    setupVisaoGeral();
    setupAportes();
    setupMetas();
    setupImpostos();
    setupConfiguracoes();
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
//#endregion

//#region Carregar e redesenhar
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
    state.taxes = null; // os impostos também
    showTab(state.tab);
    renderFixedIncomeSelect(st);
    renderClassSelects(st);
    renderClassConfig(st);
    renderHistory(st);
    if (portfolio && state.aporteFor !== portfolio.id) applyAportePrefs(portfolio);
    if (!hasEntries) {
        state.analysis = null;
        renderAssetTargets(null);
        $('lastUpdate').innerText = 'Posições, proventos e rentabilidade comparada ao CDI e ao BOVA11';
        return;
    }

    $('refreshBtn').disabled = true;
    $('lastUpdate').innerText = 'Carregando cotações...';
    busy.text('Carregando cotações e calculando a carteira...');
    state.receivables = null;
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
    renderTaxAlert($('taxAlert'), a.taxAlert);
    renderTaxAlert($('darfAlert'), a.darfAlert);
    renderAporte();
    renderPatrimony();
    renderChart();
    renderAllocation(a);
    renderPositions(a);
    renderAssetTargets(a);
    renderDividends(null, 'Carregando proventos...');
    $('receivableTotals').innerText = '';
    $('receivablesBody').innerHTML = `<tr><td colspan="8" class="empty-row">Carregando proventos anunciados...</td></tr>`;
    revealItems($('receivablesBody'));
    busy();
    // Depois da análise, que o servidor reaproveita: os recebidos vêm daqui (dependem das datas de pagamento).
    const result = await apiGet(`/carteira/${portfolio.id}/proventos`).then(r => ({ ok: true, r }), e => ({ ok: false, e }));
    if (run !== state.run) return;
    renderReceivables(result);
}
//#endregion

//#region Sub-abas
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
    if (has && name === 'impostos' && !state.taxes && state.structure?.hasEntries) loadTaxes();
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
//#endregion

//#region Alocação e metas por ativo (Visão geral, Aportes e Configurações)
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
//#endregion

//#region Gráficos e formatação
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

// Cria o gráfico do canvas quando o painel dele aparece na tela (onReveal, em animacao.js), para
// a animação de entrada acontecer à vista. Um novo pedido para o mesmo canvas antes disso
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
//#endregion

//#region Mensagens e avisos
function setMsg(id, text, kind) {
    $(id).innerText = text || '';
    $(id).className = `form-msg ${kind || ''}`;
}

// Confirmação de um lançamento salvo, com atalho para a Visão geral (a aba não muda sozinha).
function setSavedMsg(id, text) {
    setMsg(id, text, 'ok');
    $(id).insertAdjacentHTML('beforeend', ' <a href="#visao" class="inline-link">Ver na visão geral</a>');
}

// Aviso de imposto (tax_alert.py), na Visão geral e na Nova operação: o título e as frases vêm
// prontos do servidor; aqui só a cor pelo nível (near = perto do limite, over = acima, info =
// venda sem isenção).
function renderTaxAlert(el, alert) {
    if (!alert || !alert.show) { el.hidden = true; el.innerHTML = ''; return; }
    el.className = `tax-alert tax-${alert.level}`;
    const link = alert.link ? `<p><a href="${escapeHTML(alert.link.href)}">${escapeHTML(alert.link.text)}</a></p>` : '';
    el.innerHTML = `<strong>${escapeHTML(alert.title)}</strong>${(alert.lines || []).map(l => `<p>${escapeHTML(l)}</p>`).join('')}${link}`;
    el.hidden = false;
}

function showError(text) { $('errorBanner').innerText = text; $('errorBanner').style.display = 'block'; }
function hideError() { $('errorBanner').style.display = 'none'; }
//#endregion
