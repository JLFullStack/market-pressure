// Worker do MarketPressure no Cloudflare (versão publicada). Os cálculos rodam no navegador
// (Python no Pyodide, web/assets/js/py-worker.js); este Worker só faz o que o navegador não
// consegue sozinho:
//
//   /proxy?url=...   repasse para as fontes de dados, que não mandam CORS (Yahoo, brapi, BCB,
//                    B3); só os hosts de SOURCES. O token da brapi (segredo BRAPI_TOKEN) é
//                    acrescentado aqui e nunca chega ao navegador. Respostas ficam no cache
//                    do Cloudflare pelo tempo de cada fonte.
//   /db              banco no D1 (binding DB): cada tabela de marketpressure/database.py é um
//                    texto JSON numa linha de mp_tables. GET ?names=a,b lê; PUT grava várias
//                    juntas (uma transação) e, com backup, copia a versão anterior para mp_backup.
//                    As tabelas das carteiras são de cada usuário (schema.js: SHARED_TABLES).
//   /auth/*          login, sessão e contas (auth.js).
//   o resto          arquivos de dist/ (páginas, CSS, JS e o pacote Python), via ASSETS.
//
// Tudo exige login, menos a tela de login (PUBLIC): sem sessão, as páginas redirecionam para
// /login e o resto responde 401. A exceção é a atualização diária (GitHub Actions), que manda
// o token de serviço (segredo SERVICE_TOKEN) e só usa /proxy e as tabelas comuns do /db. Por isso o wrangler.jsonc tem run_worker_first (sem ele, os
// arquivos de dist/ seriam servidos sem passar por aqui).

import { currentUser, handleAuth } from "./auth.js";
import { SCHEMA, isShared } from "./schema.js";

const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36";
// Host → segundos no cache (os mesmos tempos do cache em memória do Python).
const SOURCES = {
    "query1.finance.yahoo.com": 900,
    "brapi.dev": 900,
    "api.bcb.gov.br": 43200,
    "sistemaswebb3-listados.b3.com.br": 21600,
};
const TABLE_NAME = /^[a-z0-9_]{1,40}$/;
const MAX_DB_BODY = 4 * 1024 * 1024;
const PUBLIC = new Set(["/login", "/login.html", "/assets/css/styles.css", "/assets/js/login.js", "/favicon.ico"]);

export default {
    async fetch(request, env) {
        const url = new URL(request.url);
        try {
            if (!env.DB) return json(500, { message: "banco D1 não configurado (binding DB no wrangler.jsonc)" });
            // Outro site não envia nada em nome de quem está logado (o cookie já é SameSite=Lax).
            const origin = request.headers.get("Origin");
            if (!["GET", "HEAD"].includes(request.method) && origin && new URL(origin).host !== url.host) {
                return json(403, { message: "origem não permitida" });
            }
            await ensureSchema(env.DB);
            const user = await currentUser(request, env);
            if (url.pathname.startsWith("/auth/")) return await handleAuth(request, url, env, user);
            if (PUBLIC.has(url.pathname)) {
                if (user && url.pathname.startsWith("/login")) return Response.redirect(new URL("/", url), 302);
                return env.ASSETS.fetch(request);
            }
            if (!user) {
                // Atualização diária (marketpressure/update.py no GitHub Actions): só /proxy e as
                // tabelas comuns do /db, com o token de serviço (segredo SERVICE_TOKEN).
                if (!(await isService(request, env))) return unauthenticated(request, url);
                if (url.pathname === "/proxy") return await proxy(request, url, env);
                if (url.pathname === "/db") return await database(request, url, env, SERVICE);
                return json(404, { message: "não encontrado" });
            }
            if (url.pathname === "/proxy") return await proxy(request, url, env);
            if (url.pathname === "/db") return await database(request, url, env, user);
        } catch (err) {
            console.error(err);
            return json(500, { message: `erro interno: ${err.message}` });
        }
        return env.ASSETS.fetch(request);
    },
};

const SERVICE = { id: null, service: true };

// Authorization: Bearer <SERVICE_TOKEN>, comparado em tempo constante (pelo SHA-256 dos dois).
async function isService(request, env) {
    const header = request.headers.get("Authorization") || "";
    if (!env.SERVICE_TOKEN || !header.startsWith("Bearer ")) return false;
    const encoder = new TextEncoder();
    const [given, expected] = await Promise.all([header.slice(7), env.SERVICE_TOKEN]
        .map(text => crypto.subtle.digest("SHA-256", encoder.encode(text))));
    return crypto.subtle.timingSafeEqual(given, expected);
}

