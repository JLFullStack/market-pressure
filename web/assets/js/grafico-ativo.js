// Card flutuante "Operações no gráfico": o gráfico de um ativo (candles ou linha, MM20/MM50/MM200)
// com as compras (C) e vendas (V) da carteira, os alvos das compras abertas e a previsão do
// cruzamento das médias. Usado pela Carteira (Posições, Favoritos, ativos já vendidos) e pelo
// Screener (linhas do ranking). Tudo vem pronto do Python (portfolio_service.asset_chart); aqui só
// o desenho. Precisa de core.js, animacao.js, graficos.js e do Lightweight Charts.

//#region Estado e favoritos
const assetCard = {
    data: null,        // resposta do servidor do ativo aberto
    price: null,       // { chart, candles, line } (priceChart)
    lines: [],         // linhas de alvo desenhadas
    shown: new Set(),  // índices dos alvos visíveis
    seq: 0,            // descarta respostas de um ativo que já foi trocado
    type: 'candles',   // candles | line (CHART_KEY, o mesmo do Backtest)
    opts: {},          // as opções de openAssetChart
    back: null         // elemento que recebe o foco quando o card fecha
};

const STAR_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.25 4.1 1 5.8L12 16.9l-5.25 2.7 1-5.8L3.5 9.7l5.9-.9z"/></svg>';

// Estrela de favorito (aria-pressed = está na lista); o clique é tratado por toggleFavorite.
const favoriteStar = (ticker, market, on) => `<button type="button" class="fav-star" data-fav="${escapeHTML(ticker)}" data-market="${escapeHTML(market)}"
    aria-pressed="${on}" title="${on ? 'Tirar dos favoritos' : 'Adicionar aos favoritos'}" aria-label="${on ? 'Tirar' : 'Adicionar'} ${escapeHTML(ticker)} ${on ? 'dos' : 'aos'} favoritos">${STAR_SVG}</button>`;

// Liga ou desliga um favorito (POST /api/favoritos) e atualiza as estrelas dele na página.
// Retorna a lista nova ({ticker, market}[]) ou null quando falhou (a mensagem vai em onError).
async function toggleFavorite(btn, onError = (m) => alertDialog('Favoritos', m)) {
    const ticker = btn.dataset.fav, market = btn.dataset.market || 'b3';
    const on = btn.getAttribute('aria-pressed') === 'true';
    btn.disabled = true;
    try {
        const r = await apiPost('/favoritos', { op: on ? 'remove' : 'add', ticker, market });
        for (const b of document.querySelectorAll(`.fav-star[data-fav="${CSS.escape(ticker)}"][data-market="${CSS.escape(market)}"]`)) {
            b.setAttribute('aria-pressed', String(!on));
            b.title = !on ? 'Tirar dos favoritos' : 'Adicionar aos favoritos';
        }
        return r.list;
    } catch (e) {
        onError(e.message);
        return null;
    } finally {
        btn.disabled = false;
    }
}
//#endregion

//#region Card
const moneyIn = (v, currency) => currency === 'R$' ? fmtBRL(v) : `${currency} ${fmtNum(v, 2)}`;
// Helpers próprios (as páginas têm o $ e o fmtQty delas; scripts clássicos não podem repetir o nome).
const byId = (id) => document.getElementById(id);
const qtyText = (q) => Number.isInteger(q) ? fmtNum(q, 0) : q.toLocaleString('pt-BR', { maximumFractionDigits: 8 });

