// Monta dist/ para o Cloudflare (npm run build; o wrangler deploy roda antes de publicar):
// as páginas de web/ (index.html = Screener) e o pacote marketpressure/, que o navegador
// executa no Pyodide (py/marketpressure/*.py, listados em py/manifest.json).
import { createHash } from "node:crypto";
import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
// Só o servidor HTTP local fica de fora (no navegador as rotas vêm de browser.py).
const SKIP = new Set(["__main__.py", "server.py"]);

// Esvazia dist/ sem apagar a pasta (no Windows o wrangler dev a mantém aberta).
mkdirSync(dist, { recursive: true });
for (const entry of readdirSync(dist)) rmSync(join(dist, entry), { recursive: true, force: true });
cpSync(join(root, "web"), dist, { recursive: true });
cpSync(join(dist, "analise_quantitativa_b3.html"), join(dist, "index.html"));

const pkg = join(dist, "py", "marketpressure");
mkdirSync(pkg, { recursive: true });
const hash = createHash("sha256");
const files = readdirSync(join(root, "marketpressure")).filter(f => f.endsWith(".py") && !SKIP.has(f)).sort();
for (const file of files) {
    const text = readFileSync(join(root, "marketpressure", file));
    hash.update(file).update(text);
    writeFileSync(join(pkg, file), text);
}
// version entra na URL de cada arquivo: um deploy novo não reaproveita o cache antigo.
writeFileSync(join(dist, "py", "manifest.json"), JSON.stringify({ version: hash.digest("hex").slice(0, 12), files }));
console.log(`dist/ pronto: páginas de web/ e ${files.length} módulos Python`);
