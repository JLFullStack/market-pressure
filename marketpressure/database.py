"""Banco de dados da aplicação: tabelas com esquema fixo (SCHEMAS), em dois lugares.

- Servidor local: uma tabela Parquet por arquivo na pasta data/ (precisa do pyarrow,
  pip install -r requirements.txt). write_tables grava primeiro todos os temporários, depois,
  se pedido, copia a versão anterior para data/backup/ e troca os arquivos (os.replace).
- Navegador (versão publicada, runtime.BROWSER): cada tabela é um JSON numa linha do D1 do
  Cloudflare, lida e gravada pela rota /db do Worker (worker/index.js); as tabelas de uma
  gravação vão juntas numa transação e backup=True guarda a versão anterior em outra tabela.

Além das tabelas de SCHEMAS, cada ativo tem seu histórico diário salvo (HISTORY_SCHEMA, tabelas
history_{mercado}_{ticker}, em data/history/; ver market_data.load_history).

read_table devolve as linhas como dicts, na ordem gravada (a ordem das linhas é a ordem de
exibição: carteiras, classes, ativos...). Tabela que ainda não existe é lida como vazia.
Valores que não cabem no tipo da coluna levantam TypeError/ValueError e nada é gravado.
"""

import json
import os
import shutil
import threading
import urllib.parse
from pathlib import Path

from . import config, runtime

# Atributos do módulo (e não constantes do config) para que os testes troquem a pasta do banco.
DATA_DIR: Path = config.DATA_DIR
BACKUP_DIR: Path = DATA_DIR / "backup"

_lock = threading.RLock()

_str, _f64, _i64, _bool = "string", "float64", "int64", "bool"


def _schema(*fields):
    """Colunas da tabela, em ordem: {nome: tipo}."""
    return dict(fields)


# Campos de uma análise do screener (uma linha por ativo × base): coluna, caminho no dict, tipo.
ANALYSIS_FIELDS = [
    ("date", ("date",), _i64),
    ("price", ("price",), _f64),
    ("change", ("change",), _f64),
    ("above_rising_mm200", ("aboveRisingMM200",), _bool),
    ("mm200_slope", ("mm200Slope",), _f64),
    ("return_12m", ("return12m",), _f64),
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
    # backtest do ativo com a venda parcial (loose/strict) e só com a reversão (looseRev/strictRev)
    *[(f"bt_{col_s}_{col}", ("backtest", s, key), kind)
      for s, col_s in (("loose", "loose"), ("strict", "strict"), ("looseRev", "loose_rev"), ("strictRev", "strict_rev"))
      for col, key, kind in (("wins", "wins", _i64), ("trades", "trades", _i64),
                             ("win_rate", "winRate", _f64), ("avg_return", "avgReturn", _f64),
                             ("open", "open", _i64), ("open_return", "openReturn", _f64),
                             ("recent_trades", "recentTrades", _i64), ("recent_wins", "recentWins", _i64),
                             ("recent_return", "recentReturn", _f64))],
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
    "portfolio_goals": _schema(("portfolio_id", _str), ("id", _str), ("name", _str), ("kind", _str), ("target", _f64),
                               ("withdrawal", _f64), ("initial", _f64), ("use_portfolio", _bool), ("monthly", _f64),
                               ("rate", _f64), ("inflation", _f64), ("adjust", _bool), ("target_date", _str),
                               ("created", _str)),
    # Configurações (strategy.py): estratégia de venda, uma linha; alvo e parte vendida em fração
    "settings": _schema(("strategy", _str), ("partial_target", _f64), ("partial_fraction", _f64)),
    # regra de venda de cada tipo de ativo (strategy.GROUPS): "parcial" ou "reversao"
    "settings_kinds": _schema(("kind", _str), ("strategy", _str)),
    # Screener
    "universe": _schema(("ticker", _str), ("name", _str), ("sector", _str), ("kind", _str), ("close", _f64),
                        ("volume", _f64), ("avg_volume", _f64)),
    "scan": _schema(("version", _i64), ("timestamp", _i64), ("universe_size", _i64)),
    "scan_failures": _schema(("ticker", _str), ("reason", _str)),
    "scan_analyses": _schema(("ticker", _str), ("name", _str), ("sector", _str), ("kind", _str),
                             ("duplicate_of", _str), ("traded", _f64), ("mode", _str),
                             *[(col, kind) for col, _, kind in ANALYSIS_FIELDS]),
}