// Cria o diálogo na primeira vez (o mesmo nas duas páginas) e liga os botões dele.
function assetDialog() {
    let dlg = byId('assetChartDialog');
    if (dlg) return dlg;
    const help = 'Preço em que cada compra ainda aberta atinge o alvo da venda parcial da regra da classe dela (Configurações). Alvos a menos de 1,5% um do outro aparecem juntos, num nível só (o preço é a média pela quantidade). Alvo já atingido cuja parte ainda não foi vendida aparece com a marca ATINGIDO (linha pontilhada). Para o gráfico não ficar carregado, só o próximo alvo ainda não atingido aparece de início: toque num alvo para mostrá-lo ou escondê-lo.';
    document.body.insertAdjacentHTML('beforeend', `
<dialog class="chart-dialog" id="assetChartDialog" aria-labelledby="assetChartTitle">
    <div class="panel-head">
        <div class="result-head"><span class="avatar solo chart-dialog-avatar" id="assetChartAvatar" aria-hidden="true"></span><div>
            <div class="eyebrow chart-dialog-eyebrow" id="assetChartEyebrow">Operações no gráfico</div>
            <h2 id="assetChartTitle">Operações no gráfico</h2>
            <div class="small muted" id="assetChartInfo"></div>
        </div></div>
        <div class="chart-dialog-tools">
            <button type="button" class="icon-btn chart-dialog-close" id="assetChartClose" title="Fechar" aria-label="Fechar o gráfico"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
        </div>
    </div>
    <div class="chart-dialog-stats" id="assetChartStats"></div>
    <div class="chart-toolbar">
        <div class="bt-legend" id="assetChartLegend"></div>
        <div class="seg" role="group" aria-label="Tipo de gráfico" id="assetChartType">
            <button type="button" data-type="candles" aria-pressed="true">Candles</button>
            <button type="button" data-type="line" aria-pressed="false">Linha</button>
        </div>
    </div>
    <div class="bt-chart" id="assetChartBox" role="img" aria-label="Gráfico de preço do ativo com as compras, as vendas e os alvos das compras abertas"></div>
    <div class="price-targets" id="assetTargets" hidden>
        <div class="price-target-head">
            <span class="small"><strong>Alvos das compras abertas</strong> ${tip(help)}</span>
            <div class="seg" role="group" aria-label="Alvos no gráfico" id="assetTargetMode">
                <button type="button" data-mode="next" aria-pressed="true">Próximo</button>
                <button type="button" data-mode="all" aria-pressed="false">Todos</button>
                <button type="button" data-mode="none" aria-pressed="false">Nenhum</button>
            </div>
        </div>
        <div class="price-target-chips" id="assetTargetChips"></div>
    </div>
    <p class="crossing-note" id="assetCrossing" hidden></p>
    <p class="small muted" id="assetChartMsg" role="status"></p>
</dialog>`);
    dlg = byId('assetChartDialog');
    try { assetCard.type = localStorage.getItem(CHART_KEY) === 'line' ? 'line' : 'candles'; } catch (e) { /* sem preferência */ }
    byId('assetChartClose').addEventListener('click', () => dlg.close());
    // Toque fora do card (no fundo escurecido) fecha.
    dlg.addEventListener('click', (ev) => {
        const r = dlg.getBoundingClientRect();
        if (ev.target === dlg && (ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom)) dlg.close();
    });
    dlg.addEventListener('close', () => {
        assetCard.seq++;
        assetCard.price?.chart.remove();
        assetCard.price = null;
        assetCard.lines = [];
        const tipBox = byId('tipBox');
        if (tipBox && tipBox.parentElement === dlg) { tipBox.hidden = true; document.body.appendChild(tipBox); }
        assetCard.back?.focus?.();
    });
    assetTypeButtons();
    byId('assetChartType').addEventListener('click', (ev) => {
        const type = ev.target.dataset?.type;
        if (!type || type === assetCard.type) return;
        assetCard.type = type;
        try { localStorage.setItem(CHART_KEY, type); } catch (e) { /* preferência não salva */ }
        assetTypeButtons();
        if (assetCard.price) { showPriceType(assetCard.price, assetCard.data.markers, type); drawTargets(); }
    });
    byId('assetTargetMode').addEventListener('click', (ev) => {
        const mode = ev.target.dataset?.mode;
        if (!mode || !assetCard.data) return;
        const all = assetCard.data.targets.map((t, k) => k);
        assetCard.shown = new Set(mode === 'all' ? all : mode === 'next' ? nextTarget() : []);
        drawTargets();
    });
    byId('assetTargetChips').addEventListener('click', (ev) => {
        const chip = ev.target.closest?.('button[data-k]');
        if (!chip) return;
        const k = Number(chip.dataset.k);
        if (assetCard.shown.has(k)) assetCard.shown.delete(k); else assetCard.shown.add(k);
        drawTargets();
    });
    return dlg;
}

