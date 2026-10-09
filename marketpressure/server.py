"""Servidor HTTP do MarketPressure: serve as páginas de web/ e as rotas /api/*.

O navegador não consegue consultar o Yahoo Finance diretamente (bloqueio de CORS) e o plano
gratuito da brapi só libera 3 meses de histórico. Este servidor busca os dados, faz todos os
cálculos (screener, portfolio) e entrega à página resultados prontos para exibir. Este módulo
só faz o HTTP: cada rota é uma função curta registrada com @route que chama os outros módulos.

    GET  /                              -> web/analise_quantitativa_b3.html (aba Screener)
    GET  /carteira                      -> web/carteira.html (aba Carteira)
    GET  /assets/...                    -> arquivos de web/assets/ (CSS e JS)

    Screener
    GET  /api/scan[?force=1]            -> varredura (inicia em segundo plano e informa o progresso)
    GET  /api/bova?mode=fechamento      -> KPI do BOVA11 (variação de 12 meses e médias)

    Carteira
    GET  /api/portfolios                -> carteiras salvas no banco data/ (já migradas)
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
    GET  /api/history/PETR4[?market=]   -> candles diários de 5 anos + proventos (Yahoo Finance)
    GET  /api/cdi?from=AAAA-MM-DD       -> taxas diárias do CDI (Banco Central, série 12)
    GET  /api/proventos/PETR4           -> proventos anunciados com data de pagamento (B3)

Erros: {"message": ...} com 400 (pedido inválido ou ação recusada), 403 (origem não
permitida), 404, 500 (erro interno, registrado no log) ou 502 (falha de uma fonte externa).
"""

import json
import logging
import math
import re
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, market_data, screener
from . import portfolio as pf
from . import portfolio_service as svc
from . import portfolio_store as store

log = logging.getLogger(__name__)

