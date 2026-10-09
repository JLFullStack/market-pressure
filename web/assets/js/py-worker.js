// Web Worker da versão publicada (Cloudflare): roda o pacote Python marketpressure no Pyodide
// e responde os pedidos /api/* da página (core.js) com as mesmas rotas do server.py local
// (marketpressure/browser.py). Nenhum cálculo aqui: este arquivo só carrega o Python, repassa
// pedidos e respostas e busca em paralelo os históricos da varredura do screener.
//
// Mensagens: página → {id, method, path, body (texto JSON | null)}; Worker → {id, status, data}.

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const PY_BASE = new URL("../../py/", self.location);
const SCAN_BATCH = 8; // históricos buscados ao mesmo tempo (como o batchSize do servidor)

importScripts(`${PYODIDE_URL}pyodide.js`);

const ready = (async () => {
    const pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
    const manifest = await (await fetch(new URL("manifest.json", PY_BASE), { cache: "no-store" })).json();
    const dir = "/home/pyodide/marketpressure";
    pyodide.FS.mkdirTree(dir);
    await Promise.all(manifest.files.map(async (file) => {
        const response = await fetch(new URL(`marketpressure/${file}?v=${manifest.version}`, PY_BASE));
        if (!response.ok) throw new Error(`não foi possível carregar ${file} (HTTP ${response.status})`);
        pyodide.FS.writeFile(`${dir}/${file}`, await response.text());
    }));
    pyodide.runPython("import sys; sys.path.insert(0, '/home/pyodide')");
    return pyodide.pyimport("marketpressure.browser");
})();

let scanning = false;

self.onmessage = async (event) => {
    const { id, method, path, body } = event.data;
    let browser;
    try {
        browser = await ready;
    } catch (err) {
        self.postMessage({ id, status: 500, data: { message: `falha ao carregar o Python no navegador: ${err.message}` } });
        return;
    }
    try {
        const out = JSON.parse(browser.handle(method, path, body));
        self.postMessage({ id, status: out.status, data: out.body });
    } catch (err) {
        self.postMessage({ id, status: 500, data: { message: `erro interno: ${err.message}` } });
    }
    if (!scanning && browser.take_scan_request()) runScan(browser);
};

// Varredura do screener em etapas (ver browser.py): busca SCAN_BATCH históricos em paralelo,
// entrega ao Python e analisa; entre um lote e outro o Worker responde o progresso à página.
async function runScan(browser) {
    scanning = true;
    try {
        const urls = JSON.parse(browser.scan_begin());
        if (!urls) return;
        for (let start = 0; start < urls.length; start += SCAN_BATCH) {
            const batch = urls.slice(start, start + SCAN_BATCH);
            const responses = await Promise.all(batch.map(fetchSource));
            batch.forEach((url, k) => {
                browser.prime(url, responses[k].status, responses[k].text);
                browser.scan_step(start + k);
            });
        }
        browser.scan_end();
    } catch (err) {
        console.error("varredura interrompida", err);
        browser.scan_end();
    } finally {
        scanning = false;
    }
}

async function fetchSource(url) {
    try {
        const response = await fetch(browserProxy(url));
        return { status: response.status, text: await response.text() };
    } catch (err) {
        return { status: 0, text: err.message };
    }
}

const browserProxy = (url) => `/proxy?url=${encodeURIComponent(url)}`;
