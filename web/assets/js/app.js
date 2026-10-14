// Aba Screener. Depende de core.js (apiGet, formatadores), carregado antes deste script.
// A varredura, os indicadores, o setup, a pressão, o backtest e o ranking são feitos pelo
// servidor (screener.py); a página pede o resultado, acompanha o progresso e exibe a visão
// escolhida (base × candle de rejeição) sem buscar de novo.

const POLL_MS = 700; // intervalo entre as consultas de progresso da varredura
let lastScan = null;

document.addEventListener("DOMContentLoaded", () => {
    const modeSelect = document.getElementById('baseMode');
    try { modeSelect.value = localStorage.getItem(MODE_KEY) || 'fechamento'; } catch (e) { /* sem preferência salva */ }
    modeSelect.addEventListener('change', () => {
        try { localStorage.setItem(MODE_KEY, modeSelect.value); } catch (e) { /* sem preferência salva */ }
        lastScan && render(lastScan);
    });
    document.getElementById('requireCandle').addEventListener('change', () => lastScan && render(lastScan));
    // Liquidez mínima: o servidor refaz o ranking com o piso escolhido, sem varrer de novo.
    const liquidity = document.getElementById('liquidity');
    try { liquidity.value = localStorage.getItem(LIQUIDITY_KEY) || 'padrao'; } catch (e) { /* sem preferência salva */ }
    if (!liquidity.value) liquidity.value = 'padrao';
    liquidity.addEventListener('change', () => {
        try { localStorage.setItem(LIQUIDITY_KEY, liquidity.value); } catch (e) { /* preferência não salva */ }
        runScan(false);
    });
    runScan(false);
});

// ---------------------------------------------------------------- Varredura

// Pede a varredura ao servidor: se houver uma recente, ela volta na hora; senão o servidor
// começa uma em segundo plano e a página consulta o progresso até terminar.
async function runScan(force) {
    const btn = document.getElementById('refreshBtn');
    const banner = document.getElementById('errorBanner');
    banner.style.display = "none";
    btn.disabled = true;
    setStatus(force ? "Iniciando varredura..." : "Carregando...", '');
    const busy = busyStart(force ? "Iniciando varredura..." : "Carregando ranking...");
    try {
        const liquidity = document.getElementById('liquidity').value;
        let status = await apiGet(`/scan?liquidez=${liquidity}${force ? '&force=1' : ''}`);
        const waited = status.state === 'running';
        while (status.state === 'running') {
            if (status.total) {
                setProgress(status.done / status.total);
                setStatus(`Escaneando ${status.done}/${status.total}`, '');
                busy.text(`Escaneando ativos: ${status.done} de ${status.total}`);
            } else {
                setStatus("Carregando universo...", '');
                busy.text("Carregando a lista de ativos...");
            }
            await sleep(POLL_MS);
            status = await apiGet(`/scan?liquidez=${liquidity}`);
        }
        if (status.state === 'error') {
            banner.innerText = `Aviso: não foi possível concluir a varredura (${status.message}).`;
            banner.style.display = "block";
            setStatus("Falha na API", 'warn');
            if (status.scan) {
                banner.innerText += ` Exibindo a última varredura salva (${new Date(status.scan.timestamp).toLocaleString('pt-BR')}).`;
                lastScan = status.scan;
                render(status.scan);
            }
            return;
        }
        lastScan = status.scan;
        if (!waited) setStatus(`Cache (${new Date(lastScan.timestamp).toLocaleTimeString('pt-BR')})`, 'ok');
        else setStatus(lastScan.apiErrors ? "API Parcial" : "API Conectada", lastScan.apiErrors ? 'warn' : 'ok');
        render(lastScan);
    } catch (err) {
        banner.innerText = `Aviso: não foi possível concluir a varredura (${err.message}).`;
        banner.style.display = "block";
        setStatus("Falha na API", 'warn');
    } finally {
        busy();
        btn.disabled = false;
        setProgress(null);
    }
}

function setStatus(text, kind) {
    const el = document.getElementById('modeIndicator');
    el.innerText = text;
    el.className = `status-badge ${kind}`;
}

function setProgress(fraction) {
    const wrap = document.getElementById('progress');
    wrap.style.display = fraction === null ? 'none' : 'block';
    document.getElementById('progressBar').style.width = `${(fraction || 0) * 100}%`;
}

// ---------------------------------------------------------------- Renderização

