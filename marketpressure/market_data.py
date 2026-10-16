"""Coleta de dados de mercado do MarketPressure, com cache em memória.

- brapi: lista de ações da B3 (o plano gratuito libera 1 ticker por requisição e 3 meses de
  histórico, por isso só a lista vem dela).
- Yahoo Finance: candles diários de 10 anos e proventos (data-ex) por mercado.
- Banco Central (SGS 12): CDI diário.
- B3 (empresas listadas): proventos anunciados com data de pagamento.

As funções load_* devolvem o formato bruto das rotas /api/*; history() devolve os candles no
formato usado nos cálculos ({date, o, h, l, c, v, adj}). Falha de uma fonte externa (rede,
HTTP, resposta sem dados) levanta UpstreamError, que o servidor devolve como HTTP 502.

No navegador (versão publicada, runtime.BROWSER) as fontes não mandam CORS: cada URL vai pelo
repasse /proxy do Worker do Cloudflare, que acrescenta o token da brapi. A varredura busca os
históricos em paralelo no JavaScript e os entrega antes (prime), já que lá não há threads.
"""

import base64
import http.cookiejar
import json
import logging
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import config, runtime
from . import database as db
from .core import day_of, in_session_hours, iso_day_in, last_close

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = config.CACHE_TTL_SECONDS
CDI_TTL_SECONDS = config.CDI_TTL_SECONDS
CDI_MAX_WINDOW_DAYS = 3650  # a API do BCB aceita no máximo 10 anos por consulta
PROVENTOS_TTL_SECONDS = config.PROVENTOS_TTL_SECONDS
B3_COMPANY_API = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
# Símbolo do Yahoo por mercado: B3 (padrão, usado pelo screener), EUA, cripto em dólar e
# câmbio (ex.: USDBRL -> USDBRL=X).
YAHOO_SYMBOL = {"b3": "{}.SA", "us": "{}", "crypto": "{}-USD", "fx": "{}=X"}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
PROXY = "/proxy?url="  # repasse do Worker do Cloudflare (só no navegador)


class UpstreamError(RuntimeError):
    """Falha de uma fonte externa (brapi, Yahoo, BCB, B3): rede, HTTP ou resposta sem dados."""


_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def cached(key, loader, ttl=CACHE_TTL_SECONDS):
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


_primed: dict[str, tuple[int, str]] = {}


def prime(url, status, text):
    """Resposta já buscada pelo navegador (status, texto) para a próxima http_json(url)."""
    _primed[url] = (status, text)


def proxy_url(url):
    return PROXY + urllib.parse.quote(url, safe="")


def _error_message(body, status):
    """Mensagem de erro de uma resposta HTTP (brapi: message; Yahoo: chart.error)."""
    if not isinstance(body, dict):
        body = {}
    message = body.get("message") or (body.get("chart") or {}).get("error", {}) or f"HTTP {status}"
    return message if isinstance(message, str) else json.dumps(message)


def _browser_json(url, body=None):
    if body is None:
        status, text = _primed.pop(url, None) or runtime.http_request("GET", proxy_url(url))
    else:
        status, text = runtime.http_request("POST", proxy_url(url), body)
    host = urllib.parse.urlsplit(url).hostname
    if status == 0:
        raise UpstreamError(f"falha ao consultar {host}: {text or 'sem conexão'}")
    try:
        body = json.loads(text)
    except ValueError:
        if status == 200:
            raise UpstreamError(f"falha ao consultar {host}: resposta inválida") from None
        body = {}
    if status != 200:
        raise UpstreamError(_error_message(body, status))
    return body


