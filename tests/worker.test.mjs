// Permissões do Worker da versão publicada: quem pode o quê, pelas rotas reais de worker/index.js e
// worker/auth.js, com um D1 em memória (node:sqlite) e sem rede. Rodar: npm run test:worker
import assert from "node:assert/strict";
import { before, beforeEach, test } from "node:test";
import { DatabaseSync } from "node:sqlite";
import worker from "../worker/index.js";
import { hashPassword } from "../worker/auth.js";

// O Node não tem este método do Cloudflare (usado no token de serviço).
crypto.subtle.timingSafeEqual = (a, b) => Buffer.from(a).equals(Buffer.from(b));

const ORIGIN = "https://site.test";
const PASSWORD = "senha-de-teste-1";
const SERVICE_TOKEN = "token-de-servico";

// D1 mínimo sobre SQLite: prepare/bind/first/all/run e batch em transação.
function fakeD1() {
    const sqlite = new DatabaseSync(":memory:");
    const statement = (sql, params = []) => ({
        bind: (...values) => statement(sql, values),
        first: async () => sqlite.prepare(sql).get(...params) ?? null,
        all: async () => ({ results: sqlite.prepare(sql).all(...params) }),
        run: async () => sqlite.prepare(sql).run(...params),
        exec: () => sqlite.prepare(sql).run(...params),
    });
    return {
        sqlite,
        prepare: (sql) => statement(sql),
        batch: async (list) => {
            sqlite.exec("BEGIN");
            try {
                for (const s of list) s.exec();
                sqlite.exec("COMMIT");
            } catch (err) {
                sqlite.exec("ROLLBACK");
                throw err;
            }
            return [];
        },
    };
}

const db = fakeD1();
const env = {
    DB: db, SERVICE_TOKEN, BRAPI_TOKEN: "segredo-brapi",
    ASSETS: { fetch: async () => new Response("arquivo", { status: 200 }) },
};
const upstream = [];  // pedidos que saíram para as fontes externas

globalThis.fetch = async (target, init = {}) => {
    upstream.push({ url: String(target), method: init.method || "GET" });
    if (String(target).startsWith("https://fc.yahoo.com")) return new Response("", { headers: { "Set-Cookie": "A=1; Path=/" } });
    if (String(target).includes("getcrumb")) return new Response("miolo");
    return new Response('{"ok":true}', { headers: { "Content-Type": "application/json" } });
};

async function call(method, path, { cookie, bearer, body, origin, accept } = {}) {
    const headers = {};
    if (cookie) headers.Cookie = cookie;
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    if (origin) headers.Origin = origin;
    if (accept) headers.Accept = accept;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    return worker.fetch(new Request(ORIGIN + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }), env);
}

const put = (who, tables) => call("PUT", "/db", { ...who, body: { backup: false, tables } });
const stored = (name) => db.sqlite.prepare("SELECT rows FROM mp_tables WHERE name = ?").get(name)?.rows;
const proxyPath = (url) => `/proxy?url=${encodeURIComponent(url)}`;
const BRAPI = "https://brapi.dev/api/quote/list?limit=1";
const YAHOO_LIST = "https://query1.finance.yahoo.com/v1/finance/screener";
const YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/PETR4.SA?range=5d";

const ids = {};
const admin = {};
const alice = {};
const bob = {};

async function addUser(key, email, cpf, isAdmin) {
    ids[key] = crypto.randomUUID();
    db.sqlite.prepare("INSERT INTO mp_users (id, name, cpf, email, password_hash, admin, created_at) VALUES (?, ?, ?, ?, ?, ?, 0)")
        .run(ids[key], key, cpf, email, await hashPassword(PASSWORD), isAdmin ? 1 : 0);
    const response = await call("POST", "/auth/login", { body: { login: email, password: PASSWORD } });
    assert.equal(response.status, 200);
    return { cookie: response.headers.get("Set-Cookie").split(";")[0] };
}

before(async () => {
    await call("GET", "/login");   // cria as tabelas (ensureSchema)
    Object.assign(admin, await addUser("admin", "admin@teste.com", "11144477735", true));
    Object.assign(alice, await addUser("alice", "alice@teste.com", "52998224725", false));
    Object.assign(bob, await addUser("bob", "bob@teste.com", "39053344705", false));
});

beforeEach(() => { upstream.length = 0; });

// ---------------------------------------------------------------- Sem login

