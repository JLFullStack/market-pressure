"""Banco de dados da aplicação: uma tabela Parquet por arquivo na pasta data/.

Cada tabela tem um esquema fixo (SCHEMAS). read_table devolve as linhas como dicts, na ordem
gravada (a ordem das linhas é a ordem de exibição: carteiras, classes, ativos...);
write_tables grava várias tabelas juntas: primeiro todos os temporários, depois, se pedido,
copia a versão anterior para data/backup/ e troca os arquivos (os.replace). Tabela que ainda
não existe é lida como vazia.

Precisa do pyarrow (pip install -r requirements.txt).
"""

import os
import shutil
import threading
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import config

# Atributos do módulo (e não constantes do config) para que os testes troquem a pasta do banco.
DATA_DIR: Path = config.DATA_DIR
BACKUP_DIR: Path = DATA_DIR / "backup"

_lock = threading.RLock()

_str, _f64, _i64, _bool = pa.string(), pa.float64(), pa.int64(), pa.bool_()


def _schema(*fields):
    return pa.schema([pa.field(name, kind) for name, kind in fields])


# Campos de uma análise do screener (uma linha por ativo × base): coluna, caminho no dict, tipo.
ANALYSIS_FIELDS = [
    ("date", ("date",), _i64),
    ("price", ("price",), _f64),
    ("change", ("change",), _f64),
    ("above_rising_mm200", ("aboveRisingMM200",), _bool),
    ("setup_ok", ("setup", "ok"), _bool),
    ("setup_ok_strict", ("setup", "okStrict"), _bool),
    ("setup_pullback", ("setup", "pullback"), _f64),
    ("ma_name", ("setup", "ma", "name"), _str),
    ("ma_value", ("setup", "ma", "value"), _f64),
    ("ma_dist", ("setup", "ma", "dist"), _f64),
    ("setup_pattern", ("setup", "pattern"), _str),
    *[(f"pressure_{k}", ("pressure", k), _f64) for k in ("total", "volume", "momentum", "trend", "structure")],
    *[(f"detail_{k}", ("pressure", "details", k), _bool if k == "hiddenDivergence" else _f64)
      for k in ("A", "B", "C", "obvPct", "closePct", "advanceRatio", "dryRatio", "regime", "reset",
                "stochReset", "rsiMin", "stochMin", "hiddenDivergence", "hist", "zero", "macdAtr",
                "support", "bbwPct", "range5Atr", "rsi", "stoch")],
    *[(f"bt_{s}_{col}", ("backtest", s, key), kind) for s in ("loose", "strict")
      for col, key, kind in (("wins", "wins", _i64), ("trades", "trades", _i64),
                             ("win_rate", "winRate", _f64), ("avg_return", "avgReturn", _f64))],
]

SCHEMAS = {
    # Carteiras
    "portfolios": _schema(("id", _str), ("name", _str)),
    "portfolio_classes": _schema(("portfolio_id", _str), ("name", _str), ("target", _f64)),
    "portfolio_assets": _schema(("portfolio_id", _str), ("ticker", _str), ("class", _str), ("market", _str),
                                ("target", _f64)),
    "transactions": _schema(("portfolio_id", _str), ("id", _str), ("date", _str), ("ticker", _str),
                            ("type", _str), ("quantity", _f64), ("price", _f64), ("fees", _f64), ("fx", _f64)),
    "fixed_income": _schema(("portfolio_id", _str), ("id", _str), ("name", _str), ("class", _str),
                            ("target", _f64)),
    "fixed_income_entries": _schema(("portfolio_id", _str), ("item_id", _str), ("id", _str), ("date", _str),
                                    ("type", _str), ("amount", _f64)),
    # Screener
    "universe": _schema(("ticker", _str), ("name", _str), ("sector", _str), ("close", _f64), ("volume", _f64)),
    "scan": _schema(("version", _i64), ("timestamp", _i64), ("universe_size", _i64)),
    "scan_failures": _schema(("ticker", _str), ("reason", _str)),
    "scan_analyses": _schema(("ticker", _str), ("name", _str), ("sector", _str), ("mode", _str),
                             *[(col, kind) for col, _, kind in ANALYSIS_FIELDS]),
}


def table_path(name) -> Path:
    return DATA_DIR / f"{name}.parquet"


def exists(name) -> bool:
    return table_path(name).exists()


def read_table(name) -> list:
    """Linhas da tabela como dicts (colunas do esquema), na ordem gravada; [] se não existe."""
    with _lock:
        path = table_path(name)
        if not path.exists():
            return []
        return pq.read_table(path).to_pylist()


def write_tables(tables: dict, backup=False):
    """Grava {nome: linhas} com o esquema de cada tabela. Primeiro todos os temporários, depois
    as trocas (os.replace); backup=True guarda a versão anterior de cada uma em data/backup/."""
    with _lock:
        DATA_DIR.mkdir(exist_ok=True)
        staged = []
        try:
            for name, rows in tables.items():
                schema = SCHEMAS[name]
                table = pa.Table.from_pylist([{f: r.get(f) for f in schema.names} for r in rows], schema=schema)
                tmp = table_path(name).with_suffix(".parquet.tmp")
                pq.write_table(table, tmp)
                staged.append((tmp, table_path(name)))
        except Exception:
            for tmp, _ in staged:
                tmp.unlink(missing_ok=True)
            raise
        if backup:
            BACKUP_DIR.mkdir(exist_ok=True)
            for _, path in staged:
                if path.exists():
                    shutil.copy2(path, BACKUP_DIR / path.name)
        for tmp, path in staged:
            os.replace(tmp, path)


# ---------------------------------------------------------------- Análises do screener


def flatten_analysis(analysis: dict) -> dict:
    """Análise de uma base (dict aninhado de analyze_asset) → colunas de scan_analyses."""
    row = {}
    for col, path, kind in ANALYSIS_FIELDS:
        value = analysis
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        row[col] = float(value) if kind == _f64 and isinstance(value, (int, float)) else value
    return row


def unflatten_analysis(row: dict) -> dict:
    """Inverso de flatten_analysis: ma = None sem média testada; pressure e backtest só existem
    quando o setup passou (como em analyze_asset)."""
    out = {}
    for col, path, _ in ANALYSIS_FIELDS:
        node = out
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = row.get(col)
    if row.get("ma_name") is None:
        out["setup"]["ma"] = None
    if row.get("pressure_total") is None:
        out.pop("pressure", None)
        out.pop("backtest", None)
    return out
