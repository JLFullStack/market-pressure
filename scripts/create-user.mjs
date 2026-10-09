// Cria uma conta direto no D1, pela linha de comando (foi assim que nasceu o administrador; as
// outras contas o administrador cria pela página Conta). Usa o mesmo hash de senha do Worker.
//
//   node scripts/create-user.mjs --name "Nome" --cpf 00000000000 --email a@b.com [--admin]
//                                [--remote] [--claim-legacy]
//
//   --remote        grava no D1 publicado (padrão: o D1 local do wrangler dev, em .wrangler/)
//   --admin         conta de administrador (cria, redefine e exclui as outras)
//   --claim-legacy  passa para esta conta as carteiras gravadas antes do login existir
//
// A senha vem da variável MP_PASSWORD ou é pedida no terminal; nunca vai para arquivo nem para
// o repositório (os dados pessoais também não: ficam só no D1).
import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { hashPassword, newUserProblem, normalizeCpf, normalizeEmail } from "../worker/auth.js";
import { SCHEMA, SHARED_TABLES } from "../worker/schema.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const { values: opts } = parseArgs({
    options: {
        name: { type: "string" }, cpf: { type: "string" }, email: { type: "string" },
        admin: { type: "boolean", default: false }, remote: { type: "boolean", default: false },
        "claim-legacy": { type: "boolean", default: false },
    },
});

function askHidden(question) {
    return new Promise(resolve => {
        const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
        rl._writeToOutput = (text) => { if (text.includes(question)) process.stdout.write(text); };
        rl.question(question, (answer) => { rl.close(); process.stdout.write("\n"); resolve(answer); });
    });
}

const password = process.env.MP_PASSWORD ?? await askHidden("Senha: ");
const problem = newUserProblem({ ...opts, password });
if (problem) {
    console.error(`Não criado: ${problem}.`);
    process.exit(1);
}

const sql = (value) => `'${String(value).replace(/'/g, "''")}'`;
const id = crypto.randomUUID();
const statements = [
    ...SCHEMA,
    `INSERT INTO mp_users (id, name, cpf, email, password_hash, admin, created_at) VALUES (${[
        sql(id), sql(opts.name.trim()), sql(normalizeCpf(opts.cpf)), sql(normalizeEmail(opts.email)),
        sql(await hashPassword(password)), opts.admin ? 1 : 0, Date.now(),
    ].join(", ")})`,
];
if (opts["claim-legacy"]) {
    // Tabelas sem dono (nome sem "/") que não são do screener: as carteiras de antes do login.
    const shared = [...SHARED_TABLES].map(sql).join(", ");
    for (const table of ["mp_tables", "mp_backup"]) {
        statements.push(`UPDATE ${table} SET name = ${sql(`${id}/`)} || name WHERE instr(name, '/') = 0 AND name NOT IN (${shared})`);
    }
}

// Arquivo temporário em .wrangler/ (fora do git), apagado em seguida: tem o hash da senha.
mkdirSync(join(root, ".wrangler"), { recursive: true });
const file = join(".wrangler", `create-user-${id}.sql`);
writeFileSync(join(root, file), statements.map(s => `${s};`).join("\n"));
try {
    // Uma string só (no Windows o npx precisa do shell); nenhum dado digitado entra nela.
    const command = `npx wrangler d1 execute market-pressure ${opts.remote ? "--remote" : "--local"} --yes --file ${file}`;
    const result = spawnSync(command, { cwd: root, stdio: "inherit", shell: true });
    if (result.status !== 0) process.exit(result.status ?? 1);
} finally {
    rmSync(join(root, file), { force: true });
}
console.log(`Conta criada no D1 ${opts.remote ? "publicado" : "local"}: ${normalizeEmail(opts.email)}${opts.admin ? " (administrador)" : ""}.`);
