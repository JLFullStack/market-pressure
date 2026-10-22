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
    observeReveal();
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
    document.getElementById('livePill').hidden = !scan.liveSession;

    renderKpis(scan, view, strict);
    renderGauges(scan, view, strict);
    renderRankings(scan, view, strict);
    renderBova(mode);
}

// Tipos de ativo (screener.KINDS): título do painel, ícone, rótulo do medidor e nome curto.
const KIND_TITLE = {
    acoes: 'Ações (ON, PN e units)',
    fundos: 'Fundos listados (FII, Fiagro e FI-Infra)',
    etfs: 'ETFs de ações',
    bdrs: 'BDRs',
};
const KIND_ICON = {
    acoes: '<path d="M7 3v18M17 3v18"/><rect x="5" y="7" width="4" height="8" rx="1"/><rect x="15" y="9" width="4" height="7" rx="1"/>',
    fundos: '<path d="M3 11l9-7 9 7M5 10v11h14V10M9 21v-5h6v5"/>',
    etfs: '<path d="M12 3l9 5-9 5-9-5zM3 12.5l9 5 9-5M3 16.5l9 5 9-5"/>',
    bdrs: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
};
const kindIcon = (kind) => `<span class="icon-chip" aria-hidden="true"><svg viewBox="0 0 24 24">${KIND_ICON[kind]}</svg></span>`;
const KIND_LABEL = { acoes: 'Ações', fundos: 'Fundos', etfs: 'ETFs', bdrs: 'BDRs' };
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
    setCount(document.getElementById('kpiScanned'), view.scanned);
    document.getElementById('kpiScannedNote').innerText = byKind(r => r.scanned) +
        (scan.failures ? ` · ${scan.failures} sem dados suficientes` : '');
    setCount(document.getElementById('kpiQualified'), results.reduce((sum, r) => sum + r.result.qualified, 0));
    document.getElementById('kpiQualifiedNote').innerText = byKind((r, result) => result.qualified);
    const best = results.filter(r => r.result.top.length)
        .map(r => ({ kind: r.kind, a: r.result.top[0] }))
        .sort((x, y) => y.a.score - x.a.score)[0];
    document.getElementById('kpiBest').innerText = best ? best.a.ticker : '—';
    document.getElementById('kpiBestNote').innerText = best ? `SC ${fmtNum(best.a.score)} · ${KIND_SHORT[best.kind]}` : '';
}

// ---------------------------------------------------------------- Medidores de amplitude

// Meio círculo de raio r em volta de (GAUGE_CX, GAUGE_CY): 0% à esquerda, 100% à direita, por cima.
const GAUGE_CX = 60, GAUGE_CY = 60;
function gaugePoint(v, r) {
    const a = Math.PI * (1 - v / 100);
    return `${(GAUGE_CX + r * Math.cos(a)).toFixed(2)} ${(GAUGE_CY - r * Math.sin(a)).toFixed(2)}`;
}
const gaugeArc = (from, to, r) => `M ${gaugePoint(from, r)} A ${r} ${r} 0 0 1 ${gaugePoint(to, r)}`;

// Um medidor por tipo: a amplitude (breadth) e as faixas dos regimes (scan.regimeBands, do
// Python). Cada um é um link para o ranking do tipo.
function renderGauges(scan, view, strict) {
    const bands = scan.regimeBands || { misto: 40, alta: 60 };
    const el = document.getElementById('breadthGauges');
    el.innerHTML = view.rankings.map((r) => {
        const result = r[strict ? 'strict' : 'loose'];
        const regime = REGIME[r.regime];
        const breadth = r.breadth;
        const off = breadth === null ? 100 : 100 - breadth;
        return `<a class="gauge-card regime-${r.regime || 'none'}" href="#ranking-${r.kind}" data-reveal
                aria-label="${KIND_LABEL[r.kind]}: ${breadth === null ? 'amostra pequena' : `${fmtNum(breadth, 0)}% em tendência de alta, ${regime.label.toLowerCase()}`}. Ir para o ranking.">
            <div class="gauge-wrap">
                <svg class="gauge" viewBox="0 0 120 66" aria-hidden="true">
                    <path class="gauge-band band-defensivo" d="${gaugeArc(0, bands.misto - 1, 57)}"/>
                    <path class="gauge-band band-misto" d="${gaugeArc(bands.misto + 1, bands.alta - 1, 57)}"/>
                    <path class="gauge-band band-alta" d="${gaugeArc(bands.alta + 1, 100, 57)}"/>
                    <path class="gauge-track" d="${gaugeArc(0, 100, 46)}"/>
                    ${breadth === null ? '' : `<path class="gauge-val" d="${gaugeArc(0, 100, 46)}" pathLength="100" style="--off:${off.toFixed(1)}"/>`}
                </svg>
                <span class="gauge-num num"${breadth === null ? '>—' : ` data-count="${breadth}" data-format="pctu" data-decimals="0">0%`}</span>
            </div>
            <div class="gauge-label">${KIND_LABEL[r.kind]}</div>
            ${regime ? `<span class="badge ${regime.badge}">${regime.label}</span>` : '<span class="badge badge-low">Amostra pequena</span>'}
            <div class="small muted">${result.qualified} no setup · ${r.scanned} com liquidez</div>
        </a>`;
    }).join('');
    observeReveal(el);
}

