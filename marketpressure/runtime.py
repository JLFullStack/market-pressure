"""Ambiente de execução: servidor local (CPython) ou navegador (Pyodide, versão publicada).

Publicado no Cloudflare, o mesmo pacote roda dentro do navegador (Pyodide, num Web Worker:
web/assets/js/py-worker.js). Lá não há threads nem rede pelo urllib: as requisições são
XMLHttpRequest síncronas (permitidas num Web Worker) para a mesma origem, onde o Worker do
Cloudflare (worker/index.js) repassa as fontes externas (/proxy) e guarda o banco no D1 (/db);
o que rodaria em paralelo roda em sequência.

A atualização diária (marketpressure/update.py, no GitHub Actions) usa o mesmo caminho fora do
navegador: use_remote troca http_request por requisições ao site publicado, com o token de
serviço, e REMOTE faz o banco e as fontes externas irem pelo /db e pelo /proxy do Worker.
"""

import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BROWSER = sys.platform == "emscripten"
REMOTE = BROWSER  # banco no D1 e fontes pelo /proxy (navegador ou use_remote)
HTTP_TIMEOUT_MS = 30000


def run_all(fn, items, workers=8):
    """Gera (item, resultado, erro) de fn(item) para cada item, na ordem dos itens: em threads
    no servidor, em sequência no navegador."""
    items = list(items)
    if BROWSER:
        for item in items:
            try:
                yield item, fn(item), None
            except Exception as err:
                yield item, None, err
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [(item, pool.submit(fn, item)) for item in items]
        for item, future in futures:
            try:
                yield item, future.result(), None
            except Exception as err:
                yield item, None, err


def http_request(method, url, body=None):
    """Requisição síncrona no navegador para a mesma origem: (status, texto). status 0 = sem
    resposta (rede, tempo esgotado); o texto traz o motivo."""
    from js import XMLHttpRequest  # só existe no Pyodide

    xhr = XMLHttpRequest.new()
    try:
        xhr.open(method, url, False)
        xhr.timeout = HTTP_TIMEOUT_MS
        if body is not None:
            xhr.setRequestHeader("Content-Type", "application/json")
            xhr.send(body)
        else:
            xhr.send()
    except Exception as err:  # JsException: sem rede ou tempo esgotado
        return 0, str(err)
    return xhr.status, xhr.responseText


def use_remote(base_url, token):
    """Fora do navegador, fala com o site publicado (base_url) como o navegador fala: /db e
    /proxy, autenticado pelo token de serviço (segredo SERVICE_TOKEN do Worker)."""
    global REMOTE, http_request
    base_url = base_url.rstrip("/")

    def remote_request(method, url, body=None):
        request = urllib.request.Request(base_url + url, method=method,
                                         data=body.encode() if body is not None else None,
                                         headers={"Authorization": f"Bearer {token}",
                                                  "Content-Type": "application/json",
                                                  "User-Agent": "MarketPressure-update"})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_MS / 1000) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode(errors="replace")
        except OSError as err:  # sem rede, tempo esgotado
            return 0, str(err)

    REMOTE, http_request = True, remote_request
