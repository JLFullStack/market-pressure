// Login da versão publicada. O Worker é a fronteira de segurança (o Python roda no navegador,
// que não é confiável), então contas, senhas e sessões ficam só aqui e no D1:
//
//   POST   /auth/login            {login (CPF ou e-mail), password} → abre a sessão (cookie)
//   POST   /auth/logout           encerra a sessão atual
//   GET    /auth/me               {name, email, admin}
//   POST   /auth/password         {current, password} → troca a própria senha
//   GET    /auth/users            (admin) lista as contas
//   POST   /auth/users            (admin) {name, cpf, email, password} → cria uma conta
//   POST   /auth/users/password   (admin) {id, password} → redefine a senha de uma conta
//   DELETE /auth/users?id=        (admin) exclui uma conta, as sessões e as carteiras dela
//
// Não há cadastro pela tela de login: só o administrador cria contas (aqui ou pelo script
// scripts/create-user.mjs, que criou a primeira). Senhas: PBKDF2-SHA256 com sal; o número de
// iterações fica gravado no hash, então pode subir sem invalidar as senhas antigas.

// ~4,5 ms de CPU por senha: o plano gratuito do Workers tem limite de 10 ms por requisição. O
// bloqueio após MAX_FAILURES tentativas compensa o custo menor que o recomendado.
export const ITERATIONS = 10000;
const SESSION_DAYS = 30;
const MAX_FAILURES = 5;
const LOCK_MINUTES = 15;
const COOKIE = "mp_session";
const MIN_PASSWORD = 8;
const MAX_PASSWORD = 128;

const enc = new TextEncoder();
const b64 = (bytes) => btoa(String.fromCharCode(...bytes));
const unb64 = (text) => Uint8Array.from(atob(text), ch => ch.charCodeAt(0));
const hex = (bytes) => [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");

// ---------------------------------------------------------------- Senhas e dados da conta

async function pbkdf2(password, salt, iterations) {
    const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
    return new Uint8Array(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256));
}

export async function hashPassword(password) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    return `pbkdf2_sha256$${ITERATIONS}$${b64(salt)}$${b64(await pbkdf2(password, salt, ITERATIONS))}`;
}

export async function verifyPassword(password, stored) {
    const [scheme, iterations, salt, hash] = String(stored).split("$");
    if (scheme !== "pbkdf2_sha256" || !salt || !hash) return false;
    const actual = await pbkdf2(password, unb64(salt), Number(iterations));
    const expected = unb64(hash);
    if (actual.length !== expected.length) return false;
    let diff = 0;
    for (let i = 0; i < actual.length; i++) diff |= actual[i] ^ expected[i];
    return diff === 0;
}

async function sha256(text) {
    return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(text))));
}

export const normalizeCpf = (value) => String(value ?? "").replace(/\D/g, "");
export const normalizeEmail = (value) => String(value ?? "").trim().toLowerCase();

export function validCpf(cpf) {
    if (!/^\d{11}$/.test(cpf) || /^(\d)\1{10}$/.test(cpf)) return false;
    for (const n of [9, 10]) {
        let sum = 0;
        for (let i = 0; i < n; i++) sum += Number(cpf[i]) * (n + 1 - i);
        if ((sum * 10) % 11 % 10 !== Number(cpf[n])) return false;
    }
    return true;
}

export const validEmail = (email) => email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);

// Mensagem do problema com a senha nova, ou null.
export function passwordProblem(password) {
    if (typeof password !== "string" || password.length < MIN_PASSWORD) return `a senha precisa ter pelo menos ${MIN_PASSWORD} caracteres`;
    if (password.length > MAX_PASSWORD) return `a senha pode ter no máximo ${MAX_PASSWORD} caracteres`;
    return null;
}

// Mensagem do problema com os dados de uma conta nova, ou null.
export function newUserProblem({ name, cpf, email, password }) {
    if (!String(name ?? "").trim()) return "informe o nome";
    if (!validCpf(normalizeCpf(cpf))) return "CPF inválido";
    if (!validEmail(normalizeEmail(email))) return "e-mail inválido";
    return passwordProblem(password);
}

// ---------------------------------------------------------------- Sessão

function readCookie(request, name) {
    for (const part of (request.headers.get("Cookie") || "").split(";")) {
        const [key, ...rest] = part.trim().split("=");
        if (key === name) return rest.join("=");
    }
    return null;
}

