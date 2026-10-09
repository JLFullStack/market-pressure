"""Coleta de dados de mercado do MarketPressure, com cache em memória.

- brapi: lista de ações da B3 (o plano gratuito libera 1 ticker por requisição e 3 meses de
  histórico, por isso só a lista vem dela).
- Yahoo Finance: candles diários de 5 anos e proventos (data-ex) por mercado.
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
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import config, runtime
from . import database as db
from .core import iso_day_in

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


def _browser_json(url):
    status, text = _primed.pop(url, None) or runtime.http_request("GET", proxy_url(url))
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


def http_json(url):
    if runtime.BROWSER:
        return _browser_json(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=config.HTTP_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        try:
            body = json.load(err)
        except ValueError:
            body = {}
        raise UpstreamError(_error_message(body, err.code)) from None
    except (OSError, ValueError) as err:  # sem rede, tempo esgotado, JSON inválido
        raise UpstreamError(f"falha ao consultar {urllib.parse.urlsplit(url).hostname}: {err}") from None


def load_universe():
    """Lista de ações à vista e units da B3, sem fracionários, com fechamento e volume do dia."""
    try:
        query = urllib.parse.urlencode({
            "type": "stock", "sortBy": "volume", "sortOrder": "desc", "limit": 400, "token": config.BRAPI_TOKEN,
        })
        data = http_json(f"https://brapi.dev/api/quote/list?{query}")
        stocks = [
            {"ticker": s["stock"], "name": s.get("name"), "sector": s.get("sector"),
             "close": s.get("close"), "volume": s.get("volume")}
            for s in data.get("stocks", [])
            if not s["stock"].endswith("F") and s.get("close") and s.get("volume")
        ]
        try:
            db.write_tables({"universe": stocks})
        except Exception:
            log.warning("não foi possível salvar a lista de ações no banco", exc_info=True)
        return stocks
    except Exception as err:
        # Sem a brapi, reaproveita a última lista salva no banco (tabela universe).
        if db.exists("universe"):
            log.warning("brapi indisponível (%s); usando a última lista salva", err)
            return db.read_table("universe")
        raise


def universe():
    return cached("universe", load_universe)


def history_url(ticker: str, market: str = "b3") -> str:
    symbol = YAHOO_SYMBOL[market].format(ticker)
    return (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
            "?range=5y&interval=1d&events=div")


def load_history(ticker: str, market: str = "b3") -> dict:
    """Candles diários de 5 anos do Yahoo Finance (OHLC sem ajuste por proventos) e proventos.

    market escolhe o símbolo do Yahoo (YAHOO_SYMBOL): "b3" -> PETR4.SA, "us" -> VOO,
    "crypto" -> BTC-USD, "fx" -> USDBRL=X. Fora da B3 os preços vêm na moeda do ativo (US$
    para EUA e cripto); a conversão para reais é feita em portfolio.convert_history.

    5 anos dão ao backtest da estratégia (compra no pullback, venda abaixo das três médias)
    mais operações por ativo — cada posição dura em média ~160 pregões. O screener não usa
    adjustedClose; a carteira o usa só para a rentabilidade do BOVA11.
    dividends: [{"date": ts, "amount": valor por cota}], em ordem de data. A data do Yahoo
    é a data-ex (primeiro pregão sem direito); a data-com é o pregão anterior.
    """
    data = http_json(history_url(ticker, market))
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
    return {"ticker": ticker, "candles": candles, "dividends": dividends}


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
    hist = {"candles": candles, "dividends": list(data.get("dividends") or [])}
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