function render(scan) {
    const strict = document.getElementById('requireCandle').checked;
    const mode = document.getElementById('baseMode').value;
    const view = scan.views[mode];

    const refDate = fmtDate(view.refDate);
    const basis = mode === 'fechamento'
        ? `Base: fechamento de ${refDate}`
        : scan.liveSession
            ? `Base: intradiário de ${refDate} (pregão em andamento; dados até ${new Date(scan.timestamp).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })})`
            : `Base: intradiário — sem pregão em andamento, usando o fechamento de ${refDate}`;
    document.getElementById('lastUpdate').innerText =
        `${basis} · varredura de ${new Date(scan.timestamp).toLocaleString('pt-BR')} · universo de ${scan.universeSize} ativos da B3 acima do piso de liquidez`;
    document.getElementById('fnUniverse').innerText = scan.universeSize;

    renderKpis(scan, view, strict);
    renderRankings(scan, view, strict);
    renderBova(mode);
}

// Tipos de ativo (screener.KINDS): título do painel e nome curto.
const KIND_TITLE = {
    acoes: '🏢 Ações (ON, PN e units)',
    fundos: '🏬 Fundos listados (FII, Fiagro e FI-Infra)',
    etfs: '🧺 ETFs de ações',
    bdrs: '🌎 BDRs',
};
const KIND_SHORT = { acoes: 'ações', fundos: 'fundos', etfs: 'ETFs', bdrs: 'BDRs' };
const KIND_OF = { acoes: 'das ações', fundos: 'dos fundos', etfs: 'dos ETFs', bdrs: 'dos BDRs' };

const REGIME_TEXT = {
    alta: 'tendência de alta disseminada — pullbacks tendem a ser compras de oportunidade',
    misto: 'regime misto e seletivo — prefira os setups de maior score composto',
    defensivo: 'regime defensivo — poucos ativos em tendência primária de alta; reduza exposição'
};

function renderKpis(scan, view, strict) {
    const results = view.rankings.map(r => ({ kind: r.kind, result: r[strict ? 'strict' : 'loose'] }));
    const byKind = (fn) => view.rankings.map((r, k) => `${fn(r, results[k].result)} ${KIND_SHORT[r.kind]}`).join(' · ');
    document.getElementById('kpiScanned').innerText = view.scanned;
    document.getElementById('kpiScannedNote').innerText = byKind(r => r.scanned) +
        (scan.failures ? ` · ${scan.failures} sem dados suficientes` : '');
    document.getElementById('kpiQualified').innerText = results.reduce((sum, r) => sum + r.result.qualified, 0);
    document.getElementById('kpiQualifiedNote').innerText = byKind((r, result) => result.qualified);
    const best = results.filter(r => r.result.top.length)
        .map(r => ({ kind: r.kind, a: r.result.top[0] }))
        .sort((x, y) => y.a.score - x.a.score)[0];
    document.getElementById('kpiBest').innerText = best ? best.a.ticker : '—';
    document.getElementById('kpiBestNote').innerText = best ? `SC ${fmtNum(best.a.score)} · ${KIND_SHORT[best.kind]}` : '';
}

function renderRankings(scan, view, strict) {
    document.getElementById('rankings').innerHTML = view.rankings
        .map(r => rankingPanel(scan, r, r[strict ? 'strict' : 'loose'], strict)).join('');
}

