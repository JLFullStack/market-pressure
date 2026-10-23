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
    try { sessionStorage.removeItem('marketpressure_logado'); } catch (e) { /* sem armazenamento */ }
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

// Elementos [data-auth] (link "Conta", nome e "Sair") só aparecem com login. Com o login já
// confirmado nesta sessão do navegador (AUTH_KEY), aparecem já na carga da página: no celular as
// abas dividem a barra, e a "Conta" surgindo depois movia as outras (e o destaque da aba).
const AUTH_KEY = 'marketpressure_logado';
const showAuth = (on) => document.querySelectorAll('[data-auth]').forEach(el => { el.hidden = !on; });
try { if (IN_BROWSER && sessionStorage.getItem(AUTH_KEY) === '1') showAuth(true); } catch (e) { /* sem armazenamento */ }
const session = IN_BROWSER ? checkSession() : Promise.resolve(null);
session.then(user => {
    // Sem resposta (rede): mantém como está; sessão vencida já foi para o login (toLogin limpa a marca).
    if (!user) return;
    try { sessionStorage.setItem(AUTH_KEY, '1'); } catch (e) { /* sem armazenamento */ }
    showAuth(true);
    const name = document.getElementById('userName');
    if (name) name.textContent = user.name;
    document.getElementById('logoutBtn')?.addEventListener('click', async () => {
        await fetch('/auth/logout', { method: 'POST' }).catch(() => null);
        try { sessionStorage.removeItem(AUTH_KEY); } catch (e) { /* sem armazenamento */ }
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
const BUSY_DELAY_MS = 400;
// Uma vez à vista, o spinner fica pelo menos BUSY_MIN_MS e some com fade (BUSY_FADE_MS): sem
// isso, uma resposta logo depois do atraso fazia a tela escurecer e clarear num piscar.
const BUSY_MIN_MS = 450;
const BUSY_FADE_MS = 180;
const busyStack = [];
let busyEl = null;
let busyTimer = null;
let busyShownAt = 0;
let busyHideTimer = null;

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
        document.body.removeAttribute('aria-busy');
        if (el.hidden || busyHideTimer) return;
        const wait = Math.max(0, BUSY_MIN_MS - (performance.now() - busyShownAt));
        busyHideTimer = setTimeout(() => {
            el.classList.add('leaving');
            busyHideTimer = setTimeout(() => {
                busyHideTimer = null;
                el.classList.remove('leaving');
                if (!busyStack.length) el.hidden = true; else busyRender();
            }, BUSY_FADE_MS);
        }, wait);
        return;
    }
    el.querySelector('.busy-text').textContent = top.text;
    document.body.setAttribute('aria-busy', 'true');
    if (busyHideTimer && !el.classList.contains('leaving')) { clearTimeout(busyHideTimer); busyHideTimer = null; } // voltou a carregar antes de sumir
    if (el.hidden && !busyTimer) {
        busyTimer = setTimeout(() => {
            busyTimer = null;
            if (!busyStack.length) return;
            el.hidden = false;
            busyShownAt = performance.now();
        }, BUSY_DELAY_MS);
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

// Pílula deslizante: um fundo único atrás do item ativo de um grupo (abas do topo, sub-abas da
// Carteira, botões de período .seg) que anda até o item sob o mouse e volta ao ativo ao sair.
// activeSel diz qual é o ativo; a pílula acompanha as mudanças de aria-selected/aria-pressed/
// aria-current e os grupos redesenhados por innerHTML (ela volta para dentro do grupo).
function setupSlidingPill(group, itemSel, activeSel, pillClass = 'slide-pill') {
    if (!group || group._pill) return;
    const pill = document.createElement('span');
    pill.className = pillClass;
    pill.setAttribute('aria-hidden', 'true');
    group._pill = pill;
    group.classList.add('has-pill');
    group.prepend(pill);
    const active = () => group.querySelector(activeSel);
    let placed = false;
    const place = (item) => {
        if (!item || !item.offsetWidth) { pill.style.opacity = '0'; placed = false; return; }
        if (!placed) pill.classList.remove('moving'); // primeira vez (ou ao reaparecer): sem deslizar de 0,0
        pill.style.opacity = '';
        pill.style.width = `${item.offsetWidth}px`;
        pill.style.height = `${item.offsetHeight}px`;
        pill.style.transform = `translate(${item.offsetLeft}px, ${item.offsetTop}px)`;
        if (!placed) {
            placed = true;
            requestAnimationFrame(() => requestAnimationFrame(() => pill.classList.add('moving')));
        }
    };
    // Item clicado: a pílula fica nele mesmo antes de ele virar o ativo (nas abas do topo, só a
    // página seguinte marca a aba nova; sem isso a pílula voltaria à aba antiga por um instante).
    let pending = null;
    const rest = () => {
        pill.classList.remove('hovering');
        const now = active();
        if (pending && (pending === now || !group.contains(pending))) pending = null;
        place(pending || now);
    };
    let frame = 0;
    const later = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(rest); };
    // Layout que mudou sozinho (aba que aparece, fonte carregada, tela girada): a pílula vai direto
    // para o lugar novo, sem deslizar (senão parecia voltar para outra aba e depois ir para a certa).
    const snap = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
            const moving = pill.classList.contains('moving');
            pill.classList.remove('moving');
            rest();
            if (moving) { void pill.offsetWidth; pill.classList.add('moving'); }
        });
    };
    rest();
    group.addEventListener('pointerover', (e) => {
        const item = e.target.closest(itemSel);
        if (!item || e.pointerType !== 'mouse' || item.disabled) return;
        pill.classList.toggle('hovering', item !== active());
        place(item);
    });
    group.addEventListener('pointerleave', rest);
    group.addEventListener('click', (e) => {
        const item = e.target.closest(itemSel);
        // Ctrl/Cmd/Shift + clique abre o link em outra aba ou janela: esta página fica onde está.
        if (!item || item.disabled || e.ctrlKey || e.metaKey || e.shiftKey) return;
        pending = item;
        cancelAnimationFrame(frame);
        rest();
    });
    // Voltar à página pelo histórico (cache do navegador): a aba clicada antes não vale mais.
    window.addEventListener('pageshow', (e) => { if (e.persisted) { pending = null; rest(); } });
    new MutationObserver((records) => {
        if (!group.contains(pill)) group.prepend(pill);
        // Item ativo trocado: desliza; item que aparece/some ou grupo redesenhado: reposiciona.
        const layout = records.some(r => r.type === 'childList' || r.attributeName === 'hidden');
        if (layout) snap(); else later();
    }).observe(group, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-selected', 'aria-pressed', 'aria-current', 'hidden'] });
    // Fontes carregadas, grupo que sai de uma seção oculta, tela girada: o tamanho dos itens muda.
    if (window.ResizeObserver) {
        const ro = new ResizeObserver(snap);
        ro.observe(group);
        group.querySelectorAll(itemSel).forEach(item => ro.observe(item));
    }
}

// Abas do topo: criadas já na carga do script (o <nav> vem antes dele na página), para a
// transição entre páginas (@view-transition, no CSS) encontrar a pílula e levá-la de uma aba à outra.
setupSlidingPill(document.querySelector('nav.tabs'), '.tab', '.tab[aria-current="page"]', 'tab-glow');
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.subtabs').forEach(g => setupSlidingPill(g, '[role="tab"]', '[aria-selected="true"]'));
    document.querySelectorAll('.seg').forEach(g => setupSlidingPill(g, 'button', 'button[aria-pressed="true"]'));
});

