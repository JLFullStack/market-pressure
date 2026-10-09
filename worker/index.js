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
//   o resto          arquivos de dist/ (páginas, CSS, JS e o pacote Python), via ASSETS.

const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36";
// Host → segundos no cache (os mesmos tempos do cache em memória do Python).
const SOURCES = {
    "query1.finance.yahoo.com": 900,
    "brapi.dev": 900,
    "api.bcb.gov.br": 43200,
    "sistemaswebb3-listados.b3.com.br": 21600,
};
const TABLE_NAME = /^[a-z_]{1,40}$/;
const MAX_DB_BODY = 4 * 1024 * 1024;

export default {
    async fetch(request, env) {
        const url = new URL(request.url);
        try {
            if (url.pathname === "/proxy") return await proxy(request, url, env);
            if (url.pathname === "/db") return await database(request, url, env);
        } catch (err) {
            console.error(err);
            return json(500, { message: `erro interno: ${err.message}` });
        }
        return env.ASSETS.fetch(request);
    },
};

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
            headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
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
    schemaReady ??= db.batch(["mp_tables", "mp_backup"].map(table => db.prepare(
        `CREATE TABLE IF NOT EXISTS ${table} (name TEXT PRIMARY KEY, rows TEXT NOT NULL, updated_at INTEGER NOT NULL)`,
    ))).catch(err => { schemaReady = null; throw err; });
    return schemaReady;
}

async function database(request, url, env) {
    if (!env.DB) return json(500, { message: "banco D1 não configurado (binding DB no wrangler.jsonc)" });
    // Sem login por enquanto; ao menos outro site não grava pelo navegador de quem visita.
    const origin = request.headers.get("Origin");
    if (request.method !== "GET" && origin && new URL(origin).host !== url.host) {
        return json(403, { message: "origem não permitida" });
    }
    await ensureSchema(env.DB);
    if (request.method === "GET") return readTables(env.DB, url);
    if (request.method === "PUT") return writeTables(env.DB, request);
    return json(405, { message: "método não permitido" });
}

async function readTables(db, url) {
    const names = (url.searchParams.get("names") || "").split(",").filter(Boolean);
    if (!names.length || names.some(n => !TABLE_NAME.test(n))) return json(400, { message: "tabelas inválidas" });
    const { results } = await db.prepare(`SELECT name, rows FROM mp_tables WHERE name IN (${names.map(() => "?").join(",")})`)
        .bind(...names).all();
    const found = new Map(results.map(r => [r.name, r.rows]));
    // O texto de cada tabela vai como foi gravado (o Python o lê; pode ter NaN).
    return json(200, `{${names.map(n => `${JSON.stringify(n)}:${found.get(n) ?? "null"}`).join(",")}}`);
}

async function writeTables(db, request) {
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
    const now = Date.now();
    const statements = [];
    for (const [name, rows] of tables) {
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