function sessionCookie(url, token, maxAge) {
    const secure = url.protocol === "https:" ? "; Secure" : ""; // wrangler dev roda em http
    return `${COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${secure}`;
}

// Usuário da sessão do cookie ({id, name, email, cpf, admin, session}) ou null.
export async function currentUser(request, env) {
    const token = readCookie(request, COOKIE);
    if (!token) return null;
    const row = await env.DB.prepare(
        "SELECT u.id, u.name, u.email, u.cpf, u.admin, s.id AS session FROM mp_sessions s JOIN mp_users u ON u.id = s.user_id WHERE s.id = ? AND s.expires_at > ?",
    ).bind(await sha256(token), Date.now()).first();
    return row ? { ...row, admin: row.admin === 1 } : null;
}

// ---------------------------------------------------------------- Rotas /auth

function json(status, body, headers = {}) {
    return new Response(JSON.stringify(body), {
        status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers },
    });
}

async function readJson(request) {
    try {
        const body = await request.json();
        return body && typeof body === "object" ? body : {};
    } catch {
        return {};
    }
}

export async function handleAuth(request, url, env, user) {
    const { pathname } = url;
    const method = request.method;
    if (pathname === "/auth/login" && method === "POST") return login(request, url, env);
    if (!user) return json(401, { message: "sessão expirada — entre novamente" });
    if (pathname === "/auth/logout" && method === "POST") return logout(url, env, user);
    if (pathname === "/auth/me" && method === "GET") return json(200, { name: user.name, email: user.email, admin: user.admin });
    if (pathname === "/auth/password" && method === "POST") return changeOwnPassword(request, env, user);
    if (pathname.startsWith("/auth/users")) {
        if (!user.admin) return json(403, { message: "somente o administrador gerencia usuários" });
        if (pathname === "/auth/users" && method === "GET") return listUsers(env);
        if (pathname === "/auth/users" && method === "POST") return createUser(request, env);
        if (pathname === "/auth/users" && method === "DELETE") return deleteUser(url, env, user);
        if (pathname === "/auth/users/password" && method === "POST") return resetPassword(request, env, user);
    }
    return json(404, { message: "rota não encontrada" });
}