// Entrada ao rolar: elementos com data-reveal ficam ocultos (só com a classe js-reveal no <html>,
// para nada sumir se o script falhar) e ganham a classe .in quando entram na tela, uma vez. O .in
// também dispara as animações de dentro (anéis, barras, medidores) e os contadores [data-count].
document.documentElement.classList.add('js-reveal');
let revealObserver = null;

function revealNow(el) {
    revealPending.delete(el);
    el.classList.add('in');
    if (el._revealHost) el._revealHost._itemsSeen = true; // revealItems: a lista já apareceu
    // Só os contadores deste bloco: os que estão dentro de outro [data-reveal] (um cartão dentro
    // do painel) contam quando aquele bloco aparecer.
    el.querySelectorAll('[data-count]').forEach(c => { if (c.closest('[data-reveal]') === el) countUp(c); });
    if (el.matches('[data-count]')) countUp(el);
    const waiting = el._onReveal || [];
    el._onReveal = [];
    waiting.forEach(fn => fn());
}

// Roda fn quando o bloco de el (o [data-reveal] mais próximo) aparecer na tela; na hora, se ele já
// apareceu ou não tem data-reveal. Usado para criar os gráficos só quando são vistos (a animação
// de entrada do Chart.js acontece na frente do usuário).
function onReveal(el, fn) {
    const box = el.closest('[data-reveal]');
    if (!box || box.classList.contains('in')) { fn(); return; }
    (box._onReveal = box._onReveal || []).push(fn);
}

