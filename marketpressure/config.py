"""Configuração do MarketPressure: caminhos, endereço do servidor, chaves e tempos de cache.

Os valores podem ser trocados por variáveis de ambiente ou por um arquivo .env na raiz do
projeto (linhas NOME=valor; o .env não vai para o controle de versão, veja .env.example).
Variáveis já definidas no ambiente têm prioridade sobre o .env.
"""

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path):
    """Lê NOME=valor do .env para os.environ (sem sobrescrever o que já existe)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


_load_env_file(ROOT_DIR / ".env")

# Pastas: páginas e scripts servidos ao navegador, e o banco (nunca servido).
WEB_DIR = ROOT_DIR / "web"
DATA_DIR = Path(os.environ.get("MARKETPRESSURE_DATA_DIR") or ROOT_DIR / "data")

# Servidor. Só escuta na máquina local; a página pode vir de outro servidor local (ex.: Live
# Server na porta 5500), mas nunca de um site externo (ver server.origin_allowed).
HOST = os.environ.get("MARKETPRESSURE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MARKETPRESSURE_PORT", "8765"))
LOG_LEVEL = os.environ.get("MARKETPRESSURE_LOG_LEVEL", "INFO")

# brapi (lista de ações). Sem token, a lista vem da última cópia salva no banco (tabela universe).
BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "")

# Cache em memória dos dados externos (segundos).
CACHE_TTL_SECONDS = 15 * 60          # cotações e lista de ações
CDI_TTL_SECONDS = 12 * 60 * 60
PROVENTOS_TTL_SECONDS = 6 * 60 * 60
HTTP_TIMEOUT_SECONDS = 20
