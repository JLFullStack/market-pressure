"""Calendário de feriados, editado na aba Configurações. Hoje só antecipa o vencimento do DARF
(tax_report.due_date), então é um calendário bancário, não o de pregões da B3.

Tabela holidays (por usuário na versão publicada): date "MM-DD" repete todo ano; "AAAA-MM-DD" vale
só naquele dia. Enquanto a tabela não existe, valem os feriados de data fixa (FIXED); depois de
salva, vale o que estiver nela, mesmo vazia. Os móveis (Carnaval, Sexta-feira Santa e Corpus
Christi) mudam a cada ano e são incluídos por um botão, ano a ano.
"""

import logging
from datetime import date, timedelta

from . import database as db
from .core import fmt_date, today_iso

log = logging.getLogger(__name__)

TABLE = "holidays"
# Tipo de cada feriado: só organiza a lista; todos antecipam o vencimento do DARF. "bancario" = não
# é feriado nacional (ponto facultativo na portaria federal do ano), mas os bancos não abrem ao público.
KINDS = {"nacional": "Nacional", "estadual": "Estadual", "municipal": "Municipal", "bancario": "Só bancário"}
DEFAULT_KIND = "municipal"   # o que o usuário mais inclui à mão
FIXED = [("01-01", "Confraternização Universal", "nacional"), ("04-21", "Tiradentes", "nacional"),
         ("05-01", "Dia do Trabalho", "nacional"), ("09-07", "Independência", "nacional"),
         ("10-12", "Nossa Senhora Aparecida", "nacional"), ("11-02", "Finados", "nacional"),
         ("11-15", "Proclamação da República", "nacional"), ("11-20", "Consciência Negra", "nacional"),
         ("12-25", "Natal", "nacional"), ("12-31", "Último dia do ano, sem expediente bancário ao público", "bancario")]
MOVING = [(-48, "Carnaval (segunda)", "bancario"), (-47, "Carnaval (terça)", "bancario"),
          (-2, "Sexta-feira Santa", "nacional"), (60, "Corpus Christi", "bancario")]
YEAR_RANGE = (2000, 2100)
MOVING_YEARS = 2     # anos oferecidos no botão dos feriados móveis, a partir do atual
NAME_MAX = 60


def is_holiday(iso, keys):
    """O dia "AAAA-MM-DD" está em keys (datas "MM-DD" e "AAAA-MM-DD")?"""
    return iso in keys or iso[5:] in keys


def easter(year):
    """Domingo de Páscoa (algoritmo gregoriano anônimo)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def moving(year):
    """Feriados móveis do ano: [{date, name, kind}], por data."""
    e = easter(year)
    return [{"date": (e + timedelta(days=n)).isoformat(), "name": name, "kind": kind} for n, name, kind in MOVING]


def _fixed_rows():
    return [{"date": d, "name": n, "kind": k} for d, n, k in FIXED]


def _sorted(rows):
    # Primeiro os anuais (por mês e dia), depois os de um ano só (por data).
    return sorted(rows, key=lambda r: (len(r["date"]), r["date"]))


def load():
    """Os feriados em vigor: [{date, name, kind}]."""
    try:
        if not db.exists(TABLE):
            return _fixed_rows()
        return _sorted({"date": r["date"], "name": r["name"] or "Feriado",
                        "kind": r.get("kind") if r.get("kind") in KINDS else DEFAULT_KIND} for r in db.read_table(TABLE))
    except Exception:
        log.warning("não foi possível ler os feriados; usando os de data fixa", exc_info=True)
        return _fixed_rows()


def keys():
    """As datas em vigor, para is_holiday."""
    return {r["date"] for r in load()}


def _save(rows):
    db.write_tables({TABLE: _sorted(rows)})


def _parse_key(body):
    """{date: "AAAA-MM-DD", annual} → a chave a salvar ("MM-DD" ou a data inteira)."""
    text = body.get("date")
    try:
        date.fromisoformat(text)
        if len(text) != 10:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Informe a data do feriado.") from None
    return text[5:] if body.get("annual") else text


def _label(key):
    return fmt_date(key) if len(key) == 10 else f"{key[3:]}/{key[:2]}"


def change(body):
    """{op: add, date, name, annual, kind} | {op: edit, key, date, name, annual, kind} | {op: remove, key} |
    {op: moving, year} | {op: restore} → mensagem. ValueError com a mensagem quando é inválido."""
    op = body.get("op")
    rows = load()
    if op in ("add", "edit"):
        key = _parse_key(body)
        name = str(body.get("name") or "").strip()[:NAME_MAX] or "Feriado"
        kind = body.get("kind") or DEFAULT_KIND
        if kind not in KINDS:
            raise ValueError("Tipo de feriado inválido.")
        previous = body.get("key") if op == "edit" else None
        if op == "edit" and not any(r["date"] == previous for r in rows):
            raise ValueError("Este feriado não está mais na lista.")
        if key != previous and any(r["date"] == key for r in rows):
            raise ValueError(f"{_label(key)} já está na lista.")
        rows = [r for r in rows if r["date"] != previous] + [{"date": key, "name": name, "kind": kind}]
        message = f"Feriado de {_label(key)} {'alterado' if op == 'edit' else 'adicionado'}."
    elif op == "remove":
        rows = [r for r in rows if r["date"] != body.get("key")]
        message = "Feriado removido."
    elif op == "moving":
        year = str(body.get("year") or "")
        if not (year.isdigit() and YEAR_RANGE[0] <= int(year) <= YEAR_RANGE[1]):
            raise ValueError("Ano inválido.")
        known = {r["date"] for r in rows}
        new = [r for r in moving(int(year)) if r["date"] not in known]
        rows += new
        message = f"{len(new)} feriados móveis de {year} adicionados." if new else f"Os feriados móveis de {year} já estavam na lista."
    elif op == "restore":
        known = {r["date"] for r in rows}
        new = [r for r in _fixed_rows() if r["date"] not in known]
        rows += new
        message = f"{len(new)} feriados de data fixa restaurados." if new else "Os feriados de data fixa já estavam na lista."
    else:
        raise ValueError("Operação inválida.")
    _save(rows)
    return message


def _edit_date(key, year):
    """A data que o campo mostra ao editar: um feriado anual aparece no ano atual."""
    if len(key) == 10:
        return key
    try:
        return date(year, int(key[:2]), int(key[3:])).isoformat()
    except ValueError:   # 29/02 fora de ano bissexto
        return f"2028-{key}"


def payload():
    """O que a aba Configurações mostra."""
    year = int(today_iso()[:4])
    rows = [{"key": r["date"], "name": r["name"], "label": _label(r["date"]), "editDate": _edit_date(r["date"], year),
             "annual": len(r["date"]) == 5, "kind": r["kind"], "kindLabel": KINDS[r["kind"]]} for r in load()]
    return {"annual": [r for r in rows if r["annual"]], "dated": [r for r in rows if not r["annual"]],
            "years": [year + k for k in range(MOVING_YEARS)],
            "kinds": [{"key": k, "label": v} for k, v in KINDS.items()], "defaultKind": DEFAULT_KIND}
