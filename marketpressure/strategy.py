"""Estratégia de venda escolhida na aba Configurações, por tipo de ativo.

Duas regras, as mesmas do backtest:
- "parcial": vende `fraction` de cada compra quando a máxima atinge +`target` sobre o preço
  dela; o restante sai na reversão de tendência (regra B, core.exit_signal);
- "reversao": vende tudo na reversão de tendência.

Cada grupo de ativos (GROUPS = as classes fixas da Carteira, portfolio.CLASSES, menos Renda
fixa: Ações, Fundos imobiliários, ETFs Brasil e Internacionais — BDRs, ETFs do exterior, EUA e
cripto) segue uma das duas; alvo e parte vendida são os mesmos para todos os grupos que usam a
venda parcial. Nos backtests de carteira, a venda parcial em +10% vendeu cedo demais os
internacionais, que renderam mais saindo só na reversão: por isso é o padrão deles. Na Carteira,
a regra de uma posição é a da classe do ativo; no Screener e no Backtest, a do tipo (group_of).

Tabelas: settings (uma linha: alvo, parte vendida e a regra antiga, única, que vale para os
grupos sem escolha salva) e settings_kinds (grupo, regra). A Carteira (situação das posições), o
Screener (Taxa de acerto, Score e Indicação), o "COPIAR ANÁLISE" e a aba Backtest seguem a regra
de cada grupo. No navegador as tabelas são do usuário (como as carteiras), então cada conta tem a sua.
"""

import logging

from . import database as db
from . import market_data
from .core import EXIT, fmt_num, fmt_pct_value as pct
from .portfolio import CLASS_GROUP, CLASSES, parse_number

log = logging.getLogger(__name__)

TABLE = "settings"
KINDS_TABLE = "settings_kinds"
STRATEGIES = {
    "parcial": "Venda parcial no alvo + reversão de tendência",
    "reversao": "Vender 100% na reversão de tendência",
}
GROUPS = {c["group"]: c["name"] for c in CLASSES if c["group"]}
GROUP_HINTS = {
    "acoes": "ações e units",
    "fundos": "FII, Fiagro e FI-Infra",
    "etfs": "ETFs de índices brasileiros",
    "internacionais": "BDRs, ETFs do exterior, ativos dos EUA e cripto",
}
INTERNATIONAL = ("internacionais",)
DEFAULT_KINDS = {g: "reversao" if g in INTERNATIONAL else "parcial" for g in GROUPS}
LEGACY_GROUPS = {"bdrs": "internacionais", "exterior": "internacionais"}   # grupos de antes
DEFAULT = {"target": EXIT["partialTarget"], "fraction": EXIT["partialFraction"], "kinds": dict(DEFAULT_KINDS)}
TARGET_RANGE = (0.5, 500)    # alvo da venda parcial, em %
FRACTION_RANGE = (1, 100)    # parte de cada compra vendida no alvo, em %
REVERSAL_TEXT = "fechamento abaixo da MM20, MM50 e MM200 com a MM50 abaixo da MM200"


def for_group(settings, group):
    """Regra de um grupo no formato que a Carteira e o Screener usam: {key, target, fraction}."""
    group = group if group in GROUPS else "acoes"
    return {"key": settings["kinds"].get(group, "parcial"), "target": settings["target"], "fraction": settings["fraction"],
            "group": group}


def group_of(kind=None, market="b3", name=""):
    """Grupo de um ativo pelo tipo: "internacionais" fora da B3, nos BDRs e nos ETFs do exterior
    (pelo nome, market_data.international_etf); na B3, o tipo do screener (ações sem tipo conhecido)."""
    if (market and market != "b3") or kind == "bdrs" or (kind == "etfs" and market_data.international_etf(name)):
        return "internacionais"
    return kind if kind in GROUPS else "acoes"


def asset_rules(settings, classes=None):
    """Função (ticker, market) → regra de venda de um ativo da Carteira: a do grupo da classe do
    ativo (classes: {ticker: classe}); sem classe com regra, a do tipo, pela última lista do
    screener (tabela universe) ou pelo código (market_data.asset_kind)."""
    classes = classes or {}
    universe = {}
    try:
        universe = {u["ticker"]: u for u in db.read_table("universe")}
    except Exception:
        log.warning("não foi possível ler a lista de ativos salva; tipo pelo código", exc_info=True)

    def rule(ticker, market="b3"):
        group = CLASS_GROUP.get(classes.get(ticker))
        if not group:
            u = universe.get(ticker) or {}
            kind = u.get("kind") or (market_data.asset_kind(ticker) if market == "b3" else None)
            group = group_of(kind, market, u.get("name") or "")
        return for_group(settings, group)
    return rule


