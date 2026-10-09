// Base comum das abas Screener (app.js) e Carteira (carteira.js): acesso à API do server.py
// e formatadores de tela. Script clássico, carregado antes do script da página.
// Todos os cálculos (indicadores, setup, ranking, carteira, aporte) ficam no servidor, em
// Python; aqui só se busca o resultado pronto e se exibe.

// Se a página for aberta por outro servidor (ex.: Live Server), as chamadas vão para a porta
// padrão do server.py.
const SERVER_PORT = "8765";
const API = location.port === SERVER_PORT ? "/api" : `http://127.0.0.1:${SERVER_PORT}/api`;

const MODE_KEY = "marketpressure_base_mode"; // base "fechamento" | "intradiario", comum às duas abas
const OFFLINE = 'servidor local fora do ar — rode "python server.py" na pasta do projeto';

// ---------------------------------------------------------------- API

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// Erro da API com o corpo da resposta (ex.: erros por linha da lista de compras).
// status 400 = a ação foi recusada pela validação (nada foi gravado).
class ApiError extends Error {
    constructor(message, status, data = {}) { super(message); this.status = status; this.data = data; }
}

async function apiGet(path, retries = 2) {
    let response;
    try {
        response = await fetch(`${API}${path}`);
    } catch (e) {
        if (retries > 0) {
            await sleep(1000);
            return apiGet(path, retries - 1);
        }
        throw new Error(OFFLINE);
    }
    if ((response.status === 429 || response.status === 502) && retries > 0) {
        await sleep(1500);
        return apiGet(path, retries - 1);
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(data.message || `HTTP ${response.status}`, response.status, data);
    return data;
}

// POST com corpo JSON (sem novas tentativas: pode gravar).
async function apiPost(path, body) {
    let response;
    try {
        response = await fetch(`${API}${path}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
        });
    } catch (e) {
        throw new Error(OFFLINE);
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(data.message || `HTTP ${response.status}`, response.status, data);
    return data;
}

// ---------------------------------------------------------------- Formatadores

const fmtBRL = (v) => `R$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtNum = (v, d = 1) => v.toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (v, d = 1) => `${v >= 0 ? '+' : ''}${fmtNum(v, d)}%`;
const fmtDate = (iso) => iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '—';
const escapeHTML = (s) => String(s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
