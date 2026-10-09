// Base comum das abas Screener (app.js) e Carteira (carteira.js): acesso à API e formatadores
// de tela. Script clássico, carregado antes do script da página.
// Todos os cálculos (indicadores, setup, ranking, carteira, aporte) ficam no Python; aqui só
// se busca o resultado pronto e se exibe.
//
// Onde o Python roda:
// - local: no server.py. Se a página for aberta por outro servidor local (ex.: Live Server),
//   as chamadas vão para a porta padrão do server.py;
// - versão publicada (Cloudflare) e wrangler dev (porta 8787): no próprio navegador (Pyodide,
//   num Web Worker, assets/js/py-worker.js), com as mesmas rotas /api.
const SERVER_PORT = "8765";
const API = location.port === SERVER_PORT ? "/api" : `http://127.0.0.1:${SERVER_PORT}/api`;
const IN_BROWSER = !/^(localhost|127\.0\.0\.1)$/.test(location.hostname) || location.port === "8787";

const MODE_KEY = "marketpressure_base_mode"; // base "fechamento" | "intradiario", comum às duas abas
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

// {status, data} do pedido; erro de rede só no modo local (servidor fora do ar).
async function send(method, path, body) {
    if (IN_BROWSER) return pyCall(method, path, body);
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

// ---------------------------------------------------------------- Formatadores

const fmtBRL = (v) => `R$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtNum = (v, d = 1) => v.toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (v, d = 1) => `${v >= 0 ? '+' : ''}${fmtNum(v, d)}%`;
const fmtDate = (iso) => iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '—';
const escapeHTML = (s) => String(s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
