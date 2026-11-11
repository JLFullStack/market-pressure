// Efeitos visuais comuns (design: animacao.css): pílula deslizante dos grupos de abas e botões,
// entrada ao rolar (data-reveal), contadores e o minigráfico. Só transform/opacity e traços SVG;
// prefers-reduced-motion desliga. Script clássico, carregado depois de core.js.

//#region Movimento reduzido
const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');
//#endregion

//#region Pílula deslizante
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
//#endregion

//#region Entrada ao rolar
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
//#endregion

//#region Contadores
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
//#endregion

//#region Minigráfico
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
//#endregion