def screener_kinds(settings):
    """{grupo: regra} — o que muda o histórico (backtest) e o Score do ranking."""
    return dict(settings["kinds"])


def describe_one(s):
    """Uma regra ({key, target, fraction}) com os textos da página."""
    if s["key"] == "parcial":
        rest = "; o restante na reversão de tendência" if s["fraction"] < 1 else ""
        text = f"Vende {pct(s['fraction'])}% de cada compra ao atingir +{pct(s['target'])}% sobre o preço dela{rest}."
    else:
        text = "Vende 100% da posição na reversão de tendência."
    return {**s, "label": STRATEGIES[s["key"]], "text": text, "reversal": REVERSAL_TEXT,
            "targetPct": s["target"] * 100, "fractionPct": s["fraction"] * 100}


def describe(settings):
    """As configurações com os textos da página: alvo e parte em %, e a regra de cada grupo."""
    return {"target": settings["target"], "fraction": settings["fraction"],
            "targetPct": settings["target"] * 100, "fractionPct": settings["fraction"] * 100,
            "reversal": REVERSAL_TEXT,
            "kinds": [{"group": g, "groupLabel": label, "hint": GROUP_HINTS[g], **describe_one(for_group(settings, g))}
                      for g, label in GROUPS.items()]}


def from_form(body):
    """Formulário da aba Configurações → (configurações, erro). target e fraction chegam em %;
    kinds = {grupo: "parcial" | "reversao"} (grupos ausentes ficam com o padrão)."""
    target, fraction = parse_number(body.get("target")), parse_number(body.get("fraction"))
    if target is None or not TARGET_RANGE[0] <= target <= TARGET_RANGE[1]:
        return None, f"A rentabilidade do alvo deve ficar entre {fmt_num(TARGET_RANGE[0])}% e {fmt_num(TARGET_RANGE[1], 0)}%."
    if fraction is None or not FRACTION_RANGE[0] <= fraction <= FRACTION_RANGE[1]:
        return None, f"A parte vendida no alvo deve ficar entre {FRACTION_RANGE[0]}% e {FRACTION_RANGE[1]}%."
    raw = body.get("kinds") or {}
    if not isinstance(raw, dict):
        return None, "Escolha a estratégia de cada tipo de ativo."
    kinds = dict(DEFAULT_KINDS)
    for group, key in raw.items():
        group = LEGACY_GROUPS.get(group, group)
        if group not in GROUPS or key not in STRATEGIES:
            return None, "Escolha uma das estratégias para cada tipo de ativo."
        kinds[group] = key
    return {"target": target / 100, "fraction": fraction / 100, "kinds": kinds}, None


def load():
    """Configurações salvas; sem nada salvo (ou ilegível), DEFAULT. Quem salvou antes de existir a
    regra por tipo continua com a regra antiga em todos os grupos até salvar de novo."""
    try:
        rows, kind_rows = db.read_table(TABLE), db.read_table(KINDS_TABLE)
    except Exception:
        log.warning("não foi possível ler as configurações; usando a estratégia padrão", exc_info=True)
        return {**DEFAULT, "kinds": dict(DEFAULT_KINDS)}
    if not rows:
        return {**DEFAULT, "kinds": dict(DEFAULT_KINDS)}
    r = rows[0]
    legacy = r.get("strategy") if r.get("strategy") in STRATEGIES else None
    kinds = {g: legacy for g in GROUPS} if legacy else dict(DEFAULT_KINDS)
    for k in sorted(kind_rows, key=lambda k: k.get("kind") in LEGACY_GROUPS, reverse=True):
        group = LEGACY_GROUPS.get(k.get("kind"), k.get("kind"))   # os grupos de antes primeiro: os atuais valem mais
        if group in GROUPS and k.get("strategy") in STRATEGIES:
            kinds[group] = k["strategy"]
    return {"target": r.get("partial_target") or DEFAULT["target"],
            "fraction": r.get("partial_fraction") or DEFAULT["fraction"], "kinds": kinds}


def save(s):
    db.write_tables({
        TABLE: [{"strategy": s["kinds"]["acoes"], "partial_target": s["target"], "partial_fraction": s["fraction"]}],
        KINDS_TABLE: [{"kind": g, "strategy": s["kinds"][g]} for g in GROUPS],
    })
    return s


def payload(s=None):
    """O que a aba Configurações mostra: as configurações atuais, as opções e o padrão."""
    return {"strategy": describe(s or load()), "options": [{"key": k, "label": v} for k, v in STRATEGIES.items()],
            "groups": [{"key": g, "label": v} for g, v in GROUPS.items()],
            "default": describe(DEFAULT), "ranges": {"target": TARGET_RANGE, "fraction": FRACTION_RANGE}}