# Histórico diário salvo de cada ativo (market_data.load_history): uma tabela por ticker e
# mercado, "history_{mercado}_{ticker}" (ex.: history_b3_petr4), em data/history/. Linhas de
# candle têm dividend nulo; as de provento têm só date e dividend (valor por cota, data-ex).
HISTORY_PREFIX = "history_"
HISTORY_SCHEMA = _schema(("date", _i64), ("open", _f64), ("high", _f64), ("low", _f64), ("close", _f64),
                         ("volume", _i64), ("adj", _f64), ("dividend", _f64))


def schema_of(name) -> dict:
    return HISTORY_SCHEMA if name.startswith(HISTORY_PREFIX) else SCHEMAS[name]


def table_path(name) -> Path:
    if name.startswith(HISTORY_PREFIX):
        return DATA_DIR / "history" / f"{name}.parquet"
    return DATA_DIR / f"{name}.parquet"


def exists(name) -> bool:
    if runtime.REMOTE:
        return _remote_read([name])[name] is not None
    return table_path(name).exists()


def read_table(name) -> list:
    """Linhas da tabela como dicts (colunas do esquema), na ordem gravada; [] se não existe."""
    return read_tables([name])[name]


def read_tables(names) -> dict:
    """{nome: linhas} de várias tabelas (no navegador, numa só requisição)."""
    with _lock:
        if runtime.REMOTE:
            return {name: rows or [] for name, rows in _remote_read(names).items()}
        return {name: _parquet_read(name) for name in names}


def write_tables(tables: dict, backup=False):
    """Grava {nome: linhas} com o esquema de cada tabela, todas juntas; backup=True guarda a
    versão anterior de cada uma (data/backup/ ou a tabela de backup do D1)."""
    with _lock:
        if runtime.REMOTE:
            _remote_write(tables, backup)
        else:
            _parquet_write(tables, backup)


# ---------------------------------------------------------------- Parquet (servidor local)


def _arrow_schema(name):
    import pyarrow as pa

    kinds = {_str: pa.string(), _f64: pa.float64(), _i64: pa.int64(), _bool: pa.bool_()}
    return pa.schema([pa.field(field, kinds[kind]) for field, kind in schema_of(name).items()])


def _parquet_read(name):
    import pyarrow.parquet as pq

    path = table_path(name)
    if not path.exists():
        return []
    return pq.read_table(path).to_pylist()


def _parquet_write(tables, backup):
    """Primeiro todos os temporários, depois as trocas (os.replace)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    staged = []
    try:
        for name, rows in tables.items():
            schema = _arrow_schema(name)
            table = pa.Table.from_pylist([{f: r.get(f) for f in schema.names} for r in rows], schema=schema)
            table_path(name).parent.mkdir(parents=True, exist_ok=True)
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


# ---------------------------------------------------------------- D1 (navegador)


def _coerce(value, kind, where):
    """Valor no tipo da coluna (como o pyarrow: número inteiro vale em float64, nada além)."""
    if value is None:
        return None
    if kind == _str and isinstance(value, str):
        return value
    if kind == _bool and isinstance(value, bool):
        return value
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        if kind == _f64:
            return float(value)
        if kind == _i64 and float(value).is_integer():
            return int(value)
    raise TypeError(f"{where}: {value!r} não é {kind}")


def _typed_rows(name, rows):
    schema = schema_of(name)
    return [{f: _coerce(r.get(f), kind, f"{name}.{f}") for f, kind in schema.items()} for r in rows]


def _remote_error(status, text):
    try:
        message = json.loads(text).get("message")
    except (ValueError, AttributeError):
        message = None
    return OSError(f"banco de dados indisponível (HTTP {status}): {message or text[:200]}")


def _remote_read(names):
    """GET /db?names=a,b → {nome: linhas | None (tabela ainda não gravada)}."""
    status, text = runtime.http_request("GET", "/db?" + urllib.parse.urlencode({"names": ",".join(names)}))
    if status != 200:
        raise _remote_error(status, text)
    data = json.loads(text)
    return {name: data.get(name) for name in names}


def _remote_write(tables, backup):
    """PUT /db com cada tabela como texto JSON (o Worker guarda o texto como veio)."""
    payload = {name: json.dumps(_typed_rows(name, rows), ensure_ascii=False, separators=(",", ":"))
               for name, rows in tables.items()}
    body = json.dumps({"backup": bool(backup), "tables": payload}, ensure_ascii=False)
    status, text = runtime.http_request("PUT", "/db", body)
    if status != 200:
        raise _remote_error(status, text)


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
