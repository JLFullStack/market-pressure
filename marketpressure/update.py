"""Atualização diária, depois do fechamento da B3: python -m marketpressure.update

Acrescenta ao histórico salvo de cada ação do universo (e do BOVA11 e do USDBRL, usados pela
carteira) os candles que faltam e roda a varredura do screener, que fica salva no banco. A
varredura feita depois do fechamento vale até a abertura do próximo pregão (ScanJob._fresh),
então ninguém precisa varrer de novo até lá, e todos veem os mesmos dados.

- Publicado: roda no GitHub Actions (.github/workflows/atualizacao-diaria.yml) com
  MARKETPRESSURE_REMOTE_URL (o site) e MARKETPRESSURE_SERVICE_TOKEN (segredo SERVICE_TOKEN do
  Worker): o banco é o D1 e as fontes vão pelo /proxy, como no navegador (runtime.use_remote).
- Local: sem essas variáveis, usa o banco em data/ e as fontes direto (o servidor local também
  faz isto sozinho, ver server.daily_update).

Sai com código 1 se a varredura falhar ou não puder ser salva.
"""

import logging
import os
import sys

from . import config, runtime

log = logging.getLogger("marketpressure.update")

# Históricos da carteira que não estão no universo do screener.
EXTRA_HISTORIES = (("BOVA11", "b3"), ("USDBRL", "fx"))


def run():
    """Atualiza os históricos e salva a varredura; devolve a varredura."""
    from . import market_data, screener  # depois de use_remote: o screener lê o banco ao ser importado

    for ticker, market in EXTRA_HISTORIES:
        try:
            market_data.raw_history(ticker, market)
        except Exception:
            log.warning("não foi possível atualizar o histórico de %s", ticker, exc_info=True)

    def progress(done, total):
        if done == total or done % 25 == 0:
            log.info("varredura: %d de %d", done, total)

    scan = screener.run_scan(progress)
    screener.write_scan(scan)
    log.info("varredura salva: %d ativos analisados, %d falhas", len(scan["analyses"]), len(scan["failures"]))
    return scan


def main():
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    remote_url = os.environ.get("MARKETPRESSURE_REMOTE_URL", "")
    if remote_url:
        token = os.environ.get("MARKETPRESSURE_SERVICE_TOKEN", "")
        if not token:
            log.error("MARKETPRESSURE_SERVICE_TOKEN não definido")
            return 1
        runtime.use_remote(remote_url, token)
        log.info("banco e fontes pelo site publicado: %s", remote_url)
    try:
        run()
    except Exception:
        log.exception("atualização diária falhou")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
