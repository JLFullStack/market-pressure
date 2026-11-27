// Notificações pelo Telegram na versão publicada. O token do bot (segredo TELEGRAM_BOT_TOKEN) só
// existe aqui: o Python roda no navegador e nunca o vê. Cada usuário guarda o próprio chat na
// tabela settings_telegram (dele, como as carteiras: "{id}/settings_telegram" em mp_tables,
// gravada pelo Python, marketpressure/configuracoes/notificacoes.py). Rotas:
//
//   GET  /notify/info   (usuário)          {configured, bot}: se há bot e o @nome dele
//   POST /notify/test   (usuário)          mensagem de teste para o chat salvo DESTE usuário
//                                          (nunca para um chat vindo do pedido); até TEST_LIMIT
//                                          por TEST_WINDOW_MINUTES
//   POST /notify/send   (token de serviço)  {text}: envia o texto a todos os usuários com o aviso
//                                          ligado (o aviso diário do ranking, update.py)
//   GET  /notify/watchlists (token de serviço) só as listas de favoritos ({ticker, market}) e o que já
//                                          foi avisado delas, de quem ligou o aviso dos favoritos;
//                                          nada de carteiras e nem o número do chat
//   POST /notify/user   (token de serviço)  {user, text, state}: envia o texto ao chat salvo DESSE
//                                          usuário (com o aviso dos favoritos ligado) e, se chegou,
//                                          grava state em "{user}/watchlist_alerts" (a única tabela
//                                          de usuário que o serviço grava, e só por aqui); sem
//                                          texto, só grava o estado (dia sem novidade)
//
// O token vai na URL da Bot API, então ela nunca vai para o log nem para a resposta.

const API = "https://api.telegram.org/bot";
const MAX_TEXT = 4096;
const CHAT_ID = /^-?\d{5,20}$/;
const TABLE = "settings_telegram";
const WATCHLIST = "watchlist";              // favoritos do usuário (carteira/favoritos.py)
const WATCH_STATE = "watchlist_alerts";     // o que já foi avisado de cada favorito
const TICKER = /^[A-Z0-9]{1,12}$/;
const MARKETS = new Set(["b3", "us", "crypto"]);
const STATUS = /^[a-z]{1,20}$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const MAX_STATE = 100;
const TEST_LIMIT = 5;
const TEST_WINDOW_MINUTES = 15;
const BOT_INFO_MS = 3600 * 1000;
export const TEST_TEXT = "MarketPressure: mensagem de teste. Se ela chegou, as notificações pelo Telegram estão "
    + "configuradas para a sua conta.";

let botInfo = null;   // {name, at}: o @nome do bot (getMe), guardado por uma hora neste isolate

function json(status, body) {
    return new Response(JSON.stringify(body), {
        status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
    });
}

async function telegram(env, method, body) {
    try {
        const response = await fetch(`${API}${env.TELEGRAM_BOT_TOKEN}/${method}`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
        });
        const data = await response.json().catch(() => ({}));
        return { ok: response.ok && data.ok === true, result: data.result, description: data.description || `HTTP ${response.status}` };
    } catch {
        return { ok: false, description: "sem resposta do Telegram" };
    }
}

const sendMessage = (env, chatId, text) =>
    telegram(env, "sendMessage", { chat_id: chatId, text, disable_web_page_preview: true });

// A configuração gravada pelo Python: [{enabled, chat_id}] (texto JSON da tabela) ou null.
function parseSettings(rows) {
    try {
        const row = JSON.parse(rows)?.[0];
        const chatId = String(row?.chat_id ?? "");
        return CHAT_ID.test(chatId) ? { enabled: row.enabled === true, favorites: row.favorites === true, chatId } : null;
    } catch {
        return null;
    }
}

async function info(env) {
    if (!env.TELEGRAM_BOT_TOKEN) return json(200, { configured: false, bot: null });
    if (!botInfo || Date.now() - botInfo.at > BOT_INFO_MS) {
        const me = await telegram(env, "getMe");
        if (me.ok) botInfo = { name: me.result?.username || null, at: Date.now() };
    }
    return json(200, { configured: true, bot: botInfo?.name || null });
}

// Limite de testes por usuário (reaproveita mp_login_attempts, com a chave "telegram:{id}").
async function overLimit(env, userId) {
    const key = `telegram:${userId}`;
    const now = Date.now();
    const row = await env.DB.prepare("SELECT failures, first_at FROM mp_login_attempts WHERE key = ?").bind(key).first();
    const fresh = row && now - row.first_at < TEST_WINDOW_MINUTES * 60000;
    if (fresh && row.failures >= TEST_LIMIT) return true;
    await (fresh
        ? env.DB.prepare("UPDATE mp_login_attempts SET failures = failures + 1 WHERE key = ?").bind(key)
        : env.DB.prepare("INSERT OR REPLACE INTO mp_login_attempts (key, failures, first_at) VALUES (?, 1, ?)").bind(key, now)).run();
    return false;
}

async function test(env, user) {
    if (!env.TELEGRAM_BOT_TOKEN) return json(503, { ok: false, description: "bot não configurado" });
    const row = await env.DB.prepare("SELECT rows FROM mp_tables WHERE name = ?").bind(`${user.id}/${TABLE}`).first();
    const settings = row && parseSettings(row.rows);
    if (!settings) return json(400, { ok: false, description: "nenhum chat salvo" });
    if (await overLimit(env, user.id)) {
        return json(429, { ok: false, description: `limite de ${TEST_LIMIT} testes a cada ${TEST_WINDOW_MINUTES} minutos` });
    }
    const sent = await sendMessage(env, settings.chatId, TEST_TEXT);
    return json(sent.ok ? 200 : 502, sent.ok ? { ok: true } : { ok: false, description: sent.description });
}

