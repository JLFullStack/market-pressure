"""Armazenamento das carteiras (tabelas do banco) e as alterações feitas pela página.

O documento das carteiras ({"version", "portfolios": [...]}) é guardado em seis tabelas do
banco (database.py): portfolios, portfolio_classes, portfolio_assets, transactions,
fixed_income e fixed_income_entries, ligadas por portfolio_id (e item_id nos lançamentos de
renda fixa). A ordem das linhas é a ordem do documento. Cada ação lê o documento, valida,
altera e grava todas as tabelas de uma vez (temporários + os.replace, com a versão anterior
em data/backup/). Se a validação falha, nada é gravado e a mensagem volta para a página
(ActionError). As regras são as mesmas dos formulários.
"""

import threading
import uuid

from . import database as db
from . import portfolio as pf
from . import portfolio_service as svc
from .core import fmt_brl, fmt_qty, today_iso

DOC_VERSION = 2
PORTFOLIO_TABLES = ("portfolios", "portfolio_classes", "portfolio_assets", "transactions",
                    "fixed_income", "fixed_income_entries")

_lock = threading.RLock()


class ActionError(Exception):
    """Erro de validação de uma ação; extra vai junto na resposta (ex.: erros por linha)."""

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


def _num(value):
    """Números do Parquet voltam como float; inteiros voltam a ser int (como eram no documento)."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _optional(item, key, value):
    if value is not None:
        item[key] = _num(value)
    return item


def read_raw() -> dict:
    """Monta o documento a partir das tabelas (vazio se ainda não há carteiras)."""
    with _lock:
        rows = db.read_tables(PORTFOLIO_TABLES)
    by_portfolio = {name: {} for name in PORTFOLIO_TABLES[1:]}
    for name, grouped in by_portfolio.items():
        for r in rows[name]:
            grouped.setdefault(r["portfolio_id"], []).append(r)
    entries = {}
    for r in rows["fixed_income_entries"]:
        entries.setdefault((r["portfolio_id"], r["item_id"]), []).append(
            {"id": r["id"], "date": r["date"], "type": r["type"], "amount": _num(r["amount"])})
    portfolios = []
    for p in rows["portfolios"]:
        pid = p["id"]
        assets = {a["ticker"]: _optional(_optional({"class": a["class"]}, "market", a["market"]), "target", a["target"])
                  for a in by_portfolio["portfolio_assets"].get(pid, [])}
        transactions = []
        for t in by_portfolio["transactions"].get(pid, []):
            tx = {k: _num(t[k]) for k in ("id", "date", "ticker", "type", "quantity", "price")}
            transactions.append(_optional(_optional(tx, "fees", t["fees"]), "fx", t["fx"]))
        fixed_income = [{**_optional({"id": f["id"], "name": f["name"], "class": f["class"]}, "target", f["target"]),
                         "entries": entries.get((pid, f["id"]), [])}
                        for f in by_portfolio["fixed_income"].get(pid, [])]
        portfolios.append({"id": pid, "name": p["name"], "assets": assets, "transactions": transactions,
                           "fixedIncome": fixed_income,
                           "classes": [{"name": c["name"], "target": _num(c["target"])}
                                       for c in by_portfolio["portfolio_classes"].get(pid, [])]})
    return {"version": DOC_VERSION, "portfolios": portfolios}


def read_doc() -> dict:
    """Documento já migrado para o formato atual (classes configuráveis, version 2)."""
    return pf.normalize_doc(read_raw())


def doc_tables(document: dict) -> dict:
    """Documento (já normalizado) → linhas de cada tabela."""
    tables = {name: [] for name in PORTFOLIO_TABLES}
    for p in document["portfolios"]:
        pid = p.get("id")
        tables["portfolios"].append({"id": pid, "name": p.get("name")})
        tables["portfolio_classes"] += [{"portfolio_id": pid, **c} for c in p["classes"]]
        tables["portfolio_assets"] += [{"portfolio_id": pid, "ticker": ticker, **a} for ticker, a in p["assets"].items()]
        tables["transactions"] += [{"portfolio_id": pid, **t} for t in p["transactions"]]
        for f in p["fixedIncome"]:
            tables["fixed_income"].append({"portfolio_id": pid, **f})
            tables["fixed_income_entries"] += [{"portfolio_id": pid, "item_id": f["id"], **e} for e in f["entries"]]
    return tables


def write_doc(document: dict) -> dict:
    """Grava todas as tabelas das carteiras juntas e guarda a versão anterior em data/backup/."""
    with _lock:
        db.write_tables(doc_tables(pf.normalize_doc(document)), backup=True)
    return document


def valid_document(document) -> bool:
    return (isinstance(document, dict) and isinstance(document.get("version"), int)
            and isinstance(document.get("portfolios"), list)
            and all(isinstance(p, dict) for p in document["portfolios"]))


def get_portfolio(portfolio_id):
    p = next((p for p in read_doc()["portfolios"] if p.get("id") == portfolio_id), None)
    if not p:
        raise ActionError("Carteira não encontrada.")
    return p


def new_id():
    return str(uuid.uuid4())


# ---------------------------------------------------------------- Ações


def _find(doc, portfolio_id):
    cur = next((p for p in doc["portfolios"] if p.get("id") == portfolio_id), None)
    if not cur:
        raise ActionError("Carteira não encontrada.")
    return cur


def _add_transaction(cur, tx, cls, market):
    """Ativo novo entra com a classe e o mercado dados."""
    cur["assets"].setdefault(tx["ticker"], {"class": cls, "market": market})
    cur["transactions"].append(tx)


def _add_fixed_income_entry(cur, item_id, entry):
    fi = next((f for f in cur["fixedIncome"] if f["id"] == item_id), None)
    if not fi:
        raise ActionError("Título de renda fixa não encontrado.")
    fi["entries"].append(entry)


def _percent(value, message):
    v = pf.parse_number(value)
    if v is None or not 0 <= v <= 100:
        raise ActionError(message)
    return v


def create_portfolio(doc, body):
    name = str(body.get("name") or "").strip()
    if not name:
        raise ActionError("Informe o nome da carteira.")
    p = {"id": new_id(), "name": name, "classes": [dict(c) for c in pf.DEFAULT_CLASSES], "assets": {},
         "transactions": [], "fixedIncome": []}
    doc["portfolios"].append(p)
    return {"id": p["id"], "message": f'Carteira "{name}" criada.'}


def rename_portfolio(doc, body):
    name = str(body.get("name") or "").strip()
    if not name:
        raise ActionError("Informe o nome da carteira.")
    _find(doc, body.get("id"))["name"] = name
    return {}


def delete_portfolio(doc, body):
    _find(doc, body.get("id"))
    doc["portfolios"] = [p for p in doc["portfolios"] if p["id"] != body.get("id")]
    return {"id": doc["portfolios"][0]["id"] if doc["portfolios"] else None}


def add_transaction(doc, body):
    """Formulário "Nova operação". Números chegam como digitados; preço e taxas na moeda do ativo."""
    cur = _find(doc, body.get("id"))
    info = svc.asset_info(cur, body.get("ticker"), body.get("market"))
    market = info["market"]
    tx = {
        "id": new_id(), "date": body.get("date"), "ticker": info["ticker"],
        "type": "venda" if body.get("type") == "venda" else "compra",
        "quantity": pf.parse_number(body.get("quantity")), "price": pf.parse_number(body.get("price")),
        "fees": pf.parse_number(body.get("fees"), 0), "fx": 1 if market == "b3" else pf.parse_number(body.get("fx")),
    }
    error = pf.validate_transaction(cur, tx)
    if error:
        raise ActionError(error)
    try:
        svc.quote_history(tx["ticker"], market)
    except Exception as err:
        raise ActionError(f"O ativo {tx['ticker']} ({pf.MARKETS[market]['label']}) não tem histórico no Yahoo Finance "
                          f"({err}). Confira o código e o mercado.") from None
    classes = [c["name"] for c in cur["classes"]]
    cls = (cur["assets"].get(tx["ticker"]) or {}).get("class") \
        or (body.get("cls") if body.get("cls") in classes else None) or info["suggestedClass"]
    _add_transaction(cur, tx, cls, market)
    return {"message": f"{'Compra' if tx['type'] == 'compra' else 'Venda'} de {fmt_qty(tx['quantity'])} {tx['ticker']} registrada."}


def add_fixed_income_item(doc, body):
    cur = _find(doc, body.get("id"))
    name = str(body.get("name") or "").strip()
    if not name:
        raise ActionError("Informe o nome do título.")
    classes = [c["name"] for c in cur["classes"]]
    cls = body.get("cls") if body.get("cls") in classes else pf.fixed_income_class(cur["classes"])
    item = {"id": new_id(), "name": name, "class": cls, "entries": []}
    cur["fixedIncome"].append(item)
    return {"itemId": item["id"], "message": f'Título "{name}" criado em {cls}. Registre o primeiro aporte.'}


def add_fixed_income_entry(doc, body):
    cur = _find(doc, body.get("id"))
    if not body.get("itemId"):
        raise ActionError("Crie um título de renda fixa primeiro.")
    kind = body.get("type") if body.get("type") in pf.FI_LABELS else "aporte"
    entry = {"id": new_id(), "date": body.get("date"), "type": kind, "amount": pf.parse_number(body.get("amount"))}
    error = pf.validate_fixed_income_entry(entry)
    if error:
        raise ActionError(error)
    _add_fixed_income_entry(cur, body["itemId"], entry)
    return {"message": f"{pf.FI_LABELS[kind]} de {fmt_brl(entry['amount'])} registrado."}


def delete_entry(doc, body):
    cur = _find(doc, body.get("id"))
    entry_id, item_id = body.get("entryId"), body.get("itemId")
    if body.get("kind") == "tx":
        tx = next((t for t in cur["transactions"] if t["id"] == entry_id), None)
        rest = [t for t in cur["transactions"] if t["id"] != entry_id]
        error = pf.check_sequence(rest)
        if error:
            raise ActionError(f"Não é possível excluir: sem esta compra, {error[0].lower()}{error[1:]}")
        cur["transactions"] = rest
        # Último lançamento do ativo: ele sai das posições e da lista de classes.
        if tx and not any(t["ticker"] == tx["ticker"] for t in rest):
            cur["assets"].pop(tx["ticker"], None)
    else:
        fi = next((f for f in cur["fixedIncome"] if f["id"] == item_id), None)
        if not fi:
            raise ActionError("Título de renda fixa não encontrado.")
        fi["entries"] = [e for e in fi["entries"] if e["id"] != entry_id]
        if not fi["entries"]:
            cur["fixedIncome"] = [f for f in cur["fixedIncome"] if f["id"] != item_id]
    return {}


def set_class_target(doc, body):
    cur = _find(doc, body.get("id"))
    cls = next((c for c in cur["classes"] if c["name"] == body.get("cls")), None)
    if not cls:
        raise ActionError("Classe não encontrada.")
    cls["target"] = _percent(body.get("target"), "A meta deve estar entre 0 e 100%.")
    return {}


def rename_class(doc, body):
    cur = _find(doc, body.get("id"))
    previous, name = body.get("previous"), str(body.get("name") or "").strip()
    cls = next((c for c in cur["classes"] if c["name"] == previous), None)
    if not cls:
        raise ActionError("Classe não encontrada.")
    if name == previous:
        return {}
    error = pf.validate_class_name(cur["classes"], name, previous)
    if error:
        raise ActionError(error)
    cls["name"] = name
    for a in cur["assets"].values():
        if a.get("class") == previous:
            a["class"] = name
    for f in cur["fixedIncome"]:
        if f.get("class") == previous:
            f["class"] = name
    return {"message": f'Classe "{previous}" renomeada para "{name}".'}


def add_class(doc, body):
    cur = _find(doc, body.get("id"))
    name = str(body.get("name") or "").strip()
    error = pf.validate_class_name(cur["classes"], name)
    if error:
        raise ActionError(error)
    target = _percent(body.get("target") if str(body.get("target") or "").strip() else 0,
                      "A meta deve estar entre 0 e 100%.")
    cur["classes"].append({"name": name, "target": target})
    return {"message": f'Classe "{name}" criada.'}


def remove_class(doc, body):
    cur = _find(doc, body.get("id"))
    name = body.get("name")
    members = pf.class_members(cur, name)
    if members:
        raise ActionError(f'A classe "{name}" tem ativos ({", ".join(members)}). Mova-os para outra classe antes de removê-la.')
    if len(cur["classes"]) <= 1:
        raise ActionError("A carteira precisa de ao menos uma classe.")
    cur["classes"] = [c for c in cur["classes"] if c["name"] != name]
    return {"message": f'Classe "{name}" removida.'}


def move_asset(doc, body):
    """Move um ativo (ou título de renda fixa) para outra classe. A meta fixada é mantida."""
    cur = _find(doc, body.get("id"))
    key, cls = body.get("key"), body.get("cls")
    if not key or not any(c["name"] == cls for c in cur["classes"]):
        raise ActionError("Escolha o ativo e a classe de destino.")
    if key.startswith("fi:"):
        target = next((f for f in cur["fixedIncome"] if f["id"] == key[3:]), None)
    else:
        target = cur["assets"].get(key)
    if target is None:
        raise ActionError("Ativo não encontrado.")
    target["class"] = cls
    return {"message": f'Ativo movido para "{cls}".'}


def set_asset_target(doc, body):
    """Meta fixada de um ativo ou título (% do patrimônio); vazio volta à meta automática."""
    cur = _find(doc, body.get("id"))
    key = body.get("key") or ""
    raw = str(body.get("target") if body.get("target") is not None else "").strip()
    value = None if raw == "" else _percent(raw, "A meta deve estar entre 0 e 100%.")
    target = next((f for f in cur["fixedIncome"] if f["id"] == key[3:]), None) if key.startswith("fi:") \
        else cur["assets"].get(key)
    if target is None:
        raise ActionError("Ativo não encontrado.")
    if value is None:
        target.pop("target", None)
    else:
        target["target"] = value
    return {}


def register_batch(doc, body):
    """Lista "Registrar compras sugeridas": valida cada linha incluída com as regras dos formulários
    avulsos; se alguma falha, nada é gravado e os erros voltam por linha. Senão, todas entram de
    uma vez (uma única gravação: ou entram todos os lançamentos, ou nenhum)."""
    cur = _find(doc, body.get("id"))
    rows = [pf.parse_batch_row(r) for r in body.get("rows") or []]
    included = [(k, r) for k, r in enumerate(rows) if r.get("include")]
    if not included:
        raise ActionError("Marque ao menos um lançamento.")
    today = today_iso()
    errors = {}
    for k, r in included:
        error = pf.batch_row_error(cur, r, today)
        if error:
            errors[k] = error
    if errors:
        raise ActionError(f"{len(errors)} linha(s) com erro. Corrija-as ou desmarque-as; nada foi gravado.",
                          rowErrors=errors)
    buys = [r for _, r in included if r["kind"] == "quote"]
    deposits = [r for _, r in included if r["kind"] == "fixed"]
    for r in buys:
        known = cur["assets"].get(r["key"]) or {}
        market = known.get("market") or r.get("market") or "b3"
        _add_transaction(cur, pf.batch_tx({**r, "market": market}, new_id()),
                         known.get("class") or r.get("cls"), market)
    for r in deposits:
        _add_fixed_income_entry(cur, r.get("id"), {"id": new_id(), "date": r["date"], "type": "aporte",
                                                  "amount": pf.js_round(r["amount"] * 100) / 100})
    parts = []
    if buys:
        parts.append(f"{len(buys)} {'compra' if len(buys) == 1 else 'compras'}")
    if deposits:
        parts.append(f"{len(deposits)} {'aporte' if len(deposits) == 1 else 'aportes'}")
    if not deposits:
        done = "registrada" if len(buys) == 1 else "registradas"
    else:
        done = "registrado" if len(included) == 1 else "registrados"
    return {"message": f"{' e '.join(parts)} {done}."}


ACTIONS = {
    "criar_carteira": create_portfolio,
    "renomear_carteira": rename_portfolio,
    "excluir_carteira": delete_portfolio,
    "nova_operacao": add_transaction,
    "novo_titulo": add_fixed_income_item,
    "lancamento_titulo": add_fixed_income_entry,
    "excluir_lancamento": delete_entry,
    "meta_classe": set_class_target,
    "renomear_classe": rename_class,
    "nova_classe": add_class,
    "remover_classe": remove_class,
    "mover_ativo": move_asset,
    "meta_ativo": set_asset_target,
    "registrar_lote": register_batch,
}


def apply_action(body: dict) -> dict:
    """Aplica uma ação ({"action": nome, ...}) e grava. Devolve o documento e o resultado da ação."""
    handler = ACTIONS.get(body.get("action"))
    if not handler:
        raise ActionError("Ação desconhecida.")
    with _lock:
        doc = read_doc()
        result = handler(doc, body)
        saved = pf.normalize_doc(write_doc(doc))
    return {"doc": saved, **result}