// O bloco só aparece quando já subiu REVEAL_MARGIN acima do rodapé da tela (senão a animação
// acontece fora da vista, colada no rodapé). Os que entram juntos (cartões, linhas) aparecem em
// sequência: cada um recebe --stagger, o atraso usado pelas animações dele e de dentro dele.
// data-reveal="late" (os quadros dos gráficos) espera um pouco mais: o gráfico é criado quando o
// quadro sobe REVEAL_LATE_MARGIN acima do rodapé, para a animação acontecer à vista. O quadro não
// some enquanto espera (CSS): a entrada é a animação do próprio gráfico.
const REVEAL_MARGIN = '0px 0px -20% 0px';
const REVEAL_LATE_MARGIN = '0px 0px -25% 0px';
const REVEAL_STAGGER_MS = 90;
let revealLateObserver = null;

// Blocos à espera de aparecer. Perto do fim da página não há mais para onde rolar: os últimos
// nunca sobem a margem acima do rodapé; ao chegar ao fim, todo bloco já visível aparece
// (revealAtBottom).
const revealPending = new Set();

function revealAtBottom() {
    if (!revealPending.size) return;
    const end = document.documentElement.scrollHeight - window.innerHeight - window.scrollY;
    if (end > 4) return;
    let k = 0;
    for (const el of [...revealPending]) {
        if (!el.isConnected) { revealPending.delete(el); continue; }
        const r = el.getBoundingClientRect();
        if (r.height === 0 || r.top >= window.innerHeight || r.bottom <= 0) continue;
        revealObserver?.unobserve(el);
        revealLateObserver?.unobserve(el);
        el.style.setProperty('--stagger', `${k++ * REVEAL_STAGGER_MS}ms`);
        revealNow(el);
    }
}
let revealBottomFrame = 0;
const scheduleRevealAtBottom = () => { cancelAnimationFrame(revealBottomFrame); revealBottomFrame = requestAnimationFrame(revealAtBottom); };
window.addEventListener('scroll', scheduleRevealAtBottom, { passive: true });
window.addEventListener('resize', scheduleRevealAtBottom, { passive: true });

function revealWatcher(margin) {
    const observer = new IntersectionObserver((entries) => {
        entries.filter(entry => entry.isIntersecting).forEach((entry, k) => {
            observer.unobserve(entry.target);
            if (performance.now() < PAGE_INTRO_MS) entry.target.classList.add('instant');
            entry.target.style.setProperty('--stagger', `${k * REVEAL_STAGGER_MS}ms`);
            revealNow(entry.target);
        });
    }, { rootMargin: margin });
    return observer;
}

// O que já está à vista quando a página abre aparece na hora, sem a entrada (classe .instant):
// a página nova já chega com a transição entre páginas, e somar as duas parecia uma piscada. Os
// efeitos de dentro (contadores, barras) continuam. Vale no primeiro PAGE_INTRO_MS da página,
// também para o que aparece nesse tempo (dados que chegam rápido, sub-aba mostrada na abertura).
const PAGE_INTRO_MS = 1200;
const inViewNow = (el) => {
    if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') return false; // oculto
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < window.innerHeight * 0.8;
};

function observeReveal(root = document) {
    let els = [...root.querySelectorAll('[data-reveal]:not(.in)')];
    if (!window.IntersectionObserver || REDUCED_MOTION.matches) { els.forEach(revealNow); return; }
    if (performance.now() < PAGE_INTRO_MS) {
        const now = els.filter(inViewNow);
        now.forEach(el => { el.classList.add('instant'); revealNow(el); });
        els = els.filter(el => !el.classList.contains('in'));
    }
    revealObserver = revealObserver || revealWatcher(REVEAL_MARGIN);
    revealLateObserver = revealLateObserver || revealWatcher(REVEAL_LATE_MARGIN);
    els.forEach(el => { revealPending.add(el); (el.dataset.reveal === 'late' ? revealLateObserver : revealObserver).observe(el); });
    scheduleRevealAtBottom(); // uma página curta (ou já rolada até o fim) não tem rolagem para disparar
}

