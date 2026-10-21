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

from . import ai_prompt
from . import backtest_prompt as bp
from . import goals as gl
from . import market_data, screener
from . import portfolio as pf
from . import portfolio_backtest as pb
from . import portfolio_prompt
from . import portfolio_service as svc
from . import portfolio_store as store
from . import strategy as st

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
    liquidity = req.arg("liquidez", "padrao")
    if liquidity not in screener.LIQUIDITY:
        raise HTTPError(400, "liquidez deve ser padrao, 10, 5, 3, 2 ou 1 (milhões de R$ por pregão)")
    settings = st.load()
    result = screener.scan_job.status(force=req.arg("force") == "1", liquidity=liquidity,
                                      kinds=st.screener_kinds(settings))
    if not result.get("scan"):
        return result
    return {**result, "scan": scan_with_advice(result["scan"], req.arg("carteira"), settings)}


def open_lots_for(portfolio_id, tickers):
    """Compras abertas dos `tickers` na carteira escolhida (a última aberta na aba Carteira; sem
    ela, a primeira), {ticker: lotes}; {} sem carteira ou se ela não puder ser lida."""
    try:
        portfolios = store.read_doc()["portfolios"]
        portfolio = next((p for p in portfolios if p.get("id") == portfolio_id), portfolios[0] if portfolios else None)
        if portfolio:
            return svc.held_lots(portfolio, tickers)
    except Exception:
        log.warning("carteira indisponível para a indicação do screener", exc_info=True)
    return {}


def scan_with_advice(payload, portfolio_id, settings=None):
    """Ranking com a coluna "Indicação" (COMPRAR/AGUARDAR) pela estratégia salva de cada tipo e
    pelas compras abertas da carteira escolhida."""
    settings = settings or st.load()
    lots = open_lots_for(portfolio_id, screener.top_tickers(payload))
    return {**screener.with_advice(payload, lots, lambda kind: st.for_group(settings, kind)),
            "strategy": st.describe(settings)}


@route("GET", "/api/scan/prompt")
def get_scan_prompt(req):
    """Botão "ANALISAR ATIVO": o texto para uma IA sobre um ativo do ranking, na mesma visão da
    página (mode, strict=1, liquidez) e com as compras abertas da carteira (carteira=)."""
    liquidity, mode = req.arg("liquidez", "padrao"), req.arg("mode", "fechamento")
    if liquidity not in screener.LIQUIDITY:
        raise HTTPError(400, "liquidez deve ser padrao, 10, 5, 3, 2 ou 1 (milhões de R$ por pregão)")
    if mode not in screener.MODES:
        raise HTTPError(400, "mode deve ser fechamento ou intradiario")
    ticker = valid_ticker(pf.normalize_ticker(req.arg("ticker")))
    job = screener.scan_job
    if not job.scan:
        raise HTTPError(404, "Nenhuma varredura disponível; atualize o ranking.")
    lots = open_lots_for(req.arg("carteira"), {ticker}).get(ticker)
    settings = st.load()
    try:
        text = ai_prompt.build(job.scan, job.payload(liquidity, st.screener_kinds(settings)), ticker, mode,
                               req.arg("strict") == "1", lots, settings)
    except ValueError as err:
        raise HTTPError(404, str(err)) from None
    return {"ticker": ticker, "text": text}


@route("GET", "/api/bova")
def get_bova(req):
    return screener.bova_kpi(req.arg("mode", "fechamento"))


def percent_arg(req, name, default, low, high):
    """Parâmetro em % (aceita vírgula) → fração; fora de [low, high] = 400."""
    value = pf.parse_number(req.arg(name), default)
    if value is None or not low <= value <= high:
        raise HTTPError(400, f"{name} deve ficar entre {low} e {high}%")
    return value / 100


def backtest_args(req):
    """Parâmetros do backtest de um ativo (ticker, market b3|us, saida config|manual, target e
    fraction em %, strict=1 exige o candle de rejeição, from/to = período, AAAA-MM-DD, opcionais;
    amount = valor de cada compra) → os argumentos de screener.backtest_view."""
    market = req.arg("market", "b3")
    if market not in screener.BACKTEST_MARKETS:
        raise HTTPError(400, "market deve ser b3 ou us")
    ticker = valid_ticker(pf.normalize_ticker(req.arg("ticker")))
    if req.arg("saida", "config") == "config":
        # Saída das Configurações, pela regra do tipo do ativo (reversão = sem venda parcial).
        name, kind = screener.asset_info(ticker, market)
        rule = st.for_group(st.load(), st.group_of(kind, market, name))
        target, fraction = rule["target"], rule["fraction"] if rule["key"] == "parcial" else 0.0
    else:
        target = percent_arg(req, "target", screener.CONFIG["partialTarget"] * 100, 0.5, 500)
        fraction = percent_arg(req, "fraction", screener.CONFIG["partialFraction"] * 100, 0, 100)
    amount = pf.parse_number(req.arg("amount"), screener.BACKTEST_AMOUNT)
    if amount is None or not 1 <= amount <= 100_000_000:
        raise HTTPError(400, "O valor por compra deve ficar entre R$ 1 e R$ 100 milhões.")
    start, end = req.arg("from") or None, req.arg("to") or None
    for value in (start, end):
        if value and not pf.is_iso_date(value):
            raise HTTPError(400, "informe as datas do período como AAAA-MM-DD")
    if start and end and start >= end:
        raise HTTPError(400, "O início do período deve ser antes do fim.")
    return ticker, market, target, fraction, req.arg("strict") == "1", start, end, amount


