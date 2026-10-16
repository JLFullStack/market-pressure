// Aba Screener. Depende de core.js (apiGet, formatadores), carregado antes deste script.
// A varredura, os indicadores, o setup, a pressão, o backtest e o ranking são feitos pelo
// servidor (screener.py); a página pede o resultado, acompanha o progresso e exibe a visão
// escolhida (base × candle de rejeição) sem buscar de novo.

const POLL_MS = 700; // intervalo entre as consultas de progresso da varredura
const PORTFOLIO_KEY = "marketpressure_carteira_atual"; // carteira escolhida na aba Carteira
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
    document.getElementById('rankings').addEventListener('click', (e) => {
        const btn = e.target.closest('button.copy-analysis');
        if (btn) copyAnalysis(btn);
    });
    runScan(false);
});

// Carteira da coluna "Indicação" e da análise copiada: a última aberta na aba Carteira.
function currentPortfolio() {
    try { return localStorage.getItem(PORTFOLIO_KEY) || ''; } catch (e) { return ''; }
}

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
        const query = `liquidez=${liquidity}&carteira=${encodeURIComponent(currentPortfolio())}`;
        let status = await apiGet(`/scan?${query}${force ? '&force=1' : ''}`);
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
            status = await apiGet(`/scan?${query}`);
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

