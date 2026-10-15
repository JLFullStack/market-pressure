"""Servidor HTTP do MarketPressure: serve as páginas de web/ e as rotas /api/*.

O navegador não consegue consultar o Yahoo Finance diretamente (bloqueio de CORS) e o plano
gratuito da brapi só libera 3 meses de histórico. Este servidor busca os dados, faz todos os
cálculos (screener, portfolio) e entrega à página resultados prontos para exibir. Este módulo
só faz o HTTP; as rotas ficam em api.py (as mesmas que o navegador usa na versão publicada).

    GET  /                              -> web/analise_quantitativa_b3.html (aba Screener)
    GET  /carteira                      -> web/carteira.html (aba Carteira)
    GET  /configuracoes                 -> web/configuracoes.html (aba Configurações)
    GET  /assets/...                    -> arquivos de web/assets/ (CSS e JS)

    Screener
    GET  /api/scan[?force=1]            -> varredura (inicia em segundo plano e informa o progresso)
    GET  /api/bova?mode=fechamento      -> KPI do BOVA11 (variação de 12 meses e médias)

    Configurações
    GET  /api/configuracoes             -> estratégia de venda salva (strategy.py)
    POST /api/configuracoes             -> salva a estratégia ({strategy, target, fraction}, em %)

    Carteira
    GET  /api/portfolios               -> carteiras salvas no banco data/ (já migradas)
    PUT  /api/portfolios                -> grava o documento inteiro das carteiras
    POST /api/carteira/acao             -> uma alteração ({"action": ..., "id": ..., ...})
    GET  /api/carteira/{id}/estrutura   -> classes, histórico de lançamentos e seletores
    GET  /api/carteira/{id}/analise     -> posições, KPIs, rentabilidade, alocação, proventos
    POST /api/carteira/{id}/aporte      -> plano da calculadora de aporte
    GET  /api/carteira/{id}/proventos   -> proventos anunciados a receber
    GET  /api/carteira/{id}/ativo?ticker=&market=  -> ticker novo? classe sugerida, mercado
    GET  /api/cotacao?ticker=&market=&date=        -> fechamento do dia e câmbio USDBRL
    POST /api/lote/totais               -> totais da lista "Registrar compras sugeridas"

    Dados brutos
    GET  /api/universe                  -> ações mais líquidas da B3 (lista da brapi)
    GET  /api/history/PETR4[?market=]   -> candles diários de 10 anos + proventos (Yahoo Finance)
    GET  /api/cdi?from=AAAA-MM-DD       -> taxas diárias do CDI (Banco Central, série 12)
    GET  /api/proventos/PETR4           -> proventos anunciados com data de pagamento (B3)

Erros: {"message": ...} com 400 (pedido inválido ou ação recusada), 403 (origem não
permitida), 404, 500 (erro interno, registrado no log) ou 502 (falha de uma fonte externa).
"""

import json
import logging
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api, config, screener
from .api import HTTPError, clean
from .core import in_session_hours

log = logging.getLogger(__name__)

PAGES = {
    "/": "analise_quantitativa_b3.html",
    "/index.html": "analise_quantitativa_b3.html",
    "/analise_quantitativa_b3.html": "analise_quantitativa_b3.html",
    "/carteira": "carteira.html",
    "/carteira.html": "carteira.html",
    "/backtest": "backtest.html",
    "/backtest.html": "backtest.html",
    "/configuracoes": "configuracoes.html",
    "/configuracoes.html": "configuracoes.html",
}
# Só arquivos de web/ com estas extensões são servidos; o código e o banco (data/) ficam fora.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
MAX_BODY_BYTES = 2 * 1024 * 1024
# A página pode vir deste servidor ou de outro servidor local (ex.: Live Server na porta 5500).
# Sites externos não podem chamar a API: sem isso, qualquer página aberta no navegador poderia
# alterar ou apagar as carteiras com um POST para 127.0.0.1.
ALLOWED_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")
# Rotas chamadas muitas vezes por minuto; ficam fora do log de acesso.
QUIET_PATHS = ("/api/history/", "/api/proventos/", "/api/scan", "/api/lote/", "/api/cotacao")


def origin_allowed(origin):
    """Sem Origin (mesma origem num GET, curl, testes) ou origem local."""
    return not origin or bool(ALLOWED_ORIGIN.match(origin))


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_OPTIONS(self):
        # Pré-voo CORS: POST/PUT vindos de outra origem local (ex.: Live Server) perguntam antes.
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def dispatch(self, method):
        url = urllib.parse.urlparse(self.path)
        try:
            if not origin_allowed(self.headers.get("Origin")):
                raise HTTPError(403, "origem não permitida")
            if method == "GET" and not url.path.startswith("/api/"):
                return self.send_static(url.path)
        except HTTPError as err:
            return self.send_json(err.status, {"message": str(err), **err.extra})
        status, payload = api.call(method, url.path, urllib.parse.parse_qs(url.query), self.read_json)
        self.send_json(status, payload)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            raise HTTPError(400, "corpo ausente ou maior que 2 MB")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HTTPError(400, "JSON inválido") from None

    def send_static(self, path):
        web = config.WEB_DIR.resolve()
        file = (web / PAGES[path]) if path in PAGES else (web / urllib.parse.unquote(path).lstrip("/")).resolve()
        content_type = CONTENT_TYPES.get(file.suffix)
        if not (content_type and file.is_relative_to(web) and file.is_file()):
            raise HTTPError(404, "não encontrado")
        self.send(200, file.read_bytes(), content_type)

    def send_json(self, status, payload):
        self.send(status, json.dumps(clean(payload), ensure_ascii=False).encode("utf-8"),
                  "application/json; charset=utf-8")

    def send_cors_headers(self):
        origin = self.headers.get("Origin")
        if origin and origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if not self.path.startswith(QUIET_PATHS):
            log.info("%s %s", self.address_string(), fmt % args)


DAILY_CHECK_SECONDS = 10 * 60


def daily_update():
    """Atualização diária local (a mesma do update.py publicado): fora do horário do pregão,
    se a varredura salva é de antes do último fechamento, inicia uma, que acrescenta ao
    histórico salvo os candles que faltam. Confere ao iniciar e a cada DAILY_CHECK_SECONDS,
    então também roda quando o servidor é aberto depois do fechamento."""
    while True:
        try:
            if not in_session_hours():
                screener.scan_job.status()
        except Exception:
            log.warning("atualização diária falhou", exc_info=True)
        time.sleep(DAILY_CHECK_SECONDS)


def main():
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BRAPI_TOKEN:
        log.warning("BRAPI_TOKEN não definido (.env): a lista de ações virá da última cópia salva no banco")
    threading.Thread(target=daily_update, daemon=True).start()
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"MarketPressure rodando em http://{config.HOST}:{config.PORT}  (Ctrl+C para parar)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
