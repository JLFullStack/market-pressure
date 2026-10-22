// Base comum das abas Screener (app.js) e Carteira (carteira.js): acesso à API e formatadores
// de tela. Script clássico, carregado antes do script da página.
// Todos os cálculos (indicadores, setup, ranking, carteira, aporte) ficam no Python; aqui só
// se busca o resultado pronto e se exibe.
//
// Onde o Python roda:
// - local: no server.py. Se a página for aberta por outro servidor local (ex.: Live Server),
//   as chamadas vão para a porta padrão do server.py;
// - versão publicada (Cloudflare) e wrangler dev (porta 8787): no próprio navegador (Pyodide,
//   num Web Worker, assets/js/py-worker.js), com as mesmas rotas /api. Ali há login: o Worker
//   exige sessão em tudo (worker/auth.js); aqui só se mostra quem entrou e o "Sair".
const SERVER_PORT = "8765";
const API = location.port === SERVER_PORT ? "/api" : `http://127.0.0.1:${SERVER_PORT}/api`;
const IN_BROWSER = !/^(localhost|127\.0\.0\.1)$/.test(location.hostname) || location.port === "8787";

const MODE_KEY = "marketpressure_base_mode"; // base "fechamento" | "intradiario", comum às duas abas
const LIQUIDITY_KEY = "marketpressure_liquidez"; // liquidez mínima do ranking (screener.LIQUIDITY), Screener e Backtest
const OFFLINE = 'servidor local fora do ar — rode "python server.py" na pasta do projeto';

// ---------------------------------------------------------------- API

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// Erro da API com o corpo da resposta (ex.: erros por linha da lista de compras).
// status 400 = a ação foi recusada pela validação (nada foi gravado).
class ApiError extends Error {
    constructor(message, status, data = {}) { super(message); this.status = status; this.data = data; }
}

// Python no navegador: um Web Worker para as duas abas da página, iniciado no primeiro pedido
// (o primeiro carregamento baixa o Pyodide, alguns segundos; depois fica no cache).
let pyWorker = null;
let pySeq = 0;
const pyPending = new Map();

function pyCall(method, path, body) {
    if (!pyWorker) {
        pyWorker = new Worker('assets/js/py-worker.js', { type: 'module' });
        pyWorker.onmessage = (e) => {
            const resolve = pyPending.get(e.data.id);
            pyPending.delete(e.data.id);
            if (resolve) resolve(e.data);
        };
        pyWorker.onerror = (e) => {
            const message = `falha ao iniciar o Python no navegador${e.message ? `: ${e.message}` : ''}`;
            for (const resolve of pyPending.values()) resolve({ status: 500, data: { message } });
            pyPending.clear();
            pyWorker = null;
        };
    }
    return new Promise(resolve => {
        const id = ++pySeq;
        pyPending.set(id, resolve);
        pyWorker.postMessage({ id, method, path: `/api${path}`, body: body === undefined ? null : JSON.stringify(body) });
    });
}

// ---------------------------------------------------------------- Login (versão publicada)

