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
//
// O token vai na URL da Bot API, então ela nunca vai para o log nem para a resposta.

const API = "https://api.telegram.org/bot";
const MAX_TEXT = 4096;
const CHAT_ID = /^-?\d{5,20}$/;
const TABLE = "settings_telegram";
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
        return CHAT_ID.test(chatId) ? { enabled: row.enabled === true, chatId } : null;
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

// user: o usuário logado, ou {service: true} para o token de serviço.
export async function handleNotify(request, url, env, user) {
    const route = `${request.method} ${url.pathname}`;
    if (user.service) {
        return route === "POST /notify/send" ? send(request, env) : json(403, { message: "não permitido" });
    }
    if (route === "GET /notify/info") return info(env);
    if (route === "POST /notify/test") return test(env, user);
    if (url.pathname === "/notify/send") return json(403, { message: "só a atualização diária envia a todos" });
    return json(404, { message: "não encontrado" });
}