function renderRankings(scan, view, strict) {
    const el = document.getElementById('rankings');
    el.innerHTML = view.rankings
        .map(r => rankingPanel(scan, r, r[strict ? 'strict' : 'loose'], strict)).join('');
    observeReveal(el);
}

function rankingPanel(scan, r, result, strict) {
    const top = result.top;
    const facts = [
        `<strong>${r.scanned}</strong> com liquidez (média acima de R$ ${fmtNum(r.minAvgVolume / 1e6, 0)} mi por pregão)`,
        `<strong>${result.qualified}</strong> no setup`,
        top.length ? `pressão média <strong>${fmtNum(result.avgPressure)}%</strong>` : null,
        top.length ? `acerto médio <strong>${fmtNum(result.avgWinRate)}%</strong>` : null,
        r.duplicates ? `${r.duplicates} ETF(s) que repetem um índice já listado omitidos` : null,
    ].filter(Boolean).map(f => `<span class="chip">${f}</span>`).join('');
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
        <section class="panel ranking" id="ranking-${r.kind}" aria-labelledby="ranking-title-${r.kind}" data-reveal>
            <div class="ranking-head">
                <div class="panel-title">${kindIcon(r.kind)}<h2 id="ranking-title-${r.kind}">${KIND_TITLE[r.kind]} <span class="muted">— Top ${scan.topN}</span></h2></div>
                <div class="chips">${facts}</div>
                <div class="regime">${regimeText}</div>
                ${few}
            </div>
            <div class="table-container">
                <table class="sticky-two rank-merge">
                    <thead>
                        <tr>
                            <th class="c">Rank</th>
                            <th>Ticker</th>
                            <th class="c">Score Composto</th>
                            <th class="c">Preço Atual</th>
                            <th class="c">Média Testada</th>
                            <th class="c">Pressão % ${tip('O "gás" acumulado no pullback (volume, momentum, tendência e estrutura, de 0 a 100). Descreve o padrão; não é probabilidade de acerto.\nO selo abaixo é a mesma pressão em faixas: Cheia a partir de 75%, Quase cheia de 60% a 75%, Enchendo abaixo de 60%.\nAs barrinhas ao lado são os componentes, de 0 a 100: Volume (azul), Momentum (ciano), Tendência (roxo) e Estrutura (laranja). Toque nelas para ver os valores e pesos.')}</th>
                            <th class="c">Histórico (backtest) ${tip('A estratégia aplicada aos ~10 anos de histórico do ativo, com a regra de venda configurada para o tipo dele.\nPrimeira linha: taxa de acerto das compras encerradas (suavizada para 50% com poucas), acertos/compras e o retorno médio por compra.\n3 anos: as compras encerradas nos últimos 3 anos. Nas ações, foi o histórico que melhor separou compras boas de ruins.\nAbertas: compras ainda abertas, pelo preço de hoje (num ativo em alta costumam ser as melhores).')}</th>
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
    <dd>${p.items.map(i => `<span class="fact">${escapeHTML(i)}</span>`).join('<span class="fact-sep" aria-hidden="true"> · </span>')}</dd>`).join('')}</dl>`
    // No celular a lista some e fica este botão, que abre a mesma justificativa na caixa flutuante
    // (um parágrafo por aspecto).
    + signalBadge('Justificativa', parts.map(p => `${p.label}: ${p.items.join(' · ')}`).join('\n'), 'badge-low facts-btn');

// Indicação do ranking (screener.advice): só compra ou espera; a venda fica na aba Carteira.
const ADVICE_BADGE = { comprar: 'badge-ok', aguardar: 'badge-fill' };

// Coluna "Score Composto": anel que se desenha ao aparecer (0 a 100), com o número no meio.
const scoreRing = (score) => `<span class="ring" style="--off:${(100 - Math.max(0, Math.min(100, score))).toFixed(1)}">
    <svg viewBox="0 0 44 44" aria-hidden="true"><circle class="ring-track" cx="22" cy="22" r="18"/><circle class="ring-val" cx="22" cy="22" r="18" pathLength="100"/></svg>
    <span class="ring-num num">${fmtNum(score)}</span></span>`;

// Coluna "Pressão %": o total, as quatro barras dos componentes (screener.pressure_parts, com o
// peso de cada um no tipo; o motivo abre na caixa flutuante) e o selo da garrafa.
function pressureCell(a) {
    const parts = a.pressureParts || [];
    const detail = parts.map(p => `${p.label}: ${fmtNum(p.value, 0)} de 100 (peso ${fmtNum(p.weight, 0)}%)`).join('\n');
    const bars = parts.map((p, k) => `<i class="eq-${p.key}" style="--v:${(Math.max(0, Math.min(100, p.value)) / 100).toFixed(3)};--k:${k}"></i>`).join('');
    const eq = parts.length
        ? `<span class="eq" tabindex="0" data-tip="${escapeHTML(detail)}" data-tip-title="Componentes da pressão" aria-label="Componentes da pressão: ${escapeHTML(detail.replace(/\n/g, '; '))}">${bars}</span>`
        : '';
    return `<div class="press">${eq}<span class="press-num num">${fmtNum(a.pressure, 0)}%</span></div>
        <div class="small" style="margin-top: 6px;">${BOTTLE_BADGE[a.bottle]}</div>`;
}

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
        return `<tr><td colspan="10" class="empty-row">Nenhum ${kind === 'acoes' ? 'ativo' : 'ativo deste tipo'} atende a todos os critérios hoje.</td></tr>`;
    }
    const medals = ['🥇', '🥈', '🥉'];
    return top.map((a, index) => `
        <tr class="${index < 3 ? `podium podium-${index + 1}` : ''}" data-reveal>
            <td class="c nowrap" style="font-weight: bold;">${medals[index] || ''} ${index + 1}º</td>
            <td><div class="rank-inline">${medals[index] || ''} ${index + 1}º</div>
                <div class="asset"><span class="avatar av-${kind}" aria-hidden="true">${escapeHTML(a.ticker.slice(0, 2))}</span>
                <div class="asset-text"><a href="https://br.tradingview.com/chart/?symbol=BMFBOVESPA:${a.ticker}" target="_blank" rel="noopener" class="ticker-link">${a.ticker}</a>
                <a href="backtest.html?ticker=${encodeURIComponent(a.ticker)}" class="inline-link small" aria-label="Backtest de ${a.ticker}">backtest</a>
                <div class="small muted" title="${escapeHTML(a.sector || a.name || '')}">${escapeHTML(a.sector || a.name || '')}</div></div></div></td>
            <td class="c">${scoreRing(a.score)}</td>
            <td class="c nowrap num">${fmtBRL(a.price)}<div class="small" style="color: ${a.change >= 0 ? 'var(--success)' : 'var(--danger)'};">${a.change >= 0 ? '+' : ''}${fmtNum(a.change, 2)}%</div></td>
            <td class="c"><span class="ma-pill">${a.setup.ma.name}</span></td>
            <td class="c nowrap">${pressureCell(a)}</td>
            <td class="c nowrap">${btCell(a.bt)}</td>
            <td class="c">${a.advice ? signalBadge(a.advice.label, a.advice.reason, ADVICE_BADGE[a.advice.action] || 'badge-low') : '—'}</td>
            <td>${justificationList(a.justification)}</td>
            <td class="c"><button type="button" class="btn-analyze copy-analysis" data-ticker="${escapeHTML(a.ticker)}">ANALISAR ATIVO</button></td>
        </tr>
    `).join('');
}

