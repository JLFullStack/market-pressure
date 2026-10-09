// Tabelas do D1 da versão publicada, usadas pelo Worker (index.js, auth.js) e pelo script que
// cria usuários (scripts/create-user.mjs). Criadas no primeiro uso, sem migrações.
//
//   mp_tables / mp_backup  tabelas de marketpressure/database.py, uma linha por tabela (texto JSON)
//   mp_users               contas (só o administrador cria; não há cadastro pela tela de login)
//   mp_sessions            sessões abertas (id = SHA-256 do token do cookie, nunca o token)
//   mp_login_attempts      falhas de login recentes por CPF/e-mail e por IP (bloqueio temporário)

export const SCHEMA = [
    "CREATE TABLE IF NOT EXISTS mp_tables (name TEXT PRIMARY KEY, rows TEXT NOT NULL, updated_at INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS mp_backup (name TEXT PRIMARY KEY, rows TEXT NOT NULL, updated_at INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS mp_users (id TEXT PRIMARY KEY, name TEXT NOT NULL, cpf TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS mp_sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS mp_login_attempts (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, first_at INTEGER NOT NULL)",
];

// Tabelas do screener, comuns a todos os usuários. As demais (carteiras) são de cada usuário e
// ficam em mp_tables com o nome "{id do usuário}/{tabela}"; o Python não sabe disso.
export const SHARED_TABLES = new Set(["universe", "scan", "scan_failures", "scan_analyses"]);