// Página (navegação) → tela de login, voltando depois para onde estava; o resto → 401.
function unauthenticated(request, url) {
    const page = request.method === "GET" && (request.headers.get("Accept") || "").includes("text/html");
    if (!page) return json(401, { message: "sessão expirada — entre novamente" });
    const login = new URL("/login", url);
    if (url.pathname !== "/") login.searchParams.set("next", url.pathname + url.search);
    return Response.redirect(login, 302);
}

function json(status, body) {
    return new Response(typeof body === "string" ? body : JSON.stringify(body), {
        status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
    });
}

// ---------------------------------------------------------------- Repasse

async function proxy(request, url, env) {
    if (request.method !== "GET") return json(405, { message: "método não permitido" });
    let target;
    try {
        target = new URL(url.searchParams.get("url"));
    } catch {
        return json(400, { message: "url inválida" });
    }
    const ttl = SOURCES[target.hostname];
    if (target.protocol !== "https:" || ttl === undefined) return json(400, { message: "fonte não permitida" });
    if (target.hostname === "brapi.dev") {
        target.searchParams.delete("token");
        if (env.BRAPI_TOKEN) target.searchParams.set("token", env.BRAPI_TOKEN);
    }
    let upstream;
    try {
        upstream = await fetch(target, {
            // Sem Accept: com "application/json" a B3 devolve o JSON dentro de uma string.
            headers: { "User-Agent": USER_AGENT },
            cf: { cacheEverything: true, cacheTtlByStatus: { "200-299": ttl, "404": 60, "400-599": 0 } },
        });
    } catch (err) {
        return json(502, { message: `falha ao consultar ${target.hostname}: ${err.message}` });
    }
    return new Response(upstream.body, {
        status: upstream.status,
        headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" },
    });
}

// ---------------------------------------------------------------- Banco (D1)

let schemaReady = null;

function ensureSchema(db) {
    schemaReady ??= db.batch(SCHEMA.map(sql => db.prepare(sql))).catch(err => { schemaReady = null; throw err; });
    return schemaReady;
}

// Nome da tabela no D1: as do screener e os históricos são comuns; as das carteiras, de cada usuário.
const storedName = (name, user) => isShared(name) ? name : `${user.id}/${name}`;
// O token de serviço (atualização diária) só lê e grava as tabelas comuns.
const forbidden = (names, user) => user.service && names.some(n => !isShared(n));

async function database(request, url, env, user) {
    if (request.method === "GET") return readTables(env.DB, url, user);
    if (request.method === "PUT") return writeTables(env.DB, request, user);
    return json(405, { message: "método não permitido" });
}

async function readTables(db, url, user) {
    const names = (url.searchParams.get("names") || "").split(",").filter(Boolean);
    if (!names.length || names.some(n => !TABLE_NAME.test(n))) return json(400, { message: "tabelas inválidas" });
    if (forbidden(names, user)) return json(403, { message: "só as tabelas comuns" });
    const stored = names.map(n => storedName(n, user));
    const { results } = await db.prepare(`SELECT name, rows FROM mp_tables WHERE name IN (${stored.map(() => "?").join(",")})`)
        .bind(...stored).all();
    const byStored = new Map(results.map(r => [r.name, r.rows]));
    const found = new Map(names.map((n, k) => [n, byStored.get(stored[k])]));
    // O texto de cada tabela vai como foi gravado (o Python o lê; pode ter NaN).
    return json(200, `{${names.map(n => `${JSON.stringify(n)}:${found.get(n) ?? "null"}`).join(",")}}`);
}

async function writeTables(db, request, user) {
    if (Number(request.headers.get("Content-Length") || 0) > MAX_DB_BODY) return json(400, { message: "corpo maior que 4 MB" });
    let body;
    try {
        body = await request.json();
    } catch {
        return json(400, { message: "JSON inválido" });
    }
    const tables = Object.entries(body?.tables || {});
    if (!tables.length || tables.some(([name, rows]) => !TABLE_NAME.test(name) || typeof rows !== "string")) {
        return json(400, { message: 'esperado {"backup": bool, "tables": {nome: "texto JSON"}}' });
    }
    if (forbidden(tables.map(([name]) => name), user)) return json(403, { message: "só as tabelas comuns" });
    const now = Date.now();
    const statements = [];
    for (const [table, rows] of tables) {
        const name = storedName(table, user);
        if (body.backup) {
            statements.push(db.prepare("INSERT OR REPLACE INTO mp_backup SELECT name, rows, updated_at FROM mp_tables WHERE name = ?").bind(name));
        }
        statements.push(db.prepare(
            "INSERT INTO mp_tables (name, rows, updated_at) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET rows = excluded.rows, updated_at = excluded.updated_at",
        ).bind(name, rows, now));
    }
    await db.batch(statements);
    return json(200, { ok: true });
}