function assetTypeButtons() {
    for (const b of byId('assetChartType').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.type === assetCard.type));
}

// Liga uma tabela às linhas que abrem o card: tr[data-ticker] (com data-market) por clique, Enter ou
// espaço. open(ticker, market, row) abre o card com as opções da página. Links, botões e ajudas
// da linha seguem com a ação deles.
function bindChartRows(container, open) {
    const go = (ev) => {
        if (ev.target.closest?.('a, button, .tip, input, select')) return;
        const row = ev.target.closest?.('tr[data-ticker]');
        if (row) open(row.dataset.ticker, row.dataset.market || 'b3', row);
    };
    container.addEventListener('click', go);
    container.addEventListener('keydown', (ev) => {
        if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches?.('tr[data-ticker]')) { ev.preventDefault(); go(ev); }
    });
}

// Abre o card de um ativo. opts: url (a rota que devolve o gráfico), colorFor(cls) (cor da classe),
// position ({avgPrice, quantity, returnPct, profit} da posição aberta), status ({label, reason,
// badge}: a Situação ou a Indicação), back (elemento que recebe o foco ao fechar).
async function openAssetChart(ticker, opts = {}) {
    const dlg = assetDialog();
    const seq = ++assetCard.seq;
    assetCard.opts = opts;
    assetCard.back = opts.back || document.activeElement;
    assetCard.price?.chart.remove();
    assetCard.price = null;
    assetCard.data = null;
    byId('assetChartTitle').innerText = ticker;
    byId('assetChartEyebrow').innerText = 'Operações no gráfico';
    byId('assetChartInfo').innerHTML = '';
    byId('assetChartStats').innerHTML = '';
    byId('assetChartAvatar').textContent = ticker.slice(0, 2).toUpperCase();
    byId('assetChartLegend').innerHTML = '';
    byId('assetTargets').hidden = true;
    byId('assetCrossing').hidden = true;
    byId('assetChartMsg').innerText = `Carregando ${ticker}...`;
    if (!dlg.open) dlg.showModal();
    // A caixa de ajuda flutuante precisa estar dentro do card (camada de cima do diálogo).
    if (byId('tipBox')) dlg.appendChild(byId('tipBox'));
    let data;
    try {
        data = await apiGet(opts.url);
    } catch (e) {
        if (seq === assetCard.seq) byId('assetChartMsg').innerText = `Não foi possível carregar o gráfico de ${ticker} (${e.message}).`;
        return;
    }
    if (seq !== assetCard.seq || !dlg.open) return;
    assetCard.data = data;
    assetCard.shown = new Set(nextTarget());
    assetCard.lines = [];
    const color = (data.cls && opts.colorFor?.(data.cls)) || cssVar('--accent');
    byId('assetChartAvatar').style.setProperty('--a1', color);
    dlg.style.setProperty('--g', color);
    byId('assetChartEyebrow').innerText = data.held ? 'Operações no gráfico' : 'Gráfico do ativo';
    const rule = data.rule.fraction > 0 ? `${data.ruleLabel} (${fmtNum(data.rule.fraction, 0)}% em +${fmtNum(data.rule.target, 1)}%)` : data.ruleLabel;
    // Informações em pílulas: nome, classe (na cor dela), lançamentos e compras abertas, regra de venda.
    byId('assetChartInfo').innerHTML = `<span class="chips">${[
        data.name && data.name !== data.ticker ? `<span class="chip">${escapeHTML(data.name)}</span>` : '',
        data.cls ? `<span class="chip chip-class"><span class="swatch-box" style="background:${color}"></span>${escapeHTML(data.cls)}</span>` : '',
        data.held ? `<span class="chip"><strong>${data.transactions}</strong> lançamento(s) · <strong>${data.openLots}</strong> compra(s) aberta(s)</span>`
            : '<span class="chip">sem operações nesta carteira</span>',
        `<span class="chip">${escapeHTML(rule)}</span>`].join('')}</span>`;
    renderAssetStats(data);
    const projected = data.crossing?.projection?.length > 0;
    byId('assetChartLegend').innerHTML = priceLegend([
        ...(data.held ? [[COLORS.buy, 'C = compra'], [COLORS.exit, 'V = venda']] : []),
        ...(data.targets.length ? [[COLORS.target, 'Alvo da venda parcial']] : []),
        ...(projected ? [[COLORS.mm50, 'Médias projetadas (tracejado)']] : [])]);
    byId('assetChartMsg').innerText = data.note || '';
    assetCard.price = priceChart(byId('assetChartBox'), data);
    showPriceType(assetCard.price, data.markers, assetCard.type);
    drawCrossing(data);
    // Com a projeção das médias, o zoom inicial vai até o encontro projetado.
    const range = data.range && projected ? { ...data.range, to: data.crossing.projection.at(-1).time } : data.range;
    if (range) assetCard.price.chart.timeScale().setVisibleRange(range);
    else assetCard.price.chart.timeScale().fitContent();
    drawTargets();
    keepTipsWithText(byId('assetTargets'));
    // Entrada: o gráfico se desenha da esquerda para a direita a cada ativo aberto.
    const box = byId('assetChartBox');
    box.classList.remove('drawing'); void box.offsetWidth; box.classList.add('drawing');
}

