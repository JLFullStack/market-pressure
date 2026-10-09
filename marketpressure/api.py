"""Rotas /api/* do MarketPressure, sem HTTP: cada rota é uma função curta registrada com
@route que chama os outros módulos; call() acha a rota e converte os erros em status.

Usado pelo servidor local (server.py) e, na versão publicada, pelo navegador (browser.py,
Pyodide): as duas pontas respondem exatamente as mesmas rotas. Lista das rotas e do
contrato em server.py e no CLAUDE.md.
"""

import logging
import math
import re
from datetime import date

from . import market_data, screener
from . import portfolio as pf
from . import portfolio_service as svc
from . import portfolio_store as store

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------- Despacho


def find_route(method, path):
    allowed = False
    for route_method, pattern, handler in ROUTES:
        match = pattern.match(path)
        if match:
            if route_method == method:
                return handler, match.groupdict()
            allowed = True
    raise HTTPError(405 if allowed else 404, "método não permitido" if allowed else "não encontrado")


def call(method, path, query, read_body):
    """Executa a rota: (status, payload). query = parse_qs da query string; read_body() devolve
    o corpo JSON (ou levanta HTTPError 400)."""
    try:
        handler, params = find_route(method, path)
        return 200, handler(Request(params, query, read_body))
    except HTTPError as err:
        return err.status, {"message": str(err), **err.extra}
    except store.ActionError as err:
        return 400, {"message": str(err), **err.extra}
    except market_data.UpstreamError as err:
        return 502, {"message": str(err)}
    except OSError as err:
        log.exception("falha de leitura/gravação em %s %s", method, path)
        return 500, {"message": f"falha ao acessar o banco de dados: {err}"}
    except Exception as err:
        log.exception("erro interno em %s %s", method, path)
        return 500, {"message": f"erro interno do servidor: {err}"}