// Um resultado novo no mesmo lugar (ex.: rodar o backtest de novo): os blocos de root voltam a
// esperar a tela e entram de novo ao aparecer.
function replayReveal(root) {
    root.querySelectorAll('[data-reveal]').forEach(el => {
        el.classList.remove('in', 'instant');
        el._onReveal = [];
        revealObserver?.unobserve(el);
        revealLateObserver?.unobserve(el);
    });
    observeReveal(root);
}

// Itens de uma lista redesenhada por innerHTML (linhas de tabela, cartões): enquanto nenhum
// apareceu, entram um a um ao rolar (em cascata quando entram juntos); depois que a lista já
// apareceu, cada redesenho (ex.: a cada tecla na calculadora de aporte, um lançamento novo)
// mostra os itens direto (.instant), sem repetir a entrada.
function revealItems(container, selector = ':scope > tr') {
    const items = [...container.querySelectorAll(selector)].filter(el => !el.querySelector(':scope > .empty-row'));
    for (const el of items) {
        el.setAttribute('data-reveal', '');
        el._revealHost = container;
        if (container._itemsSeen) { el.classList.add('instant'); revealNow(el); } // contadores de dentro também
    }
    if (!container._itemsSeen) observeReveal(container);
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
    brl: (v) => fmtBRL(v),
    brl0: (v) => `R$ ${fmtNum(v, 0)}`,
    sbrl: (v) => `${v > 0.004 ? '+' : ''}${fmtBRL(v)}`,
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

// ---------------------------------------------------------------- Minigráfico

// Minigráfico de área (svg viewBox 0 0 200 48) de uma série de valores, verde quando up e
// vermelho senão; a linha se desenha da esquerda para a direita (CSS: spark-line).
function drawSpark(svg, values, up) {
    if (!values || values.length < 2) { svg.innerHTML = ''; return; }
    const min = Math.min(...values), span = (Math.max(...values) - min) || 1;
    const pts = values.map((c, k) => `${(k / (values.length - 1) * 200).toFixed(1)},${(45 - (c - min) / span * 41).toFixed(1)}`);
    const color = up ? 'var(--success)' : 'var(--danger)';
    const id = `sparkFill-${svg.id || Math.random().toString(36).slice(2)}`;
    svg.innerHTML = `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" style="stop-color: ${color}; stop-opacity: 0.35"/><stop offset="1" style="stop-color: ${color}; stop-opacity: 0"/></linearGradient></defs>
        <path class="spark-area" d="M0,48 L${pts.join(' L')} L200,48 Z" fill="url(#${id})"/>
        <path class="spark-line" d="M${pts.join(' L')}" pathLength="100" style="stroke: ${color}"/>`;
}

// ---------------------------------------------------------------- Tema dos gráficos (Chart.js)

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
// Visual comum dos gráficos (Carteira, Backtest): grade, tooltip escuro e animação de entrada.
const CHART_GRID = 'rgba(120, 150, 210, 0.1)';
const CHART_ENTER = { duration: 900, easing: 'easeOutQuart' };
const chartTooltip = () => ({
    backgroundColor: 'rgba(10, 16, 32, 0.95)', borderColor: cssVar('--border-strong'), borderWidth: 1,
    titleColor: cssVar('--text-color'), bodyColor: cssVar('--text-color'), padding: 12, cornerRadius: 10,
    titleFont: { weight: '700' }, caretSize: 6,
});
// Preenchimento em degradê vertical (cor cheia em cima, alpha embaixo) para barras e áreas.
const verticalGradient = (color, topAlpha = 1, bottomAlpha = 0.45) => (ctx) => {
    const { chart } = ctx;
    if (!chart.chartArea) return color;
    const { top, bottom } = chart.chartArea;
    const g = chart.ctx.createLinearGradient(0, top, 0, bottom);
    g.addColorStop(0, hexAlpha(color, topAlpha));
    g.addColorStop(1, hexAlpha(color, bottomAlpha));
    return g;
};
// Mesmo degradê, na horizontal (barras deitadas): mais transparente à esquerda.
const horizontalGradient = (color, leftAlpha = 0.5, rightAlpha = 1) => (ctx) => {
    const { chart } = ctx;
    if (!chart.chartArea) return color;
    const { left, right } = chart.chartArea;
    const g = chart.ctx.createLinearGradient(left, 0, right, 0);
    g.addColorStop(0, hexAlpha(color, leftAlpha));
    g.addColorStop(1, hexAlpha(color, rightAlpha));
    return g;
};
const hexAlpha = (hex, a) => {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

// ---------------------------------------------------------------- Gráfico de preço (Lightweight Charts)
// Usado pelo Backtest e pelo card "Operações no gráfico" da Carteira (páginas que carregam o
// Lightweight Charts). Cores sobre o fundo #0e1527 do painel; médias como no TradingView do
// usuário: MM20 laranja, MM50 azul, MM200 roxa.

const COLORS = {
    up: '#3ddc97', down: '#ff6b6b', close: '#dce5f5',
    mm20: '#ffb057', mm50: '#5aa2ff', mm200: '#a78bfa',
    buy: '#3ddc97', partial: '#ffc65c', exit: '#ff6b6b', target: '#ffc65c',
    profit: '#5aa2ff', profitB: '#ffb057', zero: '#8a99b6',
    grid: 'rgba(120, 150, 210, 0.08)', border: '#1f2b46', text: '#dce5f5', panel: '#0e1527', crosshair: '#5aa2ff'
};
const MA_LABEL = { mm20: 'MM20', mm50: 'MM50', mm200: 'MM200' };
// Marcador de cada evento (o rótulo, ex. "C3" ou "V1–4", vem pronto do servidor). sell = venda
// lançada na carteira.
const MARKER = {
    buy: { position: 'belowBar', shape: 'arrowUp', color: COLORS.buy },
    partial: { position: 'aboveBar', shape: 'circle', color: COLORS.partial },
    exit: { position: 'aboveBar', shape: 'arrowDown', color: COLORS.exit },
    sell: { position: 'aboveBar', shape: 'arrowDown', color: COLORS.exit }
};
const CHART_KEY = 'marketpressure_backtest_grafico'; // "candles" | "line" (Backtest e Carteira)

// O tamanho vem do contêiner (.bt-chart no CSS): autoSize acompanha a largura.
function chartOptions() {
    const line = { color: COLORS.crosshair, width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: '#2f6bff' };
    return {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#8a99b6', fontSize: 12,
                  fontFamily: getComputedStyle(document.body).fontFamily },
        grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: line, horzLine: line },
        localization: { locale: 'pt-BR', priceFormatter: (p) => fmtNum(p, 2) },
        autoSize: true
    };
}