def single_backtest(args):
    """screener.backtest_view, guardado para o prompt de análise (ValueError → 400)."""
    key = bp.cache_key("ativo", args)
    view = bp.recall(key)
    if view is None:
        try:
            view = screener.backtest_view(*args)
        except ValueError as err:
            raise HTTPError(400, str(err)) from None
        bp.remember(key, view)
    return view


@route("GET", "/api/backtest")
def get_backtest(req):
    """Aba Backtest: a estratégia lote a lote num ativo."""
    return single_backtest(backtest_args(req))


@route("GET", "/api/backtest/prompt")
def get_backtest_prompt(req):
    """Botão "ANALISAR BACKTEST" do modo Um ativo: {title, text}, com os mesmos parâmetros."""
    return bp.single(single_backtest(backtest_args(req)), req.arg("saida", "config"))

# Configurações


@route("GET", "/api/configuracoes")
def get_settings(req):
    return st.payload()


@route("POST", "/api/configuracoes")
def post_settings(req):
    """Salva a estratégia de venda ({target, fraction} em %, kinds: {grupo: parcial|reversao})."""
    strategy, error = st.from_form(req.json_object())
    if error:
        raise HTTPError(400, error)
    st.save(strategy)
    return {**st.payload(strategy),
            "message": "Estratégia salva. A Carteira, o Screener e o Backtest já seguem a regra de cada tipo."}

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


@route("GET", f"/api/carteira/{PORTFOLIO_ID}/prompt")
def get_portfolio_prompt(req):
    """Botão "ANALISAR CARTEIRA": o texto para uma IA sobre a carteira e cada ativo dela."""
    return portfolio_prompt.prompt(portfolio_of(req, needs_entries=True))


@route("GET", f"/api/carteira/{PORTFOLIO_ID}/proventos")
def get_receivables(req):
    return svc.receivables(portfolio_of(req, needs_entries=True))


@route("POST", f"/api/carteira/{PORTFOLIO_ID}/aporte")
def post_contribution(req):
    body = req.json_object()
    return svc.contribution(portfolio_of(req, needs_entries=True), body.get("amount"), body.get("mode"),
                            body.get("base"), body.get("selected"))


@route("POST", "/api/backtest/carteira")
def post_portfolio_backtest(req):
    """Aba Backtest, modo Carteira: o setup em vários ativos com um único caixa (portfolio_backtest)."""
    return portfolio_backtest(req, pb.run)


@route("POST", "/api/backtest/carteira/robustez")
def post_portfolio_robustness(req):
    """Modo Carteira, "Testar robustez": a mesma simulação em vários períodos."""
    return portfolio_backtest(req, pb.robustness)


@route("POST", "/api/backtest/carteira/prompt")
def post_portfolio_backtest_prompt(req):
    """Botão "ANALISAR BACKTEST" do modo Carteira: {title, text}, com o mesmo corpo."""
    return bp.portfolio(portfolio_backtest(req, pb.run))


@route("POST", "/api/backtest/carteira/robustez/prompt")
def post_portfolio_robustness_prompt(req):
    """Botão "ANALISAR BACKTEST" do teste de robustez: {title, text}, com o mesmo corpo."""
    return bp.robustness(portfolio_backtest(req, pb.robustness))


def portfolio_backtest(req, fn):
    """pb.run ou pb.robustness pelo corpo do formulário, do cache quando já calculado (ValueError → 400)."""
    try:
        return bp.run_portfolio(pb.parse_params(req.json_object()), fn)
    except ValueError as err:
        raise HTTPError(400, str(err)) from None


@route("GET", f"/api/carteira/{PORTFOLIO_ID}/metas")
def get_goals(req):
    return svc.goals_payload(portfolio_of(req))


@route("POST", f"/api/carteira/{PORTFOLIO_ID}/metas/simular")
def post_goal_preview(req):
    portfolio = portfolio_of(req)
    goal, error = gl.goal_from_form(req.json_object(), svc.today_iso())
    if error:
        raise HTTPError(400, error)
    return svc.goal_preview(portfolio, goal)


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
