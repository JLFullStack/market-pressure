"""Inicia o MarketPressure:  python server.py  e abra http://127.0.0.1:8765

O código fica no pacote marketpressure/ (veja marketpressure/__init__.py); este arquivo só
mantém o comando de sempre. Equivale a  python -m marketpressure.
"""

from marketpressure.server import main

if __name__ == "__main__":
    main()
