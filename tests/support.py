"""Apoio dos testes: banco temporário e cotações sintéticas (nada de rede nem do banco real)."""

import math
import shutil
import tempfile
import unittest
from pathlib import Path

from marketpressure import database, market_data

DAY = 86400
START = 1609502400  # 2021-01-01 12:00 UTC (09:00 em Brasília)


def candles(n=400, drift=0.0015, wave=0.05, period=40, start=START):
    """Candles diários no formato de market_data.history: tendência de alta com ondas, que
    geram pullbacks às médias (sinais do setup) ao longo do histórico."""
    out = []
    for t in range(n):
        p = 100 * math.exp(drift * t) * (1 + wave * math.sin(2 * math.pi * t / period))
        out.append({"date": start + t * DAY, "o": p * 0.998, "h": p * 1.01, "l": p * 0.99, "c": p,
                    "v": 1e6 * (1 + 0.3 * math.cos(t)), "adj": p})
    return out


def raw_history(ticker, n=400, **kwargs):
    """Mesmo histórico no formato bruto do Yahoo (market_data.raw_history)."""
    return {"ticker": ticker, "dividends": [], "candles": [
        {"date": c["date"], "open": c["o"], "high": c["h"], "low": c["l"], "close": c["c"],
         "volume": c["v"], "adjustedClose": c["adj"]} for c in candles(n, **kwargs)]}


class DatabaseTestCase(unittest.TestCase):
    """Cada teste usa um banco vazio numa pasta temporária e cotações sintéticas."""

    def setUp(self):
        self._dirs = (database.DATA_DIR, database.BACKUP_DIR)
        self.data_dir = Path(tempfile.mkdtemp(prefix="marketpressure-test-")) / "data"
        database.DATA_DIR, database.BACKUP_DIR = self.data_dir, self.data_dir / "backup"
        self._raw_history = market_data.raw_history
        market_data.raw_history = lambda ticker, market="b3": raw_history(ticker)

    def tearDown(self):
        market_data.raw_history = self._raw_history
        database.DATA_DIR, database.BACKUP_DIR = self._dirs
        shutil.rmtree(self.data_dir.parent, ignore_errors=True)