test("sem login nada é servido, além da tela de login", async () => {
    for (const path of ["/db?names=scan", proxyPath(YAHOO_CHART), "/py/manifest.json", "/pyodide/pyodide.mjs", "/assets/js/app.js", "/auth/users", "/auth/me"]) {
        assert.equal((await call("GET", path)).status, 401, path);
    }
    assert.equal((await put({}, { scan: "[]" })).status, 401);
    const page = await call("GET", "/carteira", { accept: "text/html" });
    assert.equal(page.status, 302);
    assert.match(page.headers.get("Location"), /\/login\?next=/);
    assert.equal((await call("GET", "/login")).status, 200);
});

test("cookie inventado não abre sessão", async () => {
    assert.equal((await call("GET", "/auth/me", { cookie: "mp_session=inventado" })).status, 401);
});

test("outro site não envia pedidos em nome de quem está logado", async () => {
    const response = await call("PUT", "/db", { ...admin, origin: "https://ruim.test", body: { tables: { scan: "[]" } } });
    assert.equal(response.status, 403);
});

// ---------------------------------------------------------------- Contas

test("usuário comum não gerencia contas", async () => {
    assert.equal((await call("GET", "/auth/users", alice)).status, 403);
    assert.equal((await call("POST", "/auth/users", { ...alice, body: { name: "X", cpf: "16899535009", email: "x@teste.com", password: PASSWORD } })).status, 403);
    assert.equal((await call("POST", "/auth/users/password", { ...alice, body: { id: ids.admin, password: "outra-senha-99" } })).status, 403);
    assert.equal((await call("DELETE", `/auth/users?id=${ids.bob}`, alice)).status, 403);
    assert.ok(db.sqlite.prepare("SELECT 1 FROM mp_users WHERE id = ?").get(ids.bob));
    assert.equal(db.sqlite.prepare("SELECT 1 FROM mp_users WHERE email = 'x@teste.com'").get(), undefined);
});

test("/auth/me informa o perfil de cada um", async () => {
    assert.equal((await (await call("GET", "/auth/me", admin)).json()).admin, true);
    assert.equal((await (await call("GET", "/auth/me", alice)).json()).admin, false);
});

test("administrador gerencia contas, e a conta criada nunca é administradora", async () => {
    const list = await (await call("GET", "/auth/users", admin)).json();
    assert.ok(list.users.some(u => u.id === ids.alice));
    assert.ok(list.users.every(u => !("password_hash" in u)));
    const body = { name: "Nova", cpf: "16899535009", email: "nova@teste.com", password: PASSWORD, admin: true };
    assert.equal((await call("POST", "/auth/users", { ...admin, body })).status, 200);
    const row = db.sqlite.prepare("SELECT id, admin FROM mp_users WHERE email = 'nova@teste.com'").get();
    assert.equal(row.admin, 0);
    assert.equal((await call("DELETE", `/auth/users?id=${row.id}`, admin)).status, 200);
});

test("a conta do administrador não pode ser excluída", async () => {
    assert.equal((await call("DELETE", `/auth/users?id=${ids.admin}`, admin)).status, 400);
    assert.ok(db.sqlite.prepare("SELECT 1 FROM mp_users WHERE id = ?").get(ids.admin));
});

test("excluir uma conta apaga só os dados dela", async () => {
    const carol = await addUser("carol", "carol@teste.com", "71428793860", false);
    await put(carol, { portfolios: '[{"id":"c"}]' });
    await put(bob, { portfolios: '[{"id":"b"}]' });
    assert.equal((await call("DELETE", `/auth/users?id=${ids.carol}`, admin)).status, 200);
    assert.equal(stored(`${ids.carol}/portfolios`), undefined);
    assert.equal(stored(`${ids.bob}/portfolios`), '[{"id":"b"}]');
    assert.equal((await call("GET", "/auth/me", carol)).status, 401);   // a sessão também acabou
});

// ---------------------------------------------------------------- Tabelas comuns

test("usuário comum lê as tabelas comuns, mas não grava", async () => {
    assert.equal((await put(admin, { scan: '[{"version":1}]' })).status, 200);
    const read = await call("GET", "/db?names=scan", alice);
    assert.deepEqual(await read.json(), { scan: [{ version: 1 }] });
    for (const name of ["scan", "scan_analyses", "scan_failures", "universe", "history_b3_petr4", "history_novo"]) {
        assert.equal((await put(alice, { [name]: "[]" })).status, 403, name);
    }
    assert.equal(stored("scan"), '[{"version":1}]');
    assert.equal(stored("history_novo"), undefined);
});