// ---------------------------------------------------------------- Copiar análise

// "ANALISAR ATIVO": o texto vem pronto do servidor (ai_prompt.py), na visão que a página mostra
// (copyServerText, em core.js).
async function copyAnalysis(btn) {
    const ticker = btn.dataset.ticker;
    const params = new URLSearchParams({
        ticker,
        mode: document.getElementById('baseMode').value,
        strict: document.getElementById('requireCandle').checked ? '1' : '0',
        liquidez: document.getElementById('liquidity').value,
        carteira: currentPortfolio(),
    });
    try {
        await copyServerText(btn, apiGet(`/scan/prompt?${params}`).then(r => r.text), `Análise de ${ticker}`);
    } catch (e) {
        const banner = document.getElementById('errorBanner');
        banner.innerText = `Não foi possível montar a análise de ${ticker} (${e.message}).`;
        banner.style.display = 'block';
    }
}

// ---------------------------------------------------------------- KPI do BOVA11

async function renderBova(mode) {
    const value = document.getElementById('kpiBova');
    const note = document.getElementById('kpiBovaNote');
    const card = document.getElementById('kpiBovaCard');
    const spark = document.getElementById('bovaSpark');
    try {
        const b = await apiGet(`/bova?mode=${mode}`);
        setCount(value, b.change, { format: 'pct', decimals: 1 });
        value.style.color = b.change >= 0 ? 'var(--success)' : 'var(--danger)';
        card.classList.toggle('tone-green', b.change >= 0);
        card.classList.toggle('tone-red', b.change < 0);
        drawSpark(spark, b.spark, b.change >= 0);
        note.innerText = `${fmtBRL(b.close)} · ` + (b.above.length === 3 ? 'acima das três médias'
            : b.above.length === 0 ? 'abaixo das três médias'
            : `acima de ${b.above.join(' e ')}; abaixo de ${b.below.join(' e ')}`);
    } catch (e) {
        setCount(value, null);
        value.style.color = '';
        spark.innerHTML = '';
        note.innerText = `BOVA11 indisponível (${e.message})`;
    }
}
