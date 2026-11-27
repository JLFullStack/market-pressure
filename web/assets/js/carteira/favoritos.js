// Carteira → Favoritos: a lista de observação (a situação de cada ativo vem pronta do Python,
// carteira/favoritos.py) e os ativos que já passaram pela carteira. Toda linha abre o card
// "Operações no gráfico" (grafico-ativo.js). Usa carteira.js.

//#region Constantes e eventos
// Situação do favorito (favoritos.row: status.key) → badge.
const FAV_BADGE = {
    comprar: 'badge-ok', setup: 'badge-ok', aguardar: 'badge-fill', perto: 'badge-info', alta: 'badge-low',
    travado: 'badge-hot', fora: 'badge-low', curto: 'badge-low', sem: 'badge-low'
};

// Lista de favoritos recebida do servidor ({ticker, market}[]) → a ★ das Posições.
function setFavorites(list) {
    state.favSet = new Set(list.map(f => `${f.ticker}|${f.market}`));
}

// Estrela de uma tabela (Posições ou Favoritos): liga/desliga e marca a sub-aba para recarregar.
async function onFavoriteStar(star, onError) {
    const list = await toggleFavorite(star, onError);
    if (!list) return null;
    setFavorites(list);
    state.favorites = null;   // a sub-aba Favoritos pede a situação de novo ao abrir
    return list;
}

function setupFavoritos() {
    $('favForm').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const ticker = $('favTicker').value.trim();
        if (!ticker) { $('favTicker').focus(); return; }
        const msg = $('favMsg');
        $('favAddBtn').disabled = true;
        msg.className = 'small';
        msg.innerText = `Adicionando ${ticker.toUpperCase()}...`;
        try {
            const r = await apiPost('/favoritos', { op: 'add', ticker, market: $('favMarket').value });
            msg.className = 'small pos';
            msg.innerText = r.message;
            $('favTicker').value = '';
            loadFavorites();
        } catch (e) {
            msg.className = 'small neg';
            msg.innerText = e.message;
        } finally {
            $('favAddBtn').disabled = false;
        }
    });
    // Estrela: tira (ou devolve) o favorito e recarrega a lista.
    $('panel-favoritos').addEventListener('click', async (ev) => {
        const star = ev.target.closest?.('.fav-star');
        if (!star) return;
        const list = await onFavoriteStar(star, (m) => { $('favMsg').className = 'small neg'; $('favMsg').innerText = m; });
        if (list) loadFavorites();
    });
    const open = (ticker, market, row) => {
        const f = (state.favorites?.items || []).find(x => x.ticker === ticker && x.market === market);
        const p = (state.analysis?.open || []).find(x => x.ticker === ticker);
        // Ativo com posição: a Situação da carteira (a mesma das Posições: vender, manter, comprar);
        // sem posição, a situação nos favoritos (se vale comprar).
        const status = p?.status ? { label: p.status, reason: p.statusReason, badge: STATUS_BADGE[p.statusKey] || 'badge-low' }
            : f ? { title: 'Situação nos favoritos', label: f.status.label, reason: f.status.reason, badge: FAV_BADGE[f.status.key] } : null;
        const d = state.favorites;
        const pressure = f?.pressure != null ? { value: f.pressure, state: f.bottle, parts: f.pressureParts, levels: d?.bottleLevels, states: d?.bottleStates } : null;
        openCarteiraChart(ticker, market, row, { position: p, status, pressure, noPressure: d?.noPressureText });
    };
    bindChartRows($('favBody'), open);
    bindChartRows($('closedBody'), open);
}
//#endregion

//#region Lista
async function loadFavorites() {
    const seq = ++state.favoritesSeq;
    $('favBody').innerHTML = '<tr><td colspan="7" class="empty-row">Carregando favoritos...</td></tr>';
    $('closedBody').innerHTML = '<tr><td colspan="6" class="empty-row">Carregando...</td></tr>';
    let data;
    try {
        data = await apiGet(`/favoritos?carteira=${encodeURIComponent(state.currentId || '')}`);
    } catch (e) {
        if (seq !== state.favoritesSeq) return;
        $('favBody').innerHTML = `<tr><td colspan="7" class="empty-row">Não foi possível carregar os favoritos (${escapeHTML(e.message)}).</td></tr>`;
        $('closedBody').innerHTML = '';
        return;
    }
    if (seq !== state.favoritesSeq) return;
    state.favorites = data;
    setFavorites(data.items);
    renderFavorites(data);
}

