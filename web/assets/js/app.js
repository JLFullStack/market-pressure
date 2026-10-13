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
    try {
        let status = await apiGet(`/scan${force ? '?force=1' : ''}`);
        const waited = status.state === 'running';
        while (status.state === 'running') {
            if (status.total) {
                setProgress(status.done / status.total);
                setStatus(`Escaneando ${status.done}/${status.total}`, '');
            } else {
                setStatus("Carregando universo...", '');
            }
            await sleep(POLL_MS);
            status = await apiGet('/scan');
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
    const result = view[strict ? 'strict' : 'loose'];
    const top = result.top;

    const refDate = fmtDate(view.refDate);
    const liquidity = `R$ ${fmtNum(scan.minAvgVolume / 1e6, 0)} mi por pregão`;
    const basis = mode === 'fechamento'
        ? `Base: fechamento de ${refDate}`
        : scan.liveSession
            ? `Base: intradiário de ${refDate} (pregão em andamento; dados até ${new Date(scan.timestamp).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })})`
            : `Base: intradiário — sem pregão em andamento, usando o fechamento de ${refDate}`;
    document.getElementById('lastUpdate').innerText =
        `${basis} · varredura de ${new Date(scan.timestamp).toLocaleString('pt-BR')} · universo de ${scan.universeSize} ações da B3 com volume médio acima de ${liquidity}`;
    document.getElementById('fnUniverse').innerText = scan.universeSize;
    document.getElementById('fnLiquidity').innerText = liquidity;

    const info = document.getElementById('infoBanner');
    info.style.display = top.length < scan.topN ? "block" : "none";
    info.innerText = `Apenas ${top.length} ativo(s) atendem a todos os critérios hoje.` +
        (strict ? ' Desmarque "Exigir candle de rejeição" para ampliar a lista.' : '');

    renderKpis(scan, view, result);
    renderTable(top);
    renderBova(mode);
}

const REGIME_TEXT = {
    alta: 'tendência de alta disseminada — pullbacks tendem a ser compras de oportunidade',
    misto: 'regime misto e seletivo — prefira os setups de maior score composto',
    defensivo: 'regime defensivo — poucas ações em tendência primária de alta; reduza exposição'
};

function renderKpis(scan, view, result) {
    const top = result.top;
    document.getElementById('kpiScanned').innerText = view.scanned;
    document.getElementById('kpiScannedNote').innerText = scan.failures
        ? `${scan.failures} sem dados suficientes` : 'todos com histórico completo';
    document.getElementById('kpiQualified').innerText = result.qualified;
    document.getElementById('kpiPressure').innerText = top.length ? `${fmtNum(result.avgPressure)}%` : '—';
    document.getElementById('kpiWinRate').innerText = top.length ? `${fmtNum(result.avgWinRate)}%` : '—';
    document.getElementById('kpiBest').innerText = top.length ? top[0].ticker : '—';
    document.getElementById('kpiBestNote').innerText = top.length ? `SC ${fmtNum(top[0].score)}` : '';
    document.getElementById('regimeText').innerHTML =
        `<strong>Status do regime:</strong> ${fmtNum(view.breadth, 0)}% do universo está em tendência primária de alta (preço, MM20 e MM50 acima de uma MM200 ascendente) — ${REGIME_TEXT[view.regime]}.`;
}

const BOTTLE_BADGE = {
    cheia: `<span class="badge badge-hot">🔥 Cheia</span>`,
    quase: `<span class="badge badge-fill">💧 Q. Cheia</span>`,
    enchendo: `<span class="badge badge-low">Enchendo</span>`
};

function renderTable(top) {
    const tableBody = document.getElementById('rankingTableBody');
    if (top.length === 0) {
        tableBody.innerHTML = `<tr><td colspan="9" class="empty-row">Nenhum ativo atende a todos os critérios hoje.</td></tr>`;
        return;
    }

    const medals = ['🥇', '🥈', '🥉'];
    tableBody.innerHTML = top.map((a, index) => `
        <tr>
            <td class="c nowrap" style="font-weight: bold;">${medals[index] || ''} ${index + 1}º</td>
            <td><a href="https://br.tradingview.com/chart/?symbol=BMFBOVESPA:${a.ticker}" target="_blank" rel="noopener" class="ticker-link">${a.ticker}</a>
                <div class="small muted">${escapeHTML(a.sector || '')}</div></td>
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
