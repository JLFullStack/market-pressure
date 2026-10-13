"""Ponte do navegador (versão publicada no Cloudflare): o Web Worker web/assets/js/py-worker.js
roda este pacote no Pyodide e chama handle() para cada pedido /api/* da página, com as mesmas
rotas e respostas do servidor local (api.py).

A varredura do screener não cabe numa chamada só (sem threads, ela travaria o Worker por
minutos): scan_job.start só marca o pedido, e o Worker, entre um pedido e outro, conduz a
varredura em etapas: scan_begin (lista de ações e URLs dos históricos), busca as URLs em
paralelo e as entrega com prime, scan_step(i) para cada ação e scan_end. Enquanto isso, a
página continua consultando /api/scan e recebe o progresso.
"""

import json
import urllib.parse

from . import api, market_data, runtime, screener

_scan = {"requested": False, "universe": [], "analyses": [], "failures": []}


def handle(method, path, body_text=None):
    """Pedido da página → JSON {"status", "body"}."""
    url = urllib.parse.urlsplit(path)

    def read_body():
        if not body_text:
            raise api.HTTPError(400, "corpo ausente ou maior que 2 MB")
        try:
            return json.loads(body_text)
        except ValueError:
            raise api.HTTPError(400, "JSON inválido") from None

    status, payload = api.call(method, url.path, urllib.parse.parse_qs(url.query), read_body)
    return json.dumps({"status": status, "body": api.clean(payload)}, ensure_ascii=False)


def prime(url, status, text):
    market_data.prime(url, status, text)


# ---------------------------------------------------------------- Varredura em etapas


def _request_scan():
    _scan["requested"] = True


if runtime.BROWSER:
    screener.scan_job.start = _request_scan


def take_scan_request():
    """True uma vez depois que scan_job.status pediu uma varredura."""
    requested, _scan["requested"] = _scan["requested"], False
    return requested


def scan_begin():
    """Lista de ações e as URLs dos históricos (JSON), ou "null" se a lista falhou."""
    try:
        universe = screener.load_universe()
    except Exception as err:
        screener.scan_job.fail(err)
        return "null"
    _scan.update(universe=universe, analyses=[], failures=[])
    screener.scan_job._progress(0, len(universe))
    return json.dumps([market_data.history_url(a["ticker"]) for a in universe])


def scan_step(index):
    asset = _scan["universe"][index]
    try:
        analysis = screener.analyze_ticker(asset)
        if analysis is not None:  # None = abaixo do piso de liquidez
            _scan["analyses"].append(analysis)
    except Exception as err:
        _scan["failures"].append({"ticker": asset["ticker"], "reason": str(err)})
    screener.scan_job._progress(index + 1, len(_scan["universe"]))


def scan_end():
    try:
        scan = screener.build_scan(_scan["analyses"], _scan["failures"])
        screener.scan_job.finish(scan)
    except Exception as err:
        screener.scan_job.fail(err)
    finally:
        _scan.update(universe=[], analyses=[], failures=[])