// Faixa de números do card: preço de agora, a posição aberta (preço médio e resultado), o
// resultado de uma posição já encerrada, o próximo alvo e a Situação ou a Indicação. Os valores
// em reais e % contam ao abrir.
function renderAssetStats(data) {
    const { position: p, status } = assetCard.opts;
    const next = data.nextTarget === null || data.nextTarget === undefined ? null : data.targets[data.nextTarget];
    const stat = (label, value, note = '', tone = '') => `<div class="dlg-stat ${tone}"><span class="kpi-label">${label}</span>
        <span class="dlg-stat-value num">${value}</span>${note ? `<span class="kpi-note">${note}</span>` : ''}</div>`;
    const counter = (v, f, d = 2) => `<span data-count="${v}" data-format="${f}" data-decimals="${d}">${COUNT_FORMAT[f](0, d)}</span>`;
    const cards = [stat('Preço atual', moneyIn(data.lastClose, data.currency), data.currency === 'R$' ? 'último fechamento' : `em ${data.currency}`, 'tone-blue')];
    if (p) {
        cards.push(stat('Preço médio', counter(p.avgPrice, 'brl'), `${qtyText(p.quantity)} ${p.quantity === 1 ? 'cota' : 'cotas'}`, 'tone-cyan'));
        cards.push(stat('Resultado', p.returnPct === null ? '—' : counter(p.returnPct, 'pct', 2), `lucro ${fmtBRL(p.profit)}`,
            p.returnPct === null ? 'tone-violet' : p.returnPct >= 0 ? 'tone-green' : 'tone-red'));
    } else if (data.closed) {
        const c = data.closed;
        cards.push(stat('Resultado (encerrada)', c.returnPct === null ? '—' : counter(c.returnPct, 'pct', 2),
            `lucro ${fmtBRL(c.profit)} · última venda em ${fmtDate(c.lastDate)}`,
            c.returnPct === null ? 'tone-violet' : c.returnPct >= 0 ? 'tone-green' : 'tone-red'));
    }
    if (next) cards.push(stat(next.reached ? 'Alvo atingido' : 'Próximo alvo', moneyIn(next.price, data.currency),
        next.reached ? 'parte ainda não vendida' : next.distance === null ? '' : `${fmtPct(next.distance, 1)} do preço atual`, 'tone-amber'));
    if (status?.label) cards.push(`<div class="dlg-stat tone-violet"><span class="kpi-label">${escapeHTML(status.title || 'Situação')}</span>
        <span class="dlg-stat-badge">${signalBadge(status.label, status.reason || '', status.badge || 'badge-low')}</span></div>`);
    byId('assetChartStats').innerHTML = cards.join('');
    byId('assetChartStats').querySelectorAll('[data-count]').forEach(countUp);
}