async function login(request, url, env) {
    const body = await readJson(request);
    const raw = String(body.login ?? "").trim();
    const password = typeof body.password === "string" ? body.password : "";
    if (!raw || !password) return json(400, { message: "informe CPF ou e-mail e a senha" });
    const byEmail = raw.includes("@");
    const login = byEmail ? normalizeEmail(raw) : normalizeCpf(raw);
    const now = Date.now();
    const keys = [`login:${login}`, `ip:${request.headers.get("CF-Connecting-IP") || "local"}`];

    const { results } = await env.DB.prepare("SELECT key, failures, first_at FROM mp_login_attempts WHERE key IN (?, ?)")
        .bind(...keys).all();
    const windowMs = LOCK_MINUTES * 60000;
    const locked = results.find(r => r.failures >= MAX_FAILURES && now - r.first_at < windowMs);
    if (locked) {
        const minutes = Math.ceil((locked.first_at + windowMs - now) / 60000);
        return json(429, { message: `muitas tentativas sem sucesso; tente de novo em ${minutes} min` });
    }

    const user = await env.DB.prepare(`SELECT id, name, password_hash FROM mp_users WHERE ${byEmail ? "email" : "cpf"} = ?`)
        .bind(login).first();
    // Sem conta, o hash é calculado do mesmo jeito: o tempo de resposta não revela quem existe.
    const ok = user ? await verifyPassword(password, user.password_hash)
        : (await pbkdf2(password, new Uint8Array(16), ITERATIONS), false);
    if (!ok) {
        await env.DB.batch(keys.map(key => env.DB.prepare(
            `INSERT INTO mp_login_attempts (key, failures, first_at) VALUES (?1, 1, ?2)
             ON CONFLICT(key) DO UPDATE SET
               failures = CASE WHEN ?2 - first_at >= ?3 THEN 1 ELSE failures + 1 END,
               first_at = CASE WHEN ?2 - first_at >= ?3 THEN ?2 ELSE first_at END`,
        ).bind(key, now, windowMs)));
        return json(401, { message: "CPF/e-mail ou senha incorretos" });
    }

    const token = b64(crypto.getRandomValues(new Uint8Array(32))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const maxAge = SESSION_DAYS * 86400;
    await env.DB.batch([
        env.DB.prepare("DELETE FROM mp_login_attempts WHERE key IN (?, ?)").bind(...keys),
        env.DB.prepare("DELETE FROM mp_sessions WHERE expires_at <= ?").bind(now),
        env.DB.prepare("INSERT INTO mp_sessions (id, user_id, expires_at) VALUES (?, ?, ?)").bind(await sha256(token), user.id, now + maxAge * 1000),
    ]);
    return json(200, { name: user.name }, { "Set-Cookie": sessionCookie(url, token, maxAge) });
}

async function logout(url, env, user) {
    await env.DB.prepare("DELETE FROM mp_sessions WHERE id = ?").bind(user.session).run();
    return json(200, { ok: true }, { "Set-Cookie": sessionCookie(url, "", 0) });
}

async function changeOwnPassword(request, env, user) {
    const body = await readJson(request);
    const problem = passwordProblem(body.password);
    if (problem) return json(400, { message: problem });
    const row = await env.DB.prepare("SELECT password_hash FROM mp_users WHERE id = ?").bind(user.id).first();
    if (!row || !(await verifyPassword(String(body.current ?? ""), row.password_hash))) {
        return json(400, { message: "senha atual incorreta" });
    }
    await setPassword(env, user.id, body.password, user.session);
    return json(200, { message: "Senha alterada. As outras sessões abertas foram encerradas." });
}

// Grava a senha e encerra as sessões da conta, menos keepSession (a de quem está mudando).
async function setPassword(env, userId, password, keepSession) {
    await env.DB.batch([
        env.DB.prepare("UPDATE mp_users SET password_hash = ? WHERE id = ?").bind(await hashPassword(password), userId),
        env.DB.prepare("DELETE FROM mp_sessions WHERE user_id = ? AND id <> ?").bind(userId, keepSession ?? ""),
    ]);
}

async function listUsers(env) {
    const { results } = await env.DB.prepare(
        "SELECT id, name, cpf, email, admin, created_at AS createdAt FROM mp_users ORDER BY admin DESC, name COLLATE NOCASE",
    ).all();
    return json(200, { users: results.map(u => ({ ...u, admin: u.admin === 1 })) });
}

async function createUser(request, env) {
    const body = await readJson(request);
    const problem = newUserProblem(body);
    if (problem) return json(400, { message: problem });
    const cpf = normalizeCpf(body.cpf);
    const email = normalizeEmail(body.email);
    const taken = await env.DB.prepare("SELECT cpf, email FROM mp_users WHERE cpf = ? OR email = ?").bind(cpf, email).first();
    if (taken) return json(400, { message: taken.cpf === cpf ? "já existe uma conta com este CPF" : "já existe uma conta com este e-mail" });
    const name = String(body.name).trim();
    await env.DB.prepare("INSERT INTO mp_users (id, name, cpf, email, password_hash, admin, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)")
        .bind(crypto.randomUUID(), name, cpf, email, await hashPassword(body.password), Date.now()).run();
    return json(200, { message: `Conta de ${name} criada. Passe a senha inicial a essa pessoa por um canal seguro.` });
}

async function resetPassword(request, env, admin) {
    const body = await readJson(request);
    const problem = passwordProblem(body.password);
    if (problem) return json(400, { message: problem });
    const target = await env.DB.prepare("SELECT id, name FROM mp_users WHERE id = ?").bind(String(body.id ?? "")).first();
    if (!target) return json(400, { message: "conta não encontrada" });
    await setPassword(env, target.id, body.password, target.id === admin.id ? admin.session : null);
    return json(200, { message: `Senha de ${target.name} redefinida; as sessões abertas dessa conta foram encerradas.` });
}

async function deleteUser(url, env, admin) {
    const id = url.searchParams.get("id") || "";
    const target = await env.DB.prepare("SELECT id, name, admin FROM mp_users WHERE id = ?").bind(id).first();
    if (!target) return json(400, { message: "conta não encontrada" });
    if (target.admin === 1 || target.id === admin.id) return json(400, { message: "a conta do administrador não pode ser excluída" });
    await env.DB.batch([
        env.DB.prepare("DELETE FROM mp_sessions WHERE user_id = ?").bind(id),
        env.DB.prepare("DELETE FROM mp_tables WHERE name LIKE ?").bind(`${id}/%`),
        env.DB.prepare("DELETE FROM mp_backup WHERE name LIKE ?").bind(`${id}/%`),
        env.DB.prepare("DELETE FROM mp_users WHERE id = ? AND admin = 0").bind(id),
    ]);
    return json(200, { message: `Conta de ${target.name} excluída.` });
}
