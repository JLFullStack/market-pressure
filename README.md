# MarketPressure

Screener de swing trade da B3 (setup "Garrafa de Gás": pullback a uma média dentro de uma
tendência de alta da MM200) e acompanhamento de carteiras (B3, EUA e cripto, proventos,
metas por classe, calculadora de aporte e rentabilidade contra CDI e BOVA11).

## Como rodar

```
pip install -r requirements.txt     # pyarrow (uma vez)
copy .env.example .env              # uma vez; preencha BRAPI_TOKEN
python server.py                    # abra http://127.0.0.1:8765
```

## Testes

```
python -m unittest discover -s tests -t .
```

Rodam sem rede, num banco temporário (o banco real em `data/` não é tocado).

## Estrutura

```
server.py            inicia o servidor (o mesmo que python -m marketpressure)
marketpressure/      código Python: cálculos, dados e servidor HTTP
  config.py            caminhos, porta, token e tempos de cache (.env)
  core.py              relógio da B3, indicadores, regras de entrada e saída
  market_data.py       brapi, Yahoo Finance, Banco Central e B3, com cache
  database.py          tabelas Parquet em data/
  screener.py          varredura, pressão, backtest e ranking
  portfolio.py         cálculos puros da carteira
  portfolio_service.py carrega os dados e monta as respostas da aba Carteira
  portfolio_store.py   carteiras no banco e as ações que as alteram
  server.py            rotas HTTP
web/                 páginas, CSS e JavaScript (só exibição)
data/                banco de dados (não versionado)
tests/               testes automatizados
```

Todo cálculo fica no Python; o JavaScript só busca os resultados prontos e os exibe.
Detalhes da arquitetura, do contrato da API e das regras estão em `CLAUDE.md`.