function rankingPanel(scan, r, result, strict) {
    const top = result.top;
    const facts = [
        `${r.scanned} com liquidez (média acima de R$ ${fmtNum(r.minAvgVolume / 1e6, 0)} mi por pregão)`,
        `${result.qualified} no setup`,
        top.length ? `pressão média ${fmtNum(result.avgPressure)}%` : null,
        top.length ? `acerto médio ${fmtNum(result.avgWinRate)}%` : null,
        r.duplicates ? `${r.duplicates} ETF(s) que repetem um índice já listado omitidos` : null,
    ].filter(Boolean).join(' · ');
    const regimeText = r.regime
        ? `<strong>Regime:</strong> ${fmtNum(r.breadth, 0)}% ${KIND_OF[r.kind]} em tendência primária de alta (preço, MM20 e MM50 acima de uma MM200 ascendente) — ${REGIME_TEXT[r.regime]}.`
        : `<strong>Regime:</strong> amostra pequena (menos de 20 ${KIND_SHORT[r.kind]} no universo) — sem leitura de amplitude.`;
    const few = top.length && top.length < scan.topN
        ? `<div class="ranking-note">Apenas ${top.length} ${KIND_SHORT[r.kind]} atendem a todos os critérios hoje.${strict ? ' Desmarque "Exigir candle de rejeição" para ampliar a lista.' : ''}</div>`
        : '';
    return `
        <section class="panel ranking" aria-labelledby="ranking-${r.kind}">
            <div class="ranking-head">
                <h2 id="ranking-${r.kind}">${KIND_TITLE[r.kind]} — Top ${scan.topN}</h2>
                <div class="small muted">${facts}</div>
                <div class="regime">${regimeText}</div>
                ${few}
            </div>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th class="c">Rank</th>
                            <th>Ticker</th>
                            <th class="c">Score Composto</th>
                            <th class="c">Preço Atual</th>
                            <th class="c">Média Testada</th>
                            <th class="c">Pressão %</th>
                            <th class="c">Taxa Acerto %</th>
                            <th class="c">Estado da Garrafa</th>
                            <th>Justificativa Quantitativa (Volume &amp; Momentum)</th>
                        </tr>
                    </thead>
                    <tbody>${tableRows(top, r.kind)}</tbody>
                </table>
            </div>
        </section>`;
}

const BOTTLE_BADGE = {
    cheia: `<span class="badge badge-hot">🔥 Cheia</span>`,
    quase: `<span class="badge badge-fill">💧 Q. Cheia</span>`,
    enchendo: `<span class="badge badge-low">Enchendo</span>`
};

function tableRows(top, kind) {
    if (top.length === 0) {
        return `<tr><td colspan="9" class="empty-row">Nenhum ${kind === 'acoes' ? 'ativo' : 'ativo deste tipo'} atende a todos os critérios hoje.</td></tr>`;
    }
    const medals = ['🥇', '🥈', '🥉'];
    return top.map((a, index) => `
        <tr>
            <td class="c nowrap" style="font-weight: bold;">${medals[index] || ''} ${index + 1}º</td>
            <td><a href="https://br.tradingview.com/chart/?symbol=BMFBOVESPA:${a.ticker}" target="_blank" rel="noopener" class="ticker-link">${a.ticker}</a>
                <a href="backtest.html?ticker=${encodeURIComponent(a.ticker)}" class="inline-link small" aria-label="Backtest de ${a.ticker}">backtest</a>
                <div class="small muted">${escapeHTML(a.sector || a.name || '')}</div></td>
            <td class="c" style="font-weight: bold; color: var(--accent);">${fmtNum(a.score)}</td>
            <td class="c nowrap">${fmtBRL(a.price)}<div class="small" style="color: ${a.change >= 0 ? 'var(--success)' : 'var(--danger)'};">${a.change >= 0 ? '+' : ''}${fmtNum(a.change, 2)}%</div></td>
            <td class="c muted">${a.setup.ma.name}</td>
            <td class="c" style="font-weight: 600;">${fmtNum(a.pressure, 0)}%</td>
            <td class="c nowrap">${fmtNum(a.bt.winRate, 0)}% <span class="small muted">(${a.bt.wins}/${a.bt.trades})${a.bt.avgReturn === null ? '' : ` · méd. ${fmtPct(a.bt.avgReturn)}`}</span></td>
            <td class="c">${BOTTLE_BADGE[a.bottle]}</td>
            <td class="small muted" style="min-width: 320px;">${escapeHTML(a.justification)}</td>
        </tr>
    `).join('');
}

// ---------------------------------------------------------------- KPI do BOVA11

async function renderBova(mode) {
    const value = document.getElementById('kpiBova');
    const note = document.getElementById('kpiBovaNote');
    try {
        const b = await apiGet(`/bova?mode=${mode}`);
        value.innerText = fmtPct(b.change);
        value.style.color = b.change >= 0 ? 'var(--success)' : 'var(--danger)';
        note.innerText = `${fmtBRL(b.close)} · ` + (b.above.length === 3 ? 'acima das três médias'
            : b.above.length === 0 ? 'abaixo das três médias'
            : `acima de ${b.above.join(' e ')}; abaixo de ${b.below.join(' e ')}`);
    } catch (e) {
        value.innerText = '—';
        value.style.color = '';
        note.innerText = `BOVA11 indisponível (${e.message})`;
    }
}