// Gráfico de preço com candles, linha de fechamento e as médias (só uma das duas séries de preço
// fica visível). Devolve o gráfico e as séries.
function priceChart(el, d) {
    const chart = LightweightCharts.createChart(el, chartOptions());
    const candles = chart.addCandlestickSeries({
        upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down
    });
    candles.setData(d.candles);
    const line = chart.addLineSeries({ color: COLORS.close, lineWidth: 2, priceLineVisible: false });
    line.setData(d.candles.map(c => ({ time: c.time, value: c.close })));
    for (const k of Object.keys(MA_LABEL)) {
        const ma = chart.addLineSeries({ color: COLORS[k], lineWidth: k === 'mm200' ? 2 : 1, priceLineVisible: false,
                                         lastValueVisible: false, crosshairMarkerVisible: false });
        ma.setData(d.ma[k]);
    }
    return { chart, candles, line };
}

// Candles (type "candles") ou linha; os marcadores ficam na série visível.
function showPriceType(series, markers, type) {
    if (!series?.candles) return;
    const candles = type === 'candles';
    series.candles.applyOptions({ visible: candles });
    series.line.applyOptions({ visible: !candles });
    const list = (markers || []).map(m => ({ time: m.time, text: m.text, ...MARKER[m.kind] }));
    series.candles.setMarkers(candles ? list : []);
    series.line.setMarkers(candles ? [] : list);
}

// Legenda das médias e dos marcadores: items = [[cor, texto], ...] depois das médias.
function priceLegend(items) {
    return [
        ...Object.entries(MA_LABEL).map(([k, label]) => `<span><i class="swatch-line" style="background:${COLORS[k]}"></i>${label}</span>`),
        ...items.map(([color, text]) => `<span><i class="swatch-dot" style="background:${color}"></i>${text}</span>`)
    ].join('');
}