// Previsão do cruzamento da MM50 abaixo da MM200 (o servidor só a manda com o fechamento abaixo
// da MM200 e a MM50 ainda acima): as duas médias projetadas, tracejadas, até o encontro, e o rótulo
// com a probabilidade, o prazo e o acerto do modelo. Tudo vem pronto do Python.
function drawCrossing(d) {
    const c = d.crossing;
    byId('assetCrossing').hidden = !c;
    if (!c) return;
    for (const k of ['mm50', 'mm200']) {
        if (!c.projection.length) break;
        const s = assetCard.price.chart.addLineSeries({ color: hexAlpha(COLORS[k], 0.85), lineWidth: k === 'mm200' ? 2 : 1,
            lineStyle: LightweightCharts.LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        s.setData(c.projection.map(p => ({ time: p.time, value: p[k] })));
    }
    byId('assetCrossing').innerHTML = `<span class="crossing-label"><strong>${escapeHTML(c.label)}</strong>${c.date ? ` · encontro perto de ${fmtDate(c.date)}` : ''}
        ${tip(c.text)}</span><span class="small muted">${escapeHTML(c.accuracy || '')}</span>`;
}

// O alvo mostrado de início ("Próximo"): o mais próximo ainda não atingido, escolhido no servidor.
const nextTarget = () => assetCard.data?.nextTarget === null || assetCard.data?.nextTarget === undefined ? [] : [assetCard.data.nextTarget];

// Linhas de alvo (tracejadas, na série visível) e os chips. Com mais de 3 alvos à mostra, só o
// próximo leva o rótulo na linha; os outros ficam só com o preço no eixo. Alvo já atingido (parte
// ainda não vendida): linha pontilhada e mais apagada.
function drawTargets() {
    const d = assetCard.data;
    if (!d || !assetCard.price) return;
    for (const { series, line } of assetCard.lines) series.removePriceLine(line);
    assetCard.lines = [];
    const series = assetCard.type === 'candles' ? assetCard.price.candles : assetCard.price.line;
    const shown = [...assetCard.shown].sort((a, b) => a - b);
    const main = shown.includes(d.nextTarget) ? d.nextTarget : shown[0];
    shown.forEach((k) => {
        const t = d.targets[k];
        const label = k === main || shown.length <= 3;
        const line = series.createPriceLine({
            price: t.price, color: hexAlpha(COLORS.target, t.reached ? 0.45 : k === main ? 0.9 : 0.55), lineWidth: 1,
            lineStyle: t.reached ? LightweightCharts.LineStyle.Dotted : LightweightCharts.LineStyle.Dashed, axisLabelVisible: true,
            title: label ? `${t.reached ? 'Alvo atingido' : 'Alvo'}${t.lots.length > 1 ? ` (${t.lots.length})` : ''}` : ''
        });
        assetCard.lines.push({ series, line });
    });
    byId('assetTargets').hidden = !d.targets.length;
    const count = d.targets.length;
    const mode = shown.length === 0 ? 'none' : shown.length === count && count > 1 ? 'all' : shown.length === 1 && shown[0] === d.nextTarget ? 'next' : '';
    for (const b of byId('assetTargetMode').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
    byId('assetTargetMode').querySelector('[data-mode="all"]').hidden = count < 2;
    byId('assetTargetChips').innerHTML = d.targets.map((t, k) => `
        <button type="button" class="price-target${t.reached ? ' reached' : ''}" data-k="${k}" aria-pressed="${assetCard.shown.has(k)}"
            data-tip-title="Alvo em ${escapeHTML(moneyIn(t.price, d.currency))}" data-tip="${escapeHTML(t.detail)}">
            <strong>${moneyIn(t.price, d.currency)}</strong>
            ${t.reached ? '<span class="price-target-tag">atingido</span>' : `<span class="pos">${t.distance === null ? '' : fmtPct(t.distance, 1)}</span>`}
            <span class="muted">${t.lots.length} compra${t.lots.length > 1 ? 's' : ''}</span>
        </button>`).join('');
}
//#endregion