function toLogin() {
    location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`);
}

// Usuário da sessão ({name, email, admin}) ou null; sessão vencida → tela de login.
async function checkSession() {
    const response = await fetch('/auth/me', { cache: 'no-store' }).catch(() => null);
    if (response?.status === 401) {
        toLogin();
        return null;
    }
    return response?.ok ? response.json() : null;
}

// Elementos [data-auth] (link "Conta", nome e "Sair") só aparecem com login.
const session = IN_BROWSER ? checkSession() : Promise.resolve(null);
session.then(user => {
    if (!user) return;
    document.querySelectorAll('[data-auth]').forEach(el => { el.hidden = false; });
    const name = document.getElementById('userName');
    if (name) name.textContent = user.name;
    document.getElementById('logoutBtn')?.addEventListener('click', async () => {
        await fetch('/auth/logout', { method: 'POST' }).catch(() => null);
        location.replace('/login');
    });
});

// ---------------------------------------------------------------- Pedidos

// {status, data} do pedido; erro de rede só no modo local (servidor fora do ar).
async function send(method, path, body) {
    if (IN_BROWSER) {
        const res = await pyCall(method, path, body);
        if (res.status >= 400) await checkSession(); // o erro pode ser a sessão que venceu
        return res;
    }
    const init = method === 'GET' ? undefined
        : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const response = await fetch(`${API}${path}`, init);
    return { status: response.status, data: await response.json().catch(() => ({})) };
}

function checked({ status, data }) {
    if (status < 200 || status >= 300) throw new ApiError(data.message || `HTTP ${status}`, status, data);
    return data;
}

async function apiGet(path, retries = 2) {
    let res;
    try {
        res = await send('GET', path);
    } catch (e) {
        if (retries > 0) {
            await sleep(1000);
            return apiGet(path, retries - 1);
        }
        throw new Error(OFFLINE);
    }
    if ((res.status === 429 || res.status === 502) && retries > 0) {
        await sleep(1500);
        return apiGet(path, retries - 1);
    }
    return checked(res);
}

// POST com corpo JSON (sem novas tentativas: pode gravar).
async function apiPost(path, body) {
    let res;
    try {
        res = await send('POST', path, body);
    } catch (e) {
        throw new Error(OFFLINE);
    }
    return checked(res);
}

// ---------------------------------------------------------------- Indicador de carregamento

// Spinner com fundo escurecido enquanto os dados são consultados ou gravados. Cada chamada de
// busyStart(texto) devolve uma função que a encerra (pode ser chamada mais de uma vez); com
// várias abertas, vale o texto da mais recente. Só aparece depois de BUSY_DELAY_MS, para não
// piscar em respostas rápidas. Não é usado nos recálculos feitos enquanto se digita (aporte,
// totais da lista, cotação do formulário), para não atrapalhar a digitação.
const BUSY_DELAY_MS = 250;
const busyStack = [];
let busyEl = null;
let busyTimer = null;

function busyOverlay() {
    if (!busyEl) {
        busyEl = document.createElement('div');
        busyEl.className = 'busy-overlay';
        busyEl.hidden = true;
        busyEl.innerHTML = `<div class="busy-box" role="status" aria-live="polite">
            <div class="busy-spinner" aria-hidden="true"></div><div class="busy-text"></div></div>`;
        document.body.appendChild(busyEl);
    }
    return busyEl;
}

function busyRender() {
    const top = busyStack[busyStack.length - 1];
    const el = busyOverlay();
    if (!top) {
        clearTimeout(busyTimer);
        busyTimer = null;
        el.hidden = true;
        document.body.removeAttribute('aria-busy');
        return;
    }
    el.querySelector('.busy-text').textContent = top.text;
    document.body.setAttribute('aria-busy', 'true');
    if (el.hidden && !busyTimer) {
        busyTimer = setTimeout(() => { busyTimer = null; if (busyStack.length) el.hidden = false; }, BUSY_DELAY_MS);
    }
}

function busyStart(text = 'Carregando...') {
    const entry = { text };
    busyStack.push(entry);
    busyRender();
    const end = () => {
        const k = busyStack.indexOf(entry);
        if (k >= 0) { busyStack.splice(k, 1); busyRender(); }
    };
    end.text = (t) => { entry.text = t; busyRender(); }; // troca o texto (ex.: progresso)
    return end;
}

// ---------------------------------------------------------------- Explicações flutuantes

// Ícone "?" com a explicação em data-tip (mostrada ao passar o mouse, focar com o teclado ou tocar).
const tip = (text) => `<span class="tip" tabindex="0" data-tip="${escapeHTML(text)}" aria-label="Ajuda: ${escapeHTML(text)}">?</span>`;

// Indicação (COMPRAR, VENDER, MANTER, AGUARDAR) como botão: o motivo abre na caixa flutuante.
const signalBadge = (label, reason, badgeClass) => `<button type="button" class="badge ${badgeClass} signal-badge"
    data-tip="${escapeHTML(reason)}" data-tip-title="${escapeHTML(label)}" aria-label="${escapeHTML(label)}: ${escapeHTML(reason)}">${escapeHTML(label)}<span class="signal-info" aria-hidden="true">i</span></button>`;

// Uma única caixa flutuante (position: fixed) para todo elemento com data-tip: não é cortada
// pelas tabelas com rolagem e fica dentro da tela. data-tip-title vira o título da caixa.
// Parágrafos da caixa: os separados por "\n" (motivos das indicações, do Python) ou, num texto
// corrido, uma frase por parágrafo (corta depois de . ! ? seguido de espaço e maiúscula, então
// abreviações como "Estoc. tocou" e "Ex.: 10" ficam inteiras).
function tipParagraphs(text) {
    const parts = text.includes('\n') ? text.split('\n') : text.split(/(?<=[.!?])\s+(?=[A-ZÀ-Ý"(])/u);
    return parts.map(p => p.trim()).filter(Boolean);
}

// O "?" de um rótulo nunca cai sozinho na linha de baixo: o texto antes dele vira um bloco
// (span.tip-text, que pode quebrar linha) e o ícone fica ao lado da primeira linha desse bloco
// (span.tip-label, inline-flex). Campos (input, select) antes do texto ficam de fora.
function keepTipsWithText(root = document) {
    const FIELD = /^(INPUT|SELECT|TEXTAREA|BUTTON|BR)$/;
    root.querySelectorAll('.tip').forEach(tipEl => {
        if (tipEl.parentElement.classList.contains('tip-label')) return;
        const before = [];
        for (let n = tipEl.previousSibling; n && !(n.nodeType === Node.ELEMENT_NODE && FIELD.test(n.tagName)); n = n.previousSibling) {
            before.unshift(n);
        }
        if (!before.some(n => n.textContent.trim())) return;
        const label = document.createElement('span');
        label.className = 'tip-label';
        const text = document.createElement('span');
        text.className = 'tip-text';
        tipEl.before(label);
        text.append(...before);
        label.append(text, tipEl);
    });
}

let tipsReady = false;
function setupTips() {
    if (tipsReady) return;
    tipsReady = true;
    const box = document.createElement('div');
    box.className = 'tip-box';
    box.id = 'tipBox';
    box.setAttribute('role', 'tooltip');
    box.hidden = true;
    document.body.appendChild(box);
    let current = null;
    const show = (el) => {
        current = el;
        const title = el.dataset.tipTitle;
        // O texto pode vir em parágrafos separados por "\n" (motivos das indicações, do Python).
        const paragraphs = tipParagraphs(el.dataset.tip).map(p => `<p>${escapeHTML(p)}</p>`).join('');
        box.innerHTML = (title ? `<strong class="tip-title">${escapeHTML(title)}</strong>` : '') + paragraphs;
        box.hidden = false;
        const r = el.getBoundingClientRect();
        const left = Math.min(Math.max(8, r.left + r.width / 2 - box.offsetWidth / 2), window.innerWidth - box.offsetWidth - 8);
        let top = r.top - box.offsetHeight - 8;
        if (top < 8) top = r.bottom + 8;
        box.style.left = `${left}px`;
        box.style.top = `${top}px`;
        el.setAttribute('aria-describedby', 'tipBox');
    };
    const hide = () => { box.hidden = true; current?.removeAttribute('aria-describedby'); current = null; };
    const target = (ev) => ev.target.closest?.('[data-tip]');
    document.addEventListener('mouseover', (ev) => { const el = target(ev); if (el) show(el); else if (current && document.activeElement !== current) hide(); });
    document.addEventListener('focusin', (ev) => { const el = target(ev); if (el) show(el); else hide(); });
    document.addEventListener('focusout', (ev) => { if (ev.target === current) hide(); });
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') hide(); });
    window.addEventListener('scroll', hide, { passive: true });
    // Um toque/clique mostra a explicação (no celular não há "passar o mouse") sem ativar o campo
    // ou a linha da tabela; tocar em outro lugar fecha.
    document.addEventListener('click', (ev) => {
        const el = target(ev);
        if (el) { ev.preventDefault(); ev.stopPropagation(); show(el); } else if (current) hide();
    }, true);
}
// Botão "voltar ao topo" (canto inferior direito; no celular acima da barra de abas, pelo CSS):
// aparece depois de rolar SCROLL_TOP_AFTER px.
const SCROLL_TOP_AFTER = 400;
function setupScrollTop() {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'scroll-top';
    btn.setAttribute('aria-label', 'Voltar ao topo');
    btn.title = 'Voltar ao topo';
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M6 15l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    btn.tabIndex = -1;
    document.body.appendChild(btn);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: reduced.matches ? 'auto' : 'smooth' }));
    let shown = false;
    const update = () => {
        const show = window.scrollY > SCROLL_TOP_AFTER;
        if (show === shown) return;
        shown = show;
        btn.classList.toggle('visible', show);
        btn.tabIndex = show ? 0 : -1;
    };
    window.addEventListener('scroll', update, { passive: true });
    update();
}
document.addEventListener('DOMContentLoaded', () => { setupTips(); keepTipsWithText(); setupScrollTop(); });

// ---------------------------------------------------------------- Efeitos visuais

const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

// Destaque das abas do topo: um único fundo (.tab-glow) que desliza até a aba sob o mouse e volta
// para a atual ao sair. Criado já na carga do script (o <nav> vem antes dele na página), para a
// transição entre páginas (@view-transition, no CSS) encontrá-lo e levá-lo de uma aba à outra.
function setupTabGlow() {
    const nav = document.querySelector('nav.tabs');
    if (!nav || nav.querySelector('.tab-glow')) return;
    const glow = document.createElement('span');
    glow.className = 'tab-glow';
    glow.setAttribute('aria-hidden', 'true');
    nav.prepend(glow);
    nav.classList.add('has-glow');
    const current = () => nav.querySelector('.tab[aria-current="page"]');
    const place = (tab) => {
        glow.style.opacity = tab ? '' : '0';
        if (!tab) return;
        glow.style.width = `${tab.offsetWidth}px`;
        glow.style.transform = `translateX(${tab.offsetLeft}px)`;
    };
    place(current());
    requestAnimationFrame(() => requestAnimationFrame(() => glow.classList.add('moving')));
    nav.addEventListener('pointerover', (e) => {
        const tab = e.target.closest('.tab');
        if (!tab || e.pointerType !== 'mouse') return;
        glow.classList.toggle('hovering', tab !== current());
        place(tab);
    });
    nav.addEventListener('pointerleave', () => { glow.classList.remove('hovering'); place(current()); });
    nav.addEventListener('click', (e) => {
        const tab = e.target.closest('.tab');
        if (tab) { glow.classList.remove('hovering'); place(tab); }
    });
    // Fontes carregadas, aba "Conta" que aparece com o login, barra que muda no celular.
    if (window.ResizeObserver) new ResizeObserver(() => place(current())).observe(nav);
}
setupTabGlow();

// Entrada ao rolar: elementos com data-reveal ficam ocultos (só com a classe js-reveal no <html>,
// para nada sumir se o script falhar) e ganham a classe .in quando entram na tela, uma vez. O .in
// também dispara as animações de dentro (anéis, barras, medidores) e os contadores [data-count].
document.documentElement.classList.add('js-reveal');
let revealObserver = null;

function revealNow(el) {
    el.classList.add('in');
    if (el.matches('[data-count]')) countUp(el);
    el.querySelectorAll('[data-count]').forEach(countUp);
}

// O bloco só aparece quando já subiu REVEAL_MARGIN acima do rodapé da tela (senão a animação
// acontece fora da vista, colada no rodapé). Os que entram juntos (cartões, linhas) aparecem em
// sequência: cada um recebe --stagger, o atraso usado pelas animações dele e de dentro dele.
const REVEAL_MARGIN = '0px 0px -20% 0px';
const REVEAL_STAGGER_MS = 90;

function observeReveal(root = document) {
    const els = root.querySelectorAll('[data-reveal]:not(.in)');
    if (!window.IntersectionObserver || REDUCED_MOTION.matches) { els.forEach(revealNow); return; }
    revealObserver = revealObserver || new IntersectionObserver((entries) => {
        entries.filter(entry => entry.isIntersecting).forEach((entry, k) => {
            revealObserver.unobserve(entry.target);
            entry.target.style.setProperty('--stagger', `${k * REVEAL_STAGGER_MS}ms`);
            revealNow(entry.target);
        });
    }, { rootMargin: REVEAL_MARGIN });
    els.forEach(el => revealObserver.observe(el));
}

// Contador: o número sobe do valor mostrado antes (0 na primeira vez) até data-count, em
// COUNT_MS. data-format: "int" (padrão), "num" (data-decimals casas), "pct" (com sinal) ou
// "pctu" (sem sinal); data-suffix é acrescentado ao fim.
const COUNT_MS = 900;
const COUNT_FORMAT = {
    int: (v) => fmtNum(v, 0),
    num: (v, d) => fmtNum(v, d),
    pct: (v, d) => fmtPct(v, d),
    pctu: (v, d) => `${fmtNum(v, d)}%`,
};

function countUp(el) {
    const target = Number(el.dataset.count);
    if (!Number.isFinite(target)) return;
    const format = COUNT_FORMAT[el.dataset.format || 'int'];
    const decimals = Number(el.dataset.decimals || 1);
    const suffix = el.dataset.suffix || '';
    const show = (v) => { el.textContent = format(v, decimals) + suffix; };
    const from = Number.isFinite(el._shown) ? el._shown : 0;
    el._shown = target;
    cancelAnimationFrame(el._frame);
    if (REDUCED_MOTION.matches || from === target) { show(target); return; }
    const start = performance.now();
    const step = (now) => {
        const t = Math.min(1, (now - start) / COUNT_MS);
        show(from + (target - from) * (1 - Math.pow(1 - t, 3)));
        if (t < 1) el._frame = requestAnimationFrame(step);
    };
    el._frame = requestAnimationFrame(step);
}

// Atualiza um contador: anima já se o bloco dele estiver visível; senão, ao aparecer.
function setCount(el, value, options = {}) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        delete el.dataset.count;
        el._shown = undefined;
        el.textContent = '—';
        return;
    }
    el.dataset.count = value;
    for (const [k, v] of Object.entries(options)) el.dataset[k] = v;
    const box = el.closest('[data-reveal]');
    if (!box || box.classList.contains('in')) countUp(el);
}

// ---------------------------------------------------------------- Copiar texto para IA

// Botões que copiam um texto montado no servidor (as análises para IA do Screener e da Carteira).
// O ClipboardItem recebe a promessa do texto, para a cópia valer como parte do clique mesmo com a
// resposta chegando depois (o Safari exige); sem ele, copia ao chegar. Se o navegador recusar,
// o texto abre numa janela para copiar à mão. Erro ao montar o texto: o botão mostra "ERRO" e a
// promessa devolvida é rejeitada com ele, para a página mostrar a mensagem.
async function copyServerText(btn, textPromise, title, doneText = 'COPIADA ✓') {
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'COPIANDO...';
    textPromise.catch(() => { /* tratado abaixo */ });
    let done = doneText, error = null;
    try {
        if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
            const blob = textPromise.then(t => new Blob([t], { type: 'text/plain' }));
            await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })]);
        } else {
            await navigator.clipboard.writeText(await textPromise);
        }
    } catch (err) {
        const text = await textPromise.catch(e => { error = e; return null; });
        if (text !== null) {
            done = label;
            showCopyDialog(title, text);
        } else {
            done = 'ERRO';
        }
    }
    btn.textContent = done;
    if (done !== label) btn.classList.add(done === 'ERRO' ? 'copy-failed' : 'copied');
    setTimeout(() => {
        btn.textContent = label;
        btn.disabled = false;
        btn.classList.remove('copied', 'copy-failed');
    }, 2000);
    if (error) throw error;
}

function showCopyDialog(title, text) {
    const dialog = document.createElement('dialog');
    dialog.className = 'copy-dialog';
    dialog.innerHTML = `<h3>${escapeHTML(title)}</h3>
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

// ---------------------------------------------------------------- Formatadores

const fmtBRL = (v) => `R$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtNum = (v, d = 1) => v.toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (v, d = 1) => `${v >= 0 ? '+' : ''}${fmtNum(v, d)}%`;
const fmtDate = (iso) => iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '—';
const escapeHTML = (s) => String(s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
