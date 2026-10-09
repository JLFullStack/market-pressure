"""MarketPressure: screener de swing trade da B3 (setup "Garrafa de Gás") e acompanhamento de carteiras.

Camadas (cada uma só usa as de baixo):

    server             HTTP: rotas /api/* e arquivos de web/
    screener           varredura, ranking e KPI do BOVA11
    portfolio_store    carteiras no banco e as ações que as alteram
    portfolio_service  carrega os dados e monta os payloads da aba Carteira
    portfolio          cálculos puros da carteira (sem rede nem disco)
    market_data        fontes externas (brapi, Yahoo, BCB, B3) com cache em memória
    database           tabelas Parquet em data/
    core               relógio da B3, indicadores, regras do setup, formatadores
    config             caminhos, endereço do servidor, token e tempos de cache (.env)
"""