const REGIME = {
    alta: { label: 'Regime de alta', badge: 'badge-ok', text: 'Tendência de alta disseminada: pullbacks tendem a ser compras de oportunidade.' },
    misto: { label: 'Regime misto', badge: 'badge-fill', text: 'Mercado seletivo: prefira os setups de maior Score Composto.' },
    defensivo: { label: 'Regime defensivo', badge: 'badge-hot', text: 'Poucos ativos em tendência primária de alta: reduza a exposição.' },
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
    const regime = REGIME[r.regime];
    const regimeText = regime
        ? `<span class="badge ${regime.badge} regime-badge">${regime.label}</span> ${regime.text}
           <span class="muted">${fmtNum(r.breadth, 0)}% ${KIND_OF[r.kind]} em tendência primária de alta</span>
           ${tip('Tendência primária de alta: preço, MM20 e MM50 acima de uma MM200 ascendente. Com 60% ou mais dos ativos do tipo nessa condição, o regime é de alta; de 40% a 60%, misto; abaixo de 40%, defensivo.')}`
        : `<span class="badge badge-low regime-badge">Regime</span> <span class="muted">Amostra pequena (menos de 20 ${KIND_SHORT[r.kind]} no universo): sem leitura de amplitude.</span>`;
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
                            <th class="c">Histórico (backtest) ${tip('A estratégia aplicada aos ~10 anos de histórico do ativo, com a regra de venda configurada para o tipo dele.\nPrimeira linha: taxa de acerto das compras encerradas (suavizada para 50% com poucas), acertos/compras e o retorno médio por compra.\n3 anos: as compras encerradas nos últimos 3 anos. Nas ações, foi o histórico que melhor separou compras boas de ruins.\nAbertas: compras ainda abertas, pelo preço de hoje (num ativo em alta costumam ser as melhores).')}</th>
                            <th class="c">Estado da Garrafa</th>
                            <th class="c">Indicação</th>
                            <th>Justificativa Quantitativa (Volume &amp; Momentum)</th>
                            <th class="c">Análise IA ${tip('Copia um texto pronto para colar numa IA (ChatGPT, Claude, Gemini...): pede a ela o papel de um analista experiente que fala de forma simples e entrega todos os dados do ativo (ranking, pressão, backtest, médias, últimos pregões, mercado, sua estratégia e suas compras abertas) para dizer se compra agora, compra com posição menor ou evita, e por quê. O texto explica que esperar uma entrada melhor tem custo, para a IA não ser conservadora demais.')}</th>
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

// Justificativa (screener.justification): uma linha por aspecto, título à esquerda e os sinais
// separados por "·" (cada sinal inteiro numa linha, sem quebrar no meio).
const justificationList = (parts) => `<dl class="facts">${parts.map(p => `<dt>${escapeHTML(p.label)}</dt>
    <dd>${p.items.map(i => `<span class="fact">${escapeHTML(i)}</span>`).join('<span class="fact-sep" aria-hidden="true"> · </span>')}</dd>`).join('')}</dl>`;

// Indicação do ranking (screener.advice): só compra ou espera; a venda fica na aba Carteira.
const ADVICE_BADGE = { comprar: 'badge-ok', aguardar: 'badge-fill' };

// Coluna "Histórico (backtest)": 10 anos, últimos 3 anos e compras abertas (números do Python).
function btCell(bt) {
    const avg = v => v === null || v === undefined ? '' : ` · méd. ${fmtPct(v)}`;
    const recent = bt.recentTrades ? `3 anos: ${bt.recentWins}/${bt.recentTrades}${avg(bt.recentReturn)}` : '3 anos: sem compras encerradas';
    const open = bt.open ? `<div class="small muted">abertas: ${bt.open}${avg(bt.openReturn)}</div>` : '';
    return `${fmtNum(bt.winRate, 0)}% <span class="small muted">(${bt.wins}/${bt.trades})${avg(bt.avgReturn)}</span>
        <div class="small muted">${recent}</div>${open}`;
}

function tableRows(top, kind) {
    if (top.length === 0) {
        return `<tr><td colspan="11" class="empty-row">Nenhum ${kind === 'acoes' ? 'ativo' : 'ativo deste tipo'} atende a todos os critérios hoje.</td></tr>`;
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
            <td class="c nowrap">${btCell(a.bt)}</td>
            <td class="c">${BOTTLE_BADGE[a.bottle]}</td>
            <td class="c">${a.advice ? signalBadge(a.advice.label, a.advice.reason, ADVICE_BADGE[a.advice.action] || 'badge-low') : '—'}</td>
            <td>${justificationList(a.justification)}</td>
            <td class="c"><button type="button" class="btn-secondary copy-analysis" data-ticker="${escapeHTML(a.ticker)}">COPIAR ANÁLISE</button></td>
        </tr>
    `).join('');
}

// ---------------------------------------------------------------- Copiar análise

// "COPIAR ANÁLISE": o texto vem pronto do servidor (ai_prompt.py), na visão que a página mostra.
// O ClipboardItem recebe a promessa do texto, para a cópia valer como parte do clique mesmo com a
// resposta chegando depois (o Safari exige); sem ele, copia ao chegar. Se o navegador recusar,
// o texto abre numa janela para copiar à mão.
async function copyAnalysis(btn) {
    const params = new URLSearchParams({
        ticker: btn.dataset.ticker,
        mode: document.getElementById('baseMode').value,
        strict: document.getElementById('requireCandle').checked ? '1' : '0',
        liquidez: document.getElementById('liquidity').value,
        carteira: currentPortfolio(),
    });
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'COPIANDO...';
    const textPromise = apiGet(`/scan/prompt?${params}`).then(r => r.text);
    textPromise.catch(() => { /* tratado abaixo */ });
    let done = 'COPIADA ✓';
    try {
        if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
            const blob = textPromise.then(t => new Blob([t], { type: 'text/plain' }));
            await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })]);
        } else {
            await navigator.clipboard.writeText(await textPromise);
        }
    } catch (err) {
        const text = await textPromise.catch(e => {
            const banner = document.getElementById('errorBanner');
            banner.innerText = `Não foi possível montar a análise de ${btn.dataset.ticker} (${e.message}).`;
            banner.style.display = 'block';
            done = 'ERRO';
            return null;
        });
        if (text !== null) {
            done = label;
            showCopyDialog(btn.dataset.ticker, text);
        }
    }
    btn.textContent = done;
    setTimeout(() => {
        btn.textContent = label;
        btn.disabled = false;
    }, 2000);
}

function showCopyDialog(ticker, text) {
    const dialog = document.createElement('dialog');
    dialog.className = 'copy-dialog';
    dialog.innerHTML = `<h3>Análise de ${escapeHTML(ticker)}</h3>
        <p class="small muted">O navegador não deixou copiar sozinho. Selecione o texto (já selecionado) e copie com Ctrl+C.</p>
        <textarea readonly></textarea>
        <div class="copy-dialog-actions"><button type="button" class="btn">Fechar</button></div>`;
    const area = dialog.querySelector('textarea');
    area.value = text;
    dialog.querySelector('button').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => dialog.remove());
    document.body.appendChild(dialog);
    dialog.showModal();
    area.focus();
    area.select();
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