async function send(request, env) {
    if (!env.TELEGRAM_BOT_TOKEN) return json(200, { configured: false, recipients: 0, sent: 0, failed: 0 });
    const body = await request.json().catch(() => null);
    const text = typeof body?.text === "string" ? body.text : "";
    if (!text || text.length > MAX_TEXT) return json(400, { message: `texto de 1 a ${MAX_TEXT} caracteres` });
    const { results } = await env.DB.prepare("SELECT rows FROM mp_tables WHERE name LIKE ?").bind(`%/${TABLE}`).all();
    const chats = [...new Set(results.map(r => parseSettings(r.rows)).filter(s => s?.enabled).map(s => s.chatId))];
    let sent = 0;
    for (const chatId of chats) {
        if ((await sendMessage(env, chatId, text)).ok) sent++;
    }
    return json(200, { configured: true, recipients: chats.length, sent, failed: chats.length - sent });
}

// Linhas de uma tabela de usuário (texto JSON) → só os campos esperados, validados.
function parseRows(text, keep) {
    try {
        const rows = JSON.parse(text || "[]");
        return Array.isArray(rows) ? rows.map(keep).filter(Boolean) : [];
    } catch {
        return [];
    }
}
const favoriteRow = (r) => TICKER.test(String(r?.ticker ?? "")) && MARKETS.has(r?.market || "b3")
    ? { ticker: r.ticker, market: r.market || "b3" } : null;
const stateRow = (r) => favoriteRow(r) && STATUS.test(String(r?.status ?? "")) && DAY.test(String(r?.ref_date ?? ""))
    ? { ticker: r.ticker, market: r.market || "b3", status: r.status, ref_date: r.ref_date } : null;

// Quem ligou o aviso dos favoritos (com chat salvo): o id da conta, a lista e o que já foi avisado.
async function watchlists(env) {
    if (!env.TELEGRAM_BOT_TOKEN) return json(200, { configured: false, users: [] });
    const { results } = await env.DB.prepare("SELECT name, rows FROM mp_tables WHERE name LIKE ?").bind(`%/${TABLE}`).all();
    const ids = results.filter(r => parseSettings(r.rows)?.favorites).map(r => r.name.slice(0, -TABLE.length - 1));
    const users = [];
    for (const id of ids) {
        const names = [`${id}/${WATCHLIST}`, `${id}/${WATCH_STATE}`];
        const rows = (await env.DB.prepare("SELECT name, rows FROM mp_tables WHERE name IN (?, ?)").bind(...names).all()).results;
        const text = (name) => rows.find(r => r.name === name)?.rows;
        const list = parseRows(text(names[0]), favoriteRow);
        if (list.length) users.push({ user: id, list, state: parseRows(text(names[1]), stateRow) });
    }
    return json(200, { configured: true, users });
}

// Envia ao chat salvo de um usuário (com o aviso dos favoritos ligado) e grava o que foi avisado.
async function sendUser(request, env) {
    if (!env.TELEGRAM_BOT_TOKEN) return json(503, { ok: false, description: "bot não configurado" });
    const body = await request.json().catch(() => null);
    const user = typeof body?.user === "string" ? body.user : "";
    const text = typeof body?.text === "string" ? body.text : "";
    if (!user || user.includes("/") || text.length > MAX_TEXT || (!text && !Array.isArray(body?.state))) {
        return json(400, { ok: false, description: `usuário e texto de até ${MAX_TEXT} caracteres (ou só o estado)` });
    }
    if (!(await env.DB.prepare("SELECT id FROM mp_users WHERE id = ?").bind(user).first())) {
        return json(404, { ok: false, description: "usuário não encontrado" });
    }
    const row = await env.DB.prepare("SELECT rows FROM mp_tables WHERE name = ?").bind(`${user}/${TABLE}`).first();
    const settings = row && parseSettings(row.rows);
    if (!settings?.favorites) return json(409, { ok: false, description: "aviso dos favoritos desligado" });
    if (text) {
        const sent = await sendMessage(env, settings.chatId, text);
        if (!sent.ok) return json(502, { ok: false, description: sent.description });
    }
    if (Array.isArray(body.state)) {
        const state = body.state.slice(0, MAX_STATE).map(stateRow).filter(Boolean);
        await env.DB.prepare("INSERT INTO mp_tables (name, rows, updated_at) VALUES (?, ?, ?) "
            + "ON CONFLICT(name) DO UPDATE SET rows = excluded.rows, updated_at = excluded.updated_at")
            .bind(`${user}/${WATCH_STATE}`, JSON.stringify(state), Date.now()).run();
    }
    return json(200, { ok: true });
}

// user: o usuário logado, ou {service: true} para o token de serviço.
export async function handleNotify(request, url, env, user) {
    const route = `${request.method} ${url.pathname}`;
    if (user.service) {
        if (route === "POST /notify/send") return send(request, env);
        if (route === "GET /notify/watchlists") return watchlists(env);
        if (route === "POST /notify/user") return sendUser(request, env);
        return json(403, { message: "não permitido" });
    }
    if (route === "GET /notify/info") return info(env);
    if (route === "POST /notify/test") return test(env, user);
    if (["/notify/send", "/notify/watchlists", "/notify/user"].includes(url.pathname)) {
        return json(403, { message: "só a atualização diária usa esta rota" });
    }
    return json(404, { message: "não encontrado" });
}