PAGES = {
    "/": "analise_quantitativa_b3.html",
    "/index.html": "analise_quantitativa_b3.html",
    "/analise_quantitativa_b3.html": "analise_quantitativa_b3.html",
    "/carteira": "carteira.html",
    "/carteira.html": "carteira.html",
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


class HTTPError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


def clean(value):
    """JSON válido para o navegador: NaN e infinito viram null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def origin_allowed(origin):
    """Sem Origin (mesma origem num GET, curl, testes) ou origem local."""
    return not origin or bool(ALLOWED_ORIGIN.match(origin))


# ---------------------------------------------------------------- Rotas

ROUTES = []  # (método, regex do caminho, função)


def route(method, pattern):
    """Registra uma rota. Os grupos nomeados do padrão chegam em req.params."""
    def register(handler):
        ROUTES.append((method, re.compile(f"^{pattern}$"), handler))
        return handler
    return register


class Request:
    def __init__(self, params, query, read_body):
        self.params, self._query, self._read_body = params, query, read_body

    def arg(self, name, default=""):
        return (self._query.get(name) or [default])[0]

    def json(self):
        """Corpo JSON do pedido (POST/PUT)."""
        return self._read_body()

    def json_object(self):
        body = self.json()
        if not isinstance(body, dict):
            raise HTTPError(400, "esperado um objeto JSON")
        return body


def portfolio_of(req, needs_entries=False):
    try:
        portfolio = store.get_portfolio(req.params["id"])
    except store.ActionError as err:
        raise HTTPError(404, str(err)) from None
    if needs_entries and not pf.has_entries(portfolio):
        raise HTTPError(400, "a carteira ainda não tem lançamentos")
    return portfolio


def valid_ticker(ticker, min_length=1):
    ticker = ticker.upper()
    if not ticker.isalnum() or len(ticker) < min_length:
        raise HTTPError(400, "ticker inválido")
    return ticker


PORTFOLIO_ID = r"(?P<id>[0-9A-Za-z-]+)"

# Screener


@route("GET", "/api/scan")
def get_scan(req):
    return screener.scan_job.status(force=req.arg("force") == "1")


@route("GET", "/api/bova")
def get_bova(req):
    return screener.bova_kpi(req.arg("mode", "fechamento"))

# Carteira


@route("GET", "/api/portfolios")
def get_portfolios(req):
    return store.read_doc()


@route("PUT", "/api/portfolios")
def put_portfolios(req):
    """Documento inteiro das carteiras: valida o básico e grava no banco (com backup)."""
    document = req.json()
    if not store.valid_document(document):
        raise HTTPError(400, 'esperado {"version": número, "portfolios": [ {...} ]}')
    try:
        return store.write_doc(document)
    except (ValueError, TypeError) as err:
        raise HTTPError(400, f"documento com valores inválidos: {err}") from None


@route("POST", "/api/carteira/acao")
def post_action(req):
    return store.apply_action(req.json_object())


@route("GET", f"/api/carteira/{PORTFOLIO_ID}/estrutura")
def get_structure(req):
    return svc.structure(portfolio_of(req))


@route("GET", f"/api/carteira/{PORTFOLIO_ID}/ativo")
def get_asset_info(req):
    return svc.asset_info(portfolio_of(req), req.arg("ticker"), req.arg("market", "b3"))


@route("GET", f"/api/carteira/{PORTFOLIO_ID}/analise")
def get_analysis(req):
    return svc.analysis_payload(portfolio_of(req, needs_entries=True))


@route("GET", f"/api/carteira/{PORTFOLIO_ID}/proventos")
def get_receivables(req):
    return svc.receivables(portfolio_of(req, needs_entries=True))


@route("POST", f"/api/carteira/{PORTFOLIO_ID}/aporte")
def post_contribution(req):
    body = req.json_object()
    return svc.contribution(portfolio_of(req, needs_entries=True), body.get("amount"), body.get("mode"),
                            body.get("base"), body.get("selected"))


@route("GET", "/api/cotacao")
def get_quote(req):
    return svc.price_on(req.arg("ticker"), req.arg("market", "b3"), req.arg("date"))


@route("POST", "/api/lote/totais")
def post_batch_totals(req):
    body = req.json_object()
    return pf.batch_totals(body.get("rows") or [], pf.parse_number(body.get("amount"), 0) or 0)

# Dados brutos


@route("GET", "/api/universe")
def get_universe(req):
    return market_data.universe()


@route("GET", "/api/history/(?P<ticker>[^/]+)")
def get_history(req):
    market = req.arg("market", "b3").lower()
    if market not in market_data.YAHOO_SYMBOL:
        raise HTTPError(400, "market deve ser b3, us, crypto ou fx")
    return market_data.raw_history(valid_ticker(req.params["ticker"]), market)


@route("GET", "/api/cdi")
def get_cdi(req):
    try:
        start = date.fromisoformat(req.arg("from"))
    except ValueError:
        raise HTTPError(400, "informe from=AAAA-MM-DD") from None
    return market_data.cdi(start)


@route("GET", "/api/proventos/(?P<ticker>[^/]+)")
def get_proventos(req):
    return market_data.proventos(valid_ticker(req.params["ticker"], min_length=5))


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
            handler, params = self.find_route(method, url.path)
            req = Request(params, urllib.parse.parse_qs(url.query), self.read_json)
            self.send_json(200, handler(req))
        except HTTPError as err:
            self.send_json(err.status, {"message": str(err), **err.extra})
        except store.ActionError as err:
            self.send_json(400, {"message": str(err), **err.extra})
        except market_data.UpstreamError as err:
            self.send_json(502, {"message": str(err)})
        except OSError as err:
            log.exception("falha de leitura/gravação em %s %s", method, url.path)
            self.send_json(500, {"message": f"falha ao acessar o banco de dados: {err}"})
        except Exception as err:
            log.exception("erro interno em %s %s", method, url.path)
            self.send_json(500, {"message": f"erro interno do servidor: {err}"})

    @staticmethod
    def find_route(method, path):
        allowed = False
        for route_method, pattern, handler in ROUTES:
            match = pattern.match(path)
            if match:
                if route_method == method:
                    return handler, match.groupdict()
                allowed = True
        raise HTTPError(405 if allowed else 404, "método não permitido" if allowed else "não encontrado")

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


def main():
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BRAPI_TOKEN:
        log.warning("BRAPI_TOKEN não definido (.env): a lista de ações virá da última cópia salva no banco")
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"MarketPressure rodando em http://{config.HOST}:{config.PORT}  (Ctrl+C para parar)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
