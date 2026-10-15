"""Estratégia de venda escolhida na aba Configurações (tabela settings, uma linha).

Duas estratégias, as mesmas do backtest:
- "parcial": vende `fraction` de cada compra quando a máxima atinge +`target` sobre o preço
  dela; o restante sai na reversão de tendência (regra B, core.exit_signal);
- "reversao": vende tudo na reversão de tendência.

A Carteira (situação das posições e o aporte) e a coluna "Indicação" do Screener seguem a
estratégia salva. Sem nada salvo vale DEFAULT (a de core.EXIT). No navegador a tabela é do
usuário (como as carteiras), então cada conta tem a sua.
"""

import logging

from . import database as db
from .core import EXIT, fmt_num, fmt_pct_value as pct
from .portfolio import parse_number

log = logging.getLogger(__name__)

TABLE = "settings"
STRATEGIES = {
    "parcial": "Venda parcial no alvo + reversão de tendência",
    "reversao": "Vender 100% na reversão de tendência",
}
DEFAULT = {"key": "parcial", "target": EXIT["partialTarget"], "fraction": EXIT["partialFraction"]}
TARGET_RANGE = (0.5, 500)    # alvo da venda parcial, em %
FRACTION_RANGE = (1, 100)    # parte de cada compra vendida no alvo, em %
REVERSAL_TEXT = "fechamento abaixo da MM20, MM50 e MM200 com a MM50 abaixo da MM200"


def describe(s):
    """Estratégia com os textos que a página mostra (target e fraction também em %)."""
    if s["key"] == "parcial":
        rest = "; o restante na reversão de tendência" if s["fraction"] < 1 else ""
        text = f"Vende {pct(s['fraction'])}% de cada compra ao atingir +{pct(s['target'])}% sobre o preço dela{rest}."
    else:
        text = "Vende 100% da posição na reversão de tendência."
    return {**s, "label": STRATEGIES[s["key"]], "text": text, "reversal": REVERSAL_TEXT,
            "targetPct": s["target"] * 100, "fractionPct": s["fraction"] * 100}


def from_form(body):
    """Formulário da aba Configurações → (estratégia, erro). target e fraction chegam em %."""
    key = body.get("strategy")
    if key not in STRATEGIES:
        return None, "Escolha uma das estratégias."
    target, fraction = parse_number(body.get("target")), parse_number(body.get("fraction"))
    if target is None or not TARGET_RANGE[0] <= target <= TARGET_RANGE[1]:
        return None, f"A rentabilidade do alvo deve ficar entre {fmt_num(TARGET_RANGE[0])}% e {fmt_num(TARGET_RANGE[1], 0)}%."
    if fraction is None or not FRACTION_RANGE[0] <= fraction <= FRACTION_RANGE[1]:
        return None, f"A parte vendida no alvo deve ficar entre {FRACTION_RANGE[0]}% e {FRACTION_RANGE[1]}%."
    return {"key": key, "target": target / 100, "fraction": fraction / 100}, None


def load():
    """Estratégia salva; sem nada salvo (ou ilegível), DEFAULT."""
    try:
        rows = db.read_table(TABLE)
    except Exception:
        log.warning("não foi possível ler as configurações; usando a estratégia padrão", exc_info=True)
        return dict(DEFAULT)
    if not rows or rows[0].get("strategy") not in STRATEGIES:
        return dict(DEFAULT)
    r = rows[0]
    return {"key": r["strategy"], "target": r.get("partial_target") or DEFAULT["target"],
            "fraction": r.get("partial_fraction") or DEFAULT["fraction"]}


def save(s):
    db.write_tables({TABLE: [{"strategy": s["key"], "partial_target": s["target"], "partial_fraction": s["fraction"]}]})
    return s


def payload(s=None):
    """O que a aba Configurações mostra: a estratégia atual, as opções e o padrão."""
    return {"strategy": describe(s or load()), "options": [{"key": k, "label": v} for k, v in STRATEGIES.items()],
            "default": describe(DEFAULT), "ranges": {"target": TARGET_RANGE, "fraction": FRACTION_RANGE}}