def http_json(url, body=None, opener=None):
    """GET da URL (ou POST de body, texto JSON) → JSON. opener: urllib com cookies (Yahoo)."""
    if runtime.REMOTE:
        return _browser_json(url, body)
    headers = {"User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, headers=headers, data=body.encode() if body is not None else None)
    try:
        with (opener.open if opener else urllib.request.urlopen)(request, timeout=config.HTTP_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        try:
            body = json.load(err)
        except ValueError:
            body = {}
        raise UpstreamError(_error_message(body, err.code)) from None
    except (OSError, ValueError) as err:  # sem rede, tempo esgotado, JSON inválido
        raise UpstreamError(f"falha ao consultar {urllib.parse.urlsplit(url).hostname}: {err}") from None


# ---------------------------------------------------------------- Lista de ativos (Yahoo)
#
# A lista vem do screener do Yahoo (bolsa SAO), que, ao contrário dos candles (v8/chart), exige
# o cookie de fc.yahoo.com e o "crumb" da sessão. No servidor local, _yahoo_session os obtém;
# publicado, o Worker faz isso no /proxy (POST para YAHOO_SCREENER). A brapi complementa com o
# setor das ações e é a reserva quando o Yahoo falha.

YAHOO_SCREENER = "https://query1.finance.yahoo.com/v1/finance/screener"
YAHOO_PAGE = 250
YAHOO_MIN_SHARES = 1000   # média de 3 meses (em cotas) mínima: descarta símbolos sem negócios
YAHOO_SESSION_SECONDS = 3600
BDR_SUFFIXES = ("31", "32", "33", "34", "35", "39")
STOCK_SUFFIXES = ("3", "4", "5", "6", "7", "8")
_yahoo_session = {"opener": None, "crumb": None, "at": 0.0}
_yahoo_lock = threading.Lock()


# ETFs que não entram no screener, pelo nome: renda fixa e juros (sem pullbacks: LFTS11 oscila
# 0,05% ao dia), cripto (negocia 24/7) e alavancados/inversos (decaimento). O screener ainda
# descarta os de amplitude muito baixa (CONFIG["etfMinRange"]) que escapem desta lista.
ETF_EXCLUDED_WORDS = ("TESOURO", "SELIC", "IMA-B", "IMAB", "IRF-M", "IRFM", "IPCA", "DEBENT", "RENDA FIXA",
                      "JUROS", "CDI", "LFT", "NTN", "CREDITO", "BITCOIN", "ETHER", "CRYPTO", "CRIPTO", "HASHDEX",
                      "SOLANA", "XRP", "ALAVANC", "INVERS", "2X", "3X", "-1X")
FUND_WORDS = ("IMOBILI", "FIAGRO", "AGROINDUSTR", "INFRA", "INCENTIVAD")


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().upper()


def asset_kind(ticker: str, short_name: str = "", long_name: str = "") -> str | None:
    """Tipo do ativo pelo código e pelo nome do Yahoo: "acoes" (ON, PN e units), "fundos" (FII,
    Fiagro e FI-Infra), "etfs" (de ações, sem os de ETF_EXCLUDED_WORDS) ou "bdrs"; None para o
    que não entra (fracionários, direitos, recibos, outros fundos)."""
    if not (5 <= len(ticker) <= 6 and ticker.isascii() and ticker.isalnum() and ticker[4:].isdigit()):
        return None
    suffix = ticker[4:]
    if suffix in STOCK_SUFFIXES:
        return "acoes"
    if suffix in BDR_SUFFIXES:
        return "bdrs"
    if suffix != "11":
        return None
    if "UNT" in _plain(short_name).split():
        return "acoes"
    name = f"{_plain(short_name)} {_plain(long_name)}"
    if "INDICE" in name:
        return None if any(word in name for word in ETF_EXCLUDED_WORDS) else "etfs"
    if any(word in name for word in FUND_WORDS):
        return "fundos"
    return None


# ETF do exterior (classe Internacionais): o nome cita um índice ou mercado de fora e não um do
# Brasil (S&P/B3, Ibovespa, MSCI Brazil...). ETFs de ouro, dólar ou renda fixa ficam nos do Brasil.
INTERNATIONAL_ETF_WORDS = ("EXTERIOR", "S&P 500", "S&P500", "NASDAQ", "GLOBAL", "WORLD", "ALL COUNTRIES",
                           "MSCI US", "MSCI CHINA", " US ", "U.S", "USA", "CHINA", "EMERGING", "FANG", "ARGENTINA",
                           "EX US", "RUSSELL", "SEMICONDUCTOR", "TECHNOLOGY", "HEALTHCARE", "URANIUM", "RARE EARTH",
                           "VIDEO GAMING", "SMALLCAP 600", "LARGE CAP", "NYSE", "INVESTIMENTO NO EXTERIOR", " IE ")
BRAZIL_ETF_WORDS = ("BRASIL", "BRAZIL", "IBOV", "BOVESPA", "B3", "IBRX", "IDIV", "IFIX")


def international_etf(name):
    """O ETF acompanha um índice do exterior (pelo nome do Yahoo/brapi)."""
    words = f" {_plain(name or '')} "
    return any(w in words for w in INTERNATIONAL_ETF_WORDS) and not any(w in words for w in BRAZIL_ETF_WORDS)


def _yahoo_auth(refresh=False):
    """(opener com o cookie, crumb) da sessão do Yahoo, renovados a cada hora."""
    with _yahoo_lock:
        s = _yahoo_session
        if refresh or not s["crumb"] or time.time() - s["at"] > YAHOO_SESSION_SECONDS:
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            headers = {"User-Agent": USER_AGENT}
            try:
                opener.open(urllib.request.Request("https://fc.yahoo.com", headers=headers), timeout=config.HTTP_TIMEOUT_SECONDS)
            except urllib.error.HTTPError:
                pass  # responde 404, mas manda o cookie
            except OSError as err:
                raise UpstreamError(f"falha ao consultar fc.yahoo.com: {err}") from None
            try:
                crumb = opener.open(urllib.request.Request("https://query1.finance.yahoo.com/v1/test/getcrumb", headers=headers),
                                    timeout=config.HTTP_TIMEOUT_SECONDS).read().decode().strip()
            except OSError as err:
                raise UpstreamError(f"Yahoo recusou a sessão (crumb): {err}") from None
            if not crumb or len(crumb) > 64:
                raise UpstreamError("Yahoo recusou a sessão (crumb)")
            s.update(opener=opener, crumb=crumb, at=time.time())
        return s["opener"], s["crumb"]


def yahoo_screener(query: dict) -> dict:
    """POST no screener do Yahoo com a sessão (cookie + crumb); renova a sessão uma vez se recusada."""
    body = json.dumps(query)
    if runtime.REMOTE:
        return http_json(YAHOO_SCREENER, body)
    for refresh in (False, True):
        opener, crumb = _yahoo_auth(refresh)
        try:
            return http_json(f"{YAHOO_SCREENER}?crumb={urllib.parse.quote(crumb)}", body, opener)
        except UpstreamError:
            if refresh:
                raise
            log.info("Yahoo recusou a sessão; renovando", exc_info=True)


def load_yahoo_list() -> list:
    """Todos os ativos da bolsa de São Paulo no Yahoo com negócios nos últimos 3 meses."""
    quotes, offset = [], 0
    while offset < 20 * YAHOO_PAGE:
        query = {"offset": offset, "size": YAHOO_PAGE, "sortField": "ticker", "sortType": "ASC", "quoteType": "EQUITY",
                 "query": {"operator": "AND", "operands": [
                     {"operator": "EQ", "operands": ["exchange", "SAO"]},
                     {"operator": "GT", "operands": ["avgdailyvol3m", YAHOO_MIN_SHARES]}]}}
        result = (((yahoo_screener(query).get("finance") or {}).get("result")) or [{}])[0]
        page = result.get("quotes") or []
        quotes += page
        offset += len(page)
        if not page or offset >= (result.get("total") or 0):
            break
    if not quotes:
        raise UpstreamError("Yahoo: lista de ativos vazia")
    return quotes


def load_brapi_stocks() -> list:
    """Ações à vista e units da brapi, sem fracionários, com setor, fechamento e volume do dia."""
    query = urllib.parse.urlencode({
        "type": "stock", "sortBy": "volume", "sortOrder": "desc", "limit": 400, "token": config.BRAPI_TOKEN,
    })
    data = http_json(f"https://brapi.dev/api/quote/list?{query}")
    return [{"ticker": s["stock"], "name": s.get("name"), "sector": s.get("sector"), "kind": "acoes",
             "close": s.get("close"), "volume": s.get("volume"), "avg_volume": None}
            for s in data.get("stocks", [])
            if not s["stock"].endswith("F") and s.get("close") and s.get("volume")]


def _saved_universe() -> list:
    try:
        return db.read_table("universe") if db.exists("universe") else []
    except Exception:
        log.warning("não foi possível ler a última lista de ativos salva", exc_info=True)
        return []


def load_universe():
    """Ativos da B3 que podem entrar no screener: {ticker, name, sector, kind (asset_kind), close,
    volume (do dia), avg_volume (média de 3 meses, em cotas)}. Lista do Yahoo com o nome e o
    setor da brapi quando ela conhece o ticker. Sem o Yahoo (que às vezes recusa a sessão com
    HTTP 429), a última lista do Yahoo salva (tabela universe), que tem todos os tipos; se a
    salva não veio do Yahoo, as ações da brapi; sem nenhuma, a última lista salva, qualquer que
    seja. A lista de reserva não é gravada por cima da salva."""
    try:
        brapi = {s["ticker"]: s for s in load_brapi_stocks()}
    except Exception as err:
        log.warning("brapi indisponível (%s): sem setor das ações", err)
        brapi = {}
    try:
        assets = []
        for q in load_yahoo_list():
            ticker = (q.get("symbol") or "").removesuffix(".SA")
            kind = asset_kind(ticker, q.get("shortName"), q.get("longName"))
            if not q.get("symbol", "").endswith(".SA") or kind is None or not q.get("regularMarketPrice"):
                continue
            known = brapi.get(ticker, {})
            assets.append({"ticker": ticker, "name": known.get("name") or q.get("longName") or q.get("shortName"),
                           "sector": known.get("sector"), "kind": kind, "close": q["regularMarketPrice"],
                           "volume": q.get("regularMarketVolume") or 0, "avg_volume": q.get("averageDailyVolume3Month")})
    except Exception as err:
        saved = _saved_universe()
        if any(a.get("kind") not in (None, "acoes") for a in saved):
            log.warning("lista do Yahoo indisponível (%s); usando a última lista do Yahoo salva (%d ativos)",
                        err, len(saved))
            return saved
        if brapi:
            log.warning("lista do Yahoo indisponível (%s); usando só as ações da brapi", err)
            return list(brapi.values())
        if saved:
            log.warning("Yahoo e brapi indisponíveis (%s); usando a última lista salva", err)
            return saved
        raise
    try:
        db.write_tables({"universe": assets})
    except Exception:
        log.warning("não foi possível salvar a lista de ativos no banco", exc_info=True)
    return assets


def universe():
    return cached("universe", load_universe)


def history_url(ticker: str, market: str = "b3", range_: str = "10y") -> str:
    symbol = YAHOO_SYMBOL[market].format(ticker)
    return (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
            f"?range={range_}&interval=1d&events=div")


def parse_chart(data: dict, ticker: str) -> dict:
    """Resposta do Yahoo (v8/chart) → {"candles": [...], "dividends": [...]} no formato bruto."""
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise UpstreamError(f"Yahoo sem dados para {ticker}")
    r = result[0]
    quote = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or quote["close"]
    candles = []
    for i, ts in enumerate(r.get("timestamp") or []):
        o, h, l, c, v = (quote[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        candles.append({"date": ts, "open": o, "high": h, "low": l, "close": c,
                        "volume": v or 0, "adjustedClose": adj[i] if adj[i] is not None else c})
    events = ((r.get("events") or {}).get("dividends") or {}).values()
    dividends = sorted(({"date": e["date"], "amount": e["amount"]} for e in events
                        if e.get("date") and e.get("amount")), key=lambda e: e["date"])
    return {"candles": candles, "dividends": dividends}


# ---------------------------------------------------------------- Histórico salvo no banco
#
# O histórico de cada ativo fica salvo (database: tabela history_{mercado}_{ticker}, Parquet
# no servidor e D1 na versão publicada). A atualização diária (update.py, depois do fechamento)
# acrescenta os candles que faltam; durante o dia, o que está salvo é usado como está:
#
# - salvo até o último pregão fechado (last_closed_day) e fora do horário do pregão: nenhuma
#   requisição ao Yahoo;
# - salvo em dia e pregão em andamento: pede só range=5d, para o candle do dia (não é salvo);
# - salvo atrasado (a atualização diária não rodou, ou ativo fora dela, como os da carteira):
#   pede o menor range que cobre do último candle salvo até hoje, com folga, e salva;
# - nada salvo, ou salvo no formato antigo de 5 anos (legacy_history): os 10 anos.
#
# O fechamento do Yahoo é ajustado por desdobramentos e o adjustedClose, por proventos: um
# evento novo muda o passado inteiro. Por isso os dias que o trecho novo e o salvo têm em comum
# são comparados; qualquer diferença (desdobramento, grupamento, bonificação, provento novo,
# correção) baixa de novo os 10 anos. Só candles de pregões já fechados são salvos.

HISTORY_DAYS = 3652            # 10 anos: candles mais antigos são descartados
FULL_RANGE = "10y"             # download completo
LEGACY_HISTORY_DAYS = 1826     # histórico salvo antes, cortado em 5 anos
HISTORY_OVERLAP_DAYS = 7       # folga: dias já salvos que o trecho novo também traz
HISTORY_TOLERANCE = 1e-4       # diferença relativa de fechamento que conta como mudança do passado
LIVE_RANGE = "5d"              # candle do dia, com alguns dias salvos para comparar
# Ranges do Yahoo e quantos dias de calendário cada um cobre com certeza.
HISTORY_RANGES = (("1mo", 28), ("3mo", 89), ("6mo", 181), ("1y", 365), ("2y", 730))
# Fuso que define o dia de um candle: cripto fecha à meia-noite UTC; câmbio, à de Londres.
DAY_ZONE = {"crypto": "UTC", "fx": "Europe/London"}

_prefetched: dict[str, list] = {}  # linhas salvas já lidas em lote (varredura no navegador)


def history_table(ticker: str, market: str = "b3") -> str | None:
    """Nome da tabela do histórico salvo; None para ticker que não cabe num nome de tabela."""
    if not ticker.isascii() or not ticker.isalnum() or len(ticker) > 12:
        return None
    return f"{db.HISTORY_PREFIX}{market}_{ticker.lower()}"


def _day(ts: float, market: str) -> str:
    return iso_day_in(ts, DAY_ZONE.get(market, "America/Sao_Paulo"))


def _previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def last_closed_day(market: str, now: float) -> str:
    """Dia do último candle já fechado do mercado (feriados não são conhecidos e contam como
    dia de pregão): B3 e EUA, o último dia útil depois das 18h de Brasília; cripto, ontem em
    UTC; câmbio, o dia útil anterior ao de hoje em Londres."""
    if market == "crypto":
        return iso_day_in(now - 86400, "UTC")
    if market == "fx":
        return _previous_weekday(date.fromisoformat(iso_day_in(now, "Europe/London"))).isoformat()
    return day_of(last_close(now))


def live_session(market: str, now: float) -> bool:
    """Há um candle do dia em andamento? (cripto: sempre; câmbio: dia útil em Londres)."""
    if market == "crypto":
        return True
    if market == "fx":
        return date.fromisoformat(iso_day_in(now, "Europe/London")).weekday() < 5
    return in_session_hours(now)


def _rows_to_history(rows: list) -> dict:
    candles = [{"date": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
                "volume": r["volume"] or 0, "adjustedClose": r["adj"]} for r in rows if r.get("dividend") is None]
    dividends = [{"date": r["date"], "amount": r["dividend"]} for r in rows if r.get("dividend") is not None]
    return {"candles": candles, "dividends": dividends}


def _history_to_rows(hist: dict) -> list:
    return ([{"date": c["date"], "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
              "volume": int(c["volume"] or 0), "adj": c["adjustedClose"]} for c in hist["candles"]]
            + [{"date": d["date"], "dividend": d["amount"]} for d in hist["dividends"]])


def prefetch_stored(assets):
    """Lê numa só consulta o histórico salvo de vários (ticker, mercado), para o próximo
    stored_history de cada um (no navegador, uma ida ao D1 por lote da varredura)."""
    names = [n for n in (history_table(t, m) for t, m in assets) if n]
    _prefetched.clear()
    if not names:
        return
    try:
        _prefetched.update(db.read_tables(names))
    except Exception:
        log.warning("não foi possível ler os históricos salvos", exc_info=True)
        _prefetched.update({n: [] for n in names})


def stored_history(ticker: str, market: str = "b3", consume=False) -> dict | None:
    """Histórico salvo no banco (formato bruto) ou None se não há. Falha do banco = None."""
    name = history_table(ticker, market)
    if name is None:
        return None
    if name in _prefetched:
        rows = _prefetched.pop(name) if consume else _prefetched[name]
    else:
        try:
            rows = db.read_table(name)
        except Exception:
            log.warning("não foi possível ler o histórico salvo de %s", ticker, exc_info=True)
            rows = []
    hist = _rows_to_history(rows)
    return hist if hist["candles"] else None


def legacy_history(stored: dict) -> bool:
    """Salvo quando o histórico era cortado em 5 anos? O corte deixava do primeiro ao último
    candle entre LEGACY_HISTORY_DAYS menos alguns dias (fim de semana e feriado nas duas pontas)
    e LEGACY_HISTORY_DAYS; esse histórico é baixado de novo com os 10 anos. Um ativo que tem de
    fato ~5 anos de pregão cai na mesma faixa e é baixado de novo só nos poucos dias em que o
    seu intervalo passa por ela."""
    span = (stored["candles"][-1]["date"] - stored["candles"][0]["date"]) / 86400
    return LEGACY_HISTORY_DAYS - 10 <= span <= LEGACY_HISTORY_DAYS + 1


def update_range(stored: dict | None, market: str, now: float) -> str | None:
    """Range a pedir ao Yahoo agora (ver acima); None = o salvo basta."""
    if not stored or legacy_history(stored):
        return FULL_RANGE
    last = stored["candles"][-1]["date"]
    if _day(last, market) >= last_closed_day(market, now):
        return LIVE_RANGE if live_session(market, now) else None
    days = (now - last) / 86400 + HISTORY_OVERLAP_DAYS
    for range_, covered in HISTORY_RANGES:
        if days <= covered:
            return range_
    return FULL_RANGE


def update_url(ticker: str, market: str = "b3", now: float | None = None) -> str | None:
    """URL que load_history vai pedir agora; None se não vai pedir nada (histórico no cache em
    memória, ou o salvo basta)."""
    with _cache_lock:
        hit = _cache.get(f"history:{market}:{ticker}")
    now = time.time() if now is None else now
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return None
    range_ = update_range(stored_history(ticker, market), market, now)
    return history_url(ticker, market, range_) if range_ else None


def _differs(a, b) -> bool:
    return abs(a - b) > HISTORY_TOLERANCE * max(abs(a), abs(b))


def merge_history(stored: dict, fresh: dict, market: str) -> dict | None:
    """Salvo + trecho novo. None quando o passado mudou (fechamento ou fechamento ajustado de
    um dia em comum diferente) ou não há dia em comum: aí é preciso baixar os 10 anos."""
    if not fresh["candles"]:
        return None
    first = _day(fresh["candles"][0]["date"], market)
    fresh_by_day = {_day(c["date"], market): c for c in fresh["candles"]}
    overlap = [c for c in stored["candles"] if _day(c["date"], market) >= first]
    if not overlap:
        return None
    for c in overlap:
        f = fresh_by_day.get(_day(c["date"], market))
        if f and (_differs(c["close"], f["close"]) or _differs(c["adjustedClose"], f["adjustedClose"])):
            return None
    return {"candles": [c for c in stored["candles"] if _day(c["date"], market) < first] + fresh["candles"],
            "dividends": [d for d in stored["dividends"] if _day(d["date"], market) < first] + fresh["dividends"]}


def _save_history(ticker, market, stored, hist, now):
    """Grava os candles de pregões já fechados (e os proventos), se mudaram desde o salvo."""
    name = history_table(ticker, market)
    if name is None:
        return
    last = last_closed_day(market, now)
    closed = {"candles": [c for c in hist["candles"] if _day(c["date"], market) <= last],
              "dividends": [d for d in hist["dividends"] if _day(d["date"], market) <= last]}
    if not closed["candles"] or closed == stored:
        return
    try:
        db.write_tables({name: _history_to_rows(closed)})
    except Exception:
        log.warning("não foi possível salvar o histórico de %s", ticker, exc_info=True)


def load_history(ticker: str, market: str = "b3", now: float | None = None) -> dict:
    """Candles diários de 10 anos do Yahoo Finance (OHLC sem ajuste por proventos) e proventos,
    a partir do histórico salvo no banco mais o que falta (ver acima).

    market escolhe o símbolo do Yahoo (YAHOO_SYMBOL): "b3" -> PETR4.SA, "us" -> VOO,
    "crypto" -> BTC-USD, "fx" -> USDBRL=X. Fora da B3 os preços vêm na moeda do ativo (US$
    para EUA e cripto); a conversão para reais é feita em portfolio.convert_history.

    10 anos dão ao backtest da estratégia (compra no pullback, venda abaixo das três médias)
    mais operações por ativo — cada posição dura em média ~160 pregões. O screener não usa
    adjustedClose; a carteira o usa só para a rentabilidade do BOVA11.
    dividends: [{"date": ts, "amount": valor por cota}], em ordem de data. A data do Yahoo
    é a data-ex (primeiro pregão sem direito); a data-com é o pregão anterior.
    """
    now = time.time() if now is None else now
    stored = stored_history(ticker, market, consume=True)
    range_ = update_range(stored, market, now)
    if range_ is None:
        hist = stored
    else:
        hist = parse_chart(http_json(history_url(ticker, market, range_)), ticker)
        if range_ != FULL_RANGE:
            merged = merge_history(stored, hist, market)
            if merged is None:
                log.info("histórico de %s mudou no passado (desdobramento ou provento); baixando 10 anos", ticker)
                merged = parse_chart(http_json(history_url(ticker, market)), ticker)
            hist = merged
    start = now - HISTORY_DAYS * 86400
    hist = {"candles": [c for c in hist["candles"] if c["date"] >= start],
            "dividends": [d for d in hist["dividends"] if d["date"] >= start]}
    if range_ is not None:
        _save_history(ticker, market, stored, hist, now)
    return {"ticker": ticker, **hist}


def raw_history(ticker: str, market: str = "b3") -> dict:
    # O mercado entra na chave: BTC (cripto) e um eventual BTC da B3 são ativos diferentes.
    return cached(f"history:{market}:{ticker}", lambda: load_history(ticker, market))


def _noon(iso: str) -> int:
    """Meio-dia de Brasília (15:00 UTC) do dia iso, em unix (s)."""
    return int(datetime.fromisoformat(iso).replace(hour=15, tzinfo=timezone.utc).timestamp())


def restamp_days(hist: dict, zone: str) -> dict:
    """Cripto (dia em UTC) e câmbio (dia em Londres) têm candles com horário 00:00 UTC / 23:00
    UTC, que no fuso da B3 caem no dia anterior. Recarimba cada candle ao meio-dia (Brasília) do
    dia do próprio mercado, para que day_of devolva a data certa; mantém o último candle por dia."""
    by_day = {}
    for c in hist["candles"]:
        iso = iso_day_in(c["date"], zone)
        by_day[iso] = {**c, "date": _noon(iso)}
    dividends = [{**d, "date": _noon(iso_day_in(d["date"], zone))} for d in hist["dividends"]]
    return {"candles": sorted(by_day.values(), key=lambda c: c["date"]), "dividends": dividends}


BAD_CANDLE_RATIO = 0.5  # fechamento 50%+ longe dos vizinhos dos dois lados = dado corrompido
BAD_CANDLE_NEIGHBORS = 5


def drop_bad_candles(candles: list, ticker: str = "") -> list:
    """Descarta candles isolados com fechamento absurdo (o Yahoo tem, por exemplo, XPML11 a
    ~1,07 em vez de ~110 de 14 a 16/01/2026), que contaminariam a MM200 por 200 pregões: o
    fechamento fica mais de BAD_CANDLE_RATIO longe da mediana dos BAD_CANDLE_NEIGHBORS anteriores
    e também da dos seguintes (a mediana tolera até 3 candles ruins seguidos). Uma mudança de
    patamar (só de um lado) não é descartada, nem o primeiro e o último candles."""
    closes = [c["c"] for c in candles]
    n, k = len(closes), BAD_CANDLE_NEIGHBORS

    def far(i, window):
        if len(window) < 3:
            return False
        return abs(closes[i] / sorted(window)[len(window) // 2] - 1) > BAD_CANDLE_RATIO

    kept = [c for i, c in enumerate(candles)
            if not (far(i, closes[max(0, i - k):i]) and far(i, closes[i + 1:i + 1 + k]))]
    if len(kept) < n:
        log.warning("%s: %d candle(s) com fechamento fora do padrão descartado(s)", ticker, n - len(kept))
    return kept


def history(ticker: str, market: str = "b3") -> dict:
    """Histórico completo de um ticker na moeda do mercado: candles e proventos (data-ex e valor
    por cota). Preços sem ajuste por proventos (o Yahoo já ajusta desdobramentos), como o
    gráfico padrão do TradingView: o ajuste por dividendos rebaixa o passado e infla a MM200.
    adj (fechamento ajustado) existe só para medir o retorno total do BOVA11 na carteira.
    Cripto e câmbio têm os dias recarimbados (restamp_days) para casar com o calendário da B3."""
    data = raw_history(ticker, market)
    candles = sorted((c for c in data.get("candles") or []
                      if c.get("close") and c.get("high") and c.get("low") and c.get("open")),
                     key=lambda c: c["date"])
    candles = [{"date": c["date"], "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"],
                "v": c.get("volume") or 0, "adj": c.get("adjustedClose") or c["close"]} for c in candles]
    hist = {"candles": drop_bad_candles(candles, ticker), "dividends": list(data.get("dividends") or [])}
    if market not in ("crypto", "fx"):
        return hist
    if not candles:
        raise UpstreamError(f"sem cotações para {ticker}")
    return restamp_days(hist, "UTC" if market == "crypto" else "Europe/London")


def load_cdi(start: date) -> dict:
    """Taxas diárias do CDI (% ao dia) desde start, em janelas de até 10 anos (limite do BCB)."""
    rates: list[dict] = []
    today = date.today()
    while start <= today:
        end = min(start + timedelta(days=CDI_MAX_WINDOW_DAYS - 1), today)
        query = urllib.parse.urlencode({"formato": "json", "dataInicial": start.strftime("%d/%m/%Y"),
                                        "dataFinal": end.strftime("%d/%m/%Y")})
        rows = http_json(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?{query}")
        for row in rows if isinstance(rows, list) else []:
            day = datetime.strptime(row["data"], "%d/%m/%Y").date()
            rates.append({"date": day.isoformat(), "rate": float(row["valor"])})
        start = end + timedelta(days=1)
    return {"rates": rates}


def cdi(start: date) -> dict:
    return cached(f"cdi:{start}", lambda: load_cdi(start), CDI_TTL_SECONDS)


def b3_call(method: str, params: dict):
    """API pública da B3 (a mesma da página de empresas listadas): parâmetros em JSON base64."""
    payload = base64.b64encode(json.dumps(params).encode()).decode()
    data = http_json(f"{B3_COMPANY_API}/{method}/{payload}")
    # Conforme o cabeçalho Accept, a B3 devolve o JSON dentro de uma string.
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise UpstreamError("B3: resposta inválida") from None
    return data


def b3_date(text: str):
    """"DD/MM/AAAA" -> "AAAA-MM-DD"; vazio ou data-sentinela (pagamento a definir) -> None."""
    try:
        day = datetime.strptime((text or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None
    return day.isoformat() if day.year < 9000 else None


def load_proventos(ticker: str) -> dict:
    """Proventos em dinheiro anunciados para um ticker da B3, com data-com e data de pagamento.

    A B3 lista os proventos da empresa (ou do fundo) dos últimos ~12 meses por ISIN; o ISIN
    do ticker vem de GetDetail (ações e units). Fundos (codeCVM "0") não têm essa lista: o
    ISIN das cotas é BR{base}CTF... (os R..M.. são recibos de subscrição). Ticker sem
    cobertura (ETF, BDR) devolve events vazio.
    events: [{type, rate (R$ por cota), comDate, paymentDate (None = a definir), relatedTo}].
    """
    base = ticker[:4]
    rows = b3_call("GetListedSupplementCompany", {"issuingCompany": base, "language": "pt-br"})
    company = rows[0] if isinstance(rows, list) and rows else {}
    code_cvm = str(company.get("codeCVM") or "0")
    if code_cvm != "0":
        detail = b3_call("GetDetail", {"codeCVM": code_cvm, "language": "pt-br"})
        isins = {c.get("isin") for c in (detail or {}).get("otherCodes") or [] if c.get("code") == ticker}
        match = lambda isin: isin in isins
    else:
        prefix = f"BR{base}CTF"
        match = lambda isin: ticker.endswith("11") and (isin or "").startswith(prefix)
    events = []
    for d in company.get("cashDividends") or []:
        com_date = b3_date(d.get("lastDatePrior"))
        try:
            rate = float((d.get("rate") or "").replace(".", "").replace(",", "."))
        except ValueError:
            continue
        if match(d.get("isinCode")) and com_date and rate > 0:
            events.append({"type": (d.get("label") or "").strip(), "rate": rate, "comDate": com_date,
                           "paymentDate": b3_date(d.get("paymentDate")),
                           "relatedTo": (d.get("relatedTo") or "").strip()})
    events.sort(key=lambda e: e["comDate"])
    return {"ticker": ticker, "events": events}


def proventos(ticker: str) -> dict:
    return cached(f"proventos:{ticker}", lambda: load_proventos(ticker), PROVENTOS_TTL_SECONDS)