function renderFavorites(d) {
    const dist = (v) => v === null || v === undefined ? '—' : `<span class="${signClass(v)}">${fmtPct(v, 1)}</span>`;
    $('favCount').innerText = `${d.items.length} de ${d.max}`;
    $('favBody').innerHTML = d.items.length ? d.items.map(f => `
        <tr class="chart-row" tabindex="0" data-reveal data-ticker="${escapeHTML(f.ticker)}" data-market="${f.market}" aria-label="Abrir o gráfico de ${escapeHTML(f.ticker)}">
            <td><div class="asset"><span class="avatar solo" aria-hidden="true">${escapeHTML(f.ticker.slice(0, 2))}</span><div class="asset-text">
                <strong>${escapeHTML(f.ticker)}</strong>${f.held ? ' <span class="badge badge-info">na carteira</span>' : f.traded ? ' <span class="badge badge-low">já teve</span>' : ''}
                <div class="small muted">${escapeHTML(f.name !== f.ticker ? f.name : '')}${f.market === 'b3' ? '' : ` · ${MARKETS[f.market].label}`}</div></div></div></td>
            <td class="r nowrap">${f.price === undefined ? '—' : fmtMoney(f.price, f.currency)}${f.date ? `<div class="small muted">${fmtDate(f.date)}</div>` : ''}</td>
            <td class="r nowrap">${f.change === undefined || f.change === null ? '—' : `<span class="delta-pill ${signClass(f.change)}">${fmtPct(f.change, 2)}</span>`}</td>
            <td class="r nowrap small">${f.mas ? `MM20 ${dist(f.mas.mm20)} · MM50 ${dist(f.mas.mm50)} · MM200 ${dist(f.mas.mm200)}` : '—'}</td>
            <td class="c nowrap">${f.rank ? `${f.rank}º<div class="small muted">Score ${fmtNum(f.score, 0)}</div>` : '<span class="small muted">fora</span>'}
                ${f.pressure != null ? `<div class="fav-bottle">${bottleIcon(f.pressure, f.bottle, d.bottleLevels)}</div>` : ''}</td>
            <td class="c">${signalBadge(f.status.label, f.status.reason, FAV_BADGE[f.status.key] || 'badge-low')}</td>
            <td class="c">${favoriteStar(f.ticker, f.market, true)}</td>
        </tr>`).join('')
        : '<tr><td colspan="7" class="empty-row">Nenhum favorito ainda. Adicione um código acima ou toque na ★ de um ativo no ranking do Screener.</td></tr>';
    revealItems($('favBody'));
    const fav = new Set(d.items.map(f => `${f.ticker}|${f.market}`));
    $('closedBody').innerHTML = d.closed.length ? d.closed.map(c => `
        <tr class="chart-row" tabindex="0" data-reveal data-ticker="${escapeHTML(c.ticker)}" data-market="${c.market}" aria-label="Abrir o gráfico de ${escapeHTML(c.ticker)} com as compras e vendas">
            <td><strong>${escapeHTML(c.ticker)}</strong></td>
            <td class="nowrap">${fmtDate(c.first)} a ${fmtDate(c.last)}</td>
            <td class="r">${c.operations}</td>
            <td class="r nowrap ${signClass(c.profit)}">${fmtBRL(c.profit)}</td>
            <td class="r nowrap">${c.returnPct === null ? '—' : `<span class="delta-pill ${signClass(c.returnPct)}">${fmtPct(c.returnPct, 1)}</span>`}</td>
            <td class="c">${favoriteStar(c.ticker, c.market, fav.has(`${c.ticker}|${c.market}`))}</td>
        </tr>`).join('')
        : '<tr><td colspan="6" class="empty-row">Nenhum ativo vendido por inteiro nesta carteira.</td></tr>';
    revealItems($('closedBody'));
}
//#endregion
