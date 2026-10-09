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

## Versão publicada (Cloudflare)

Publicada em `https://marketpressure.lucasaraujo.dev.br`, sem servidor Python: o navegador
roda o próprio pacote `marketpressure` (Pyodide) e um Worker do Cloudflare serve as páginas,
repassa as fontes de dados (`/proxy`) e guarda o banco no D1 (`/db`). Detalhes em `CLAUDE.md`
("Publicação"). Para testar localmente: `npm install` e `npm run dev` (http://127.0.0.1:8787).

Configuração única no painel do Cloudflare:

1. **Domínio**: Add a domain → `lucasaraujo.dev.br` (plano Free) e, no registro.br, troque os
   servidores DNS pelos dois nameservers que o Cloudflare indicar. Espere o domínio ficar ativo.
2. **Banco**: Storage & Databases → D1 → Create → nome `market-pressure`; copie o *Database ID*
   para `database_id` em `wrangler.jsonc` (já preenchido: banco `market-pressure`).
3. **Worker**: Workers & Pages → Create → Import a repository → este repositório, com
   Build command vazio e Deploy command `npx wrangler deploy` (o build roda pelo `wrangler.jsonc`).
4. **Token**: no Worker, Settings → Variables and Secrets → Add → Secret `BRAPI_TOKEN`.

O domínio `marketpressure.lucasaraujo.dev.br` é criado no deploy (`routes` em `wrangler.jsonc`).
Ainda não há login: quem tiver o endereço vê e altera as carteiras.

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
  runtime.py           servidor (threads, urllib) ou navegador (Pyodide)
  api.py               rotas /api/* (as mesmas no servidor e no navegador)
  server.py            servidor HTTP local
  browser.py           ponte do navegador (versão publicada)
web/                 páginas, CSS e JavaScript (só exibição)
data/                banco de dados (não versionado)
tests/               testes automatizados
worker/index.js      Worker do Cloudflare (repasse /proxy, banco /db no D1, arquivos)
scripts/build.mjs    monta dist/ para o Cloudflare (páginas + pacote Python)
wrangler.jsonc       configuração do Cloudflare
```

Todo cálculo fica no Python; o JavaScript só busca os resultados prontos e os exibe.
Detalhes da arquitetura, do contrato da API e das regras estão em `CLAUDE.md`.