test("um pedido com tabela comum e própria não grava nenhuma", async () => {
    assert.equal((await put(alice, { portfolios: '[{"id":"misto"}]', universe: "[]" })).status, 403);
    assert.notEqual(stored(`${ids.alice}/portfolios`), '[{"id":"misto"}]');
});

test("administrador e token de serviço gravam as comuns", async () => {
    assert.equal((await put(admin, { history_b3_vale3: '[{"a":1}]' })).status, 200);
    assert.equal((await put({ bearer: SERVICE_TOKEN }, { universe: '[{"b":2}]' })).status, 200);
    assert.equal(stored("history_b3_vale3"), '[{"a":1}]');
    assert.equal(stored("universe"), '[{"b":2}]');
});

// ---------------------------------------------------------------- Tabelas de cada usuário

test("cada usuário só vê as próprias tabelas, mesmo o administrador", async () => {
    await put(alice, { transactions: '[{"dono":"alice"}]' });
    await put(bob, { transactions: '[{"dono":"bob"}]' });
    const seen = async (who) => (await (await call("GET", "/db?names=transactions", who)).json()).transactions;
    assert.deepEqual(await seen(alice), [{ dono: "alice" }]);
    assert.deepEqual(await seen(bob), [{ dono: "bob" }]);
    assert.equal(await seen(admin), null);
});

test("nome de tabela não alcança a tabela de outro usuário nem as do sistema", async () => {
    for (const name of [`${ids.bob}/transactions`, "../transactions", "mp_users;--", "a,b/c"]) {
        const read = await call("GET", `/db?names=${encodeURIComponent(name)}`, alice);
        assert.equal(read.status, 400, name);
        assert.equal((await put(alice, { [name]: "[]" })).status, 400, name);
    }
    // "mp_users" é um nome válido, mas vira uma tabela da própria conta, não a do sistema.
    const read = await (await call("GET", "/db?names=mp_users", alice)).json();
    assert.equal(read.mp_users, null);
});

// ---------------------------------------------------------------- Token de serviço

test("o token de serviço não alcança dados de usuários nem contas", async () => {
    const service = { bearer: SERVICE_TOKEN };
    assert.equal((await put(service, { portfolios: "[]" })).status, 403);
    assert.equal((await call("GET", "/db?names=portfolios", service)).status, 403);
    assert.equal((await call("GET", "/auth/users", service)).status, 401);
    assert.equal((await call("GET", "/py/manifest.json", service)).status, 404);
    assert.equal((await call("GET", "/db?names=scan", { bearer: "token-errado" })).status, 401);
});

// ---------------------------------------------------------------- Fontes externas

test("usuário comum busca candles, mas não a lista de ativos nem a brapi", async () => {
    assert.equal((await call("GET", proxyPath(YAHOO_CHART), alice)).status, 200);
    assert.equal((await call("GET", proxyPath(BRAPI), alice)).status, 403);
    assert.equal((await call("POST", proxyPath(YAHOO_LIST), { ...alice, body: {} })).status, 403);
    assert.deepEqual(upstream.map(u => u.url), [YAHOO_CHART]);
});

test("administrador e token de serviço atualizam a lista; o token da brapi fica no servidor", async () => {
    for (const who of [admin, { bearer: SERVICE_TOKEN }]) {
        upstream.length = 0;
        const response = await call("GET", proxyPath(BRAPI), who);
        assert.equal(response.status, 200);
        assert.match(upstream[0].url, /token=segredo-brapi/);
        assert.doesNotMatch(await response.text(), /segredo-brapi/);
        assert.equal((await call("POST", proxyPath(YAHOO_LIST), { ...who, body: {} })).status, 200);
    }
});

test("o repasse não aceita outros sites, nem para o administrador", async () => {
    for (const url of ["https://exemplo.test/x", "http://query1.finance.yahoo.com/x", "nao-e-url"]) {
        assert.equal((await call("GET", proxyPath(url), admin)).status, 400, url);
    }
    assert.equal((await call("POST", proxyPath(YAHOO_CHART), { ...admin, body: {} })).status, 405);
    assert.equal(upstream.length, 0);
});
