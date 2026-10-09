"""Ambiente de execução: servidor local (CPython) ou navegador (Pyodide, versão publicada).

Publicado no Cloudflare, o mesmo pacote roda dentro do navegador (Pyodide, num Web Worker:
web/assets/js/py-worker.js). Lá não há threads nem rede pelo urllib: as requisições são
XMLHttpRequest síncronas (permitidas num Web Worker) para a mesma origem, onde o Worker do
Cloudflare (worker/index.js) repassa as fontes externas (/proxy) e guarda o banco no D1 (/db);
o que rodaria em paralelo roda em sequência.
"""

import sys
from concurrent.futures import ThreadPoolExecutor

BROWSER = sys.platform == "emscripten"
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
