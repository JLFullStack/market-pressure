"""Sub-aba Impostos da Carteira: apuração mensal do imposto de renda sobre as vendas, dados para
emitir o DARF, controle do que já foi pago e o resumo para a declaração anual. Também o alerta de
vencimento do DARF no topo da Visão geral.

Regras (pessoa física, operações comuns):
- Ações: vendas de até R$ 20 mil no mês deixam o lucro das ações isento; acima disso, 15%.
- ETFs de renda variável e BDRs: 15% em qualquer valor. Compensam prejuízo com as ações.
- Fundos imobiliários e Fiagro: 20% em qualquer valor, com prejuízo em controle separado.
- FI-Infra: ganho isento para pessoa física.
- Exterior (EUA e cripto): sem DARF mensal, 15% ao ano na declaração (Lei 14.754/2023).

O imposto do mês menos o IRRF ("dedo-duro", 0,005%) vira um DARF de código 6015, com vencimento no
último dia útil do mês seguinte; abaixo de R$ 10, passa para o mês seguinte. O aplicativo não sabe o
horário das operações: compra e venda do mesmo ativo no mesmo dia (day trade, 20%) só gera um aviso.

Apoia a declaração, não a substitui: os valores são estimativas a partir dos lançamentos e os
códigos da declaração devem ser conferidos no programa da Receita.
"""

import calendar
import logging
from datetime import date, timedelta

from . import database as db
from . import holidays as hd
from . import market_data
from . import portfolio as pf
from . import tax_alert
from .core import add_up, fmt_brl, fmt_date, fmt_num, fmt_qty, today_iso

log = logging.getLogger(__name__)

PAYMENTS_TABLE = "tax_payments"
OPENING_TABLE = "tax_opening"

RATE_COMMON = pf.STOCK_TAX     # ações, ETFs de renda variável e BDRs
RATE_FUNDS = 0.20              # fundos imobiliários e Fiagro
RATE_FOREIGN = 0.15            # exterior, na declaração anual
SOURCE_RATE = 0.00005          # IRRF ("dedo-duro") sobre o valor das vendas
SOURCE_MIN = 1                 # retenção de até R$ 1 é dispensada
DARF_MIN = 10                  # abaixo disso o imposto passa para o mês seguinte
DARF_CODE = "6015"
LATE_DAILY = 0.0033            # multa de mora por dia de atraso
LATE_MAX = 0.20                # limite da multa
DUE_SOON_DAYS = 7              # o alerta de vencimento fica mais forte a esta distância
SICALC_URL = "https://sicalc.receita.economia.gov.br/sicalc/principal"

# categoria → (rótulo, palavra na descrição do bem, grupo e código de Bens e Direitos, conferir?)
CATEGORIES = {
    "acao": ("Ação", ("ação", "ações"), "03 / 01", False),
    "etf": ("ETF", ("cota do ETF", "cotas do ETF"), "07 / 09", True),
    "bdr": ("BDR", ("BDR", "BDRs"), "04 / 04", True),
    "fii": ("Fundo imobiliário", ("cota do fundo", "cotas do fundo"), "07 / 03", False),
    "infra": ("FI-Infra", ("cota do fundo", "cotas do fundo"), "07 / 99", True),
    "exterior": ("Exterior", ("unidade de", "unidades de"), "03 / 01 (ação) ou 07 / 99 (ETF)", True),
    "cripto": ("Cripto", ("unidade de", "unidades de"), "08 / 01 (Bitcoin) ou 08 / 02 (outras)", True),
}
FOREIGN = ("exterior", "cripto")
INFRA_WORDS = ("INFRA", "INCENTIVAD")
# categoria → onde declarar os proventos
INCOME_WHERE = {
    "acao": "Dividendos: Rendimentos Isentos, código 09. JCP: Tributação Exclusiva, código 10.",
    "etf": "Confira no informe da corretora.",
    "bdr": "Tributáveis: carnê-leão no mês do recebimento (DARF 0190) e Rendimentos Tributáveis do Exterior.",
    "fii": "Rendimentos Isentos, código 26.",
    "infra": "Rendimentos Isentos; confira o código no informe.",
    "exterior": "Ficha de Aplicações Financeiras e Lucros e Dividendos no Exterior.",
    "cripto": "Ficha de Aplicações Financeiras e Lucros e Dividendos no Exterior.",
}


# ---------------------------------------------------------------- Tipo de cada ativo

def _universe():
    try:
        return {r["ticker"]: r for r in db.read_table("universe")}
    except Exception:
        log.warning("não foi possível ler a lista de ativos salva; tipo pelo código e pela classe", exc_info=True)
        return {}


def category_of(portfolio, ticker, universe):
    """(categoria, nome, assumed): assumed quando o tipo foi deduzido da classe da carteira, sem o
    nome na lista de ativos (ETF de renda fixa, por exemplo, não está nela)."""
    market = pf.market_of(portfolio, ticker)
    if market == "crypto":
        return "cripto", "", False
    if market != "b3":
        return "exterior", "", False
    base = ticker[:-1] if len(ticker) >= 6 and ticker.endswith("F") else ticker
    row = universe.get(base) or {}
    name = row.get("name") or ""
    kind = row.get("kind") or market_data.asset_kind(base, name)
    if kind == "acoes":
        return "acao", name, False
    if kind == "bdrs":
        return "bdr", name, False
    if kind == "etfs":
        return "etf", name, False
    if kind == "fundos":
        return ("infra" if any(w in name.upper() for w in INFRA_WORDS) else "fii"), name, False
    cls = ((portfolio.get("assets") or {}).get(ticker) or {}).get("class")
    if cls == "Ações":
        return "acao", name, True
    if cls in ("ETFs Brasil", "Internacionais"):
        return "etf", name, True
    return "fii", name, True


def categories(portfolio, universe=None):
    """{ticker: {category, name, assumed}} de todos os ativos com lançamentos."""
    universe = _universe() if universe is None else universe
    out = {}
    for t in {tx["ticker"] for tx in portfolio.get("transactions") or []}:
        category, name, assumed = category_of(portfolio, t, universe)
        out[t] = {"category": category, "name": name, "assumed": assumed}
    return out


# ---------------------------------------------------------------- Cálculos (puros)

def sales_of(transactions, cats):
    """Cada venda em reais, com o custo pelo preço médio (como portfolio.build_positions):
    [{date, month, ticker, category, quantity, gross, fees, cost, gain, sameDayBuy}]."""
    cost, qty = {}, {}
    buy_days = set()
    out = []
    for tx in pf.by_date(transactions):
        t = tx["ticker"]
        if tx["type"] == "compra":
            cost[t] = cost.get(t, 0) + pf.tx_flow(tx)
            qty[t] = qty.get(t, 0) + tx["quantity"]
            buy_days.add((t, tx["date"]))
            continue
        avg = cost.get(t, 0) / qty[t] if qty.get(t) else 0
        fx = tx.get("fx") or 1
        sold_cost = avg * tx["quantity"]
        proceeds = -pf.tx_flow(tx)
        out.append({"date": tx["date"], "month": tx["date"][:7], "ticker": t, "category": cats[t]["category"],
                    "categoryLabel": CATEGORIES[cats[t]["category"]][0],
                    "quantity": tx["quantity"], "gross": tx["quantity"] * tx["price"] * fx,
                    "fees": (tx.get("fees") or 0) * fx, "cost": sold_cost, "gain": proceeds - sold_cost,
                    "sameDayBuy": (t, tx["date"]) in buy_days})
        qty[t] = qty.get(t, 0) - tx["quantity"]
        cost[t] = cost.get(t, 0) - sold_cost
        if qty[t] < pf.EPS:
            qty[t] = cost[t] = 0
    return out


def due_date(month, holidays=()):
    """Vencimento do DARF do mês "AAAA-MM": último dia útil do mês seguinte, sem fins de semana e
    sem os feriados (holidays: datas "MM-DD" e "AAAA-MM-DD", holidays.keys())."""
    year, m = int(month[:4]), int(month[5:7])
    year, m = (year + 1, 1) if m == 12 else (year, m + 1)
    day = date(year, m, calendar.monthrange(year, m)[1])
    while day.weekday() >= 5 or hd.is_holiday(day.isoformat(), holidays):
        day -= timedelta(days=1)
    return day.isoformat()


def source_tax(sales):
    """IRRF estimado das vendas: 0,005% do valor vendido em cada dia, quando passa de R$ 1. O valor
    real está na nota de corretagem."""
    per_day = {}
    for s in sales:
        per_day.setdefault(s["date"], []).append(s["gross"])
    return add_up(v for v in (add_up(day) * SOURCE_RATE for day in per_day.values()) if v > SOURCE_MIN)


def _compensate(result, loss):
    """Resultado do mês contra o prejuízo acumulado → (base, prejuízo usado, novo prejuízo)."""
    if result <= 0:
        return 0.0, 0.0, loss - result
    used = min(loss, result)
    return result - used, used, loss - used


def _month_steps(r):
    """A conta do mês em frases, na ordem em que é feita."""
    steps = []
    if r["stockSold"]:
        if r["stockExempt"]:
            steps.append(f"Vendas de ações: {fmt_brl(r['stockSold'])}, até {fmt_brl(pf.STOCK_EXEMPTION)}: "
                         + (f"lucro de {fmt_brl(r['exemptGain'])} isento." if r["exemptGain"] else "não há lucro a tributar."))
        else:
            steps.append(f"Vendas de ações: {fmt_brl(r['stockSold'])}, acima de {fmt_brl(pf.STOCK_EXEMPTION)}: "
                         f"o resultado de {fmt_brl(r['stockGain'])} entra na conta.")
    if r["otherGain"]:
        steps.append(f"ETFs e BDRs, sem isenção: resultado de {fmt_brl(r['otherGain'])}.")
    if r["taxableCommon"] or r["lossUsedCommon"]:
        steps.append(f"Resultado a {fmt_num(RATE_COMMON * 100, 0)}%: {fmt_brl(r['taxableCommon'])}"
                     + (f", menos {fmt_brl(r['lossUsedCommon'])} de prejuízo anterior" if r["lossUsedCommon"] else "")
                     + f" = base de {fmt_brl(r['baseCommon'])}, imposto de {fmt_brl(r['taxCommon'])}.")
    if r["fundGain"]:
        steps.append(f"Fundos imobiliários a {fmt_num(RATE_FUNDS * 100, 0)}%: resultado de {fmt_brl(r['fundGain'])}"
                     + (f", menos {fmt_brl(r['lossUsedFunds'])} de prejuízo anterior" if r["lossUsedFunds"] else "")
                     + f" = base de {fmt_brl(r['baseFunds'])}, imposto de {fmt_brl(r['taxFunds'])}.")
    if r["infraGain"]:
        steps.append(f"FI-Infra: resultado de {fmt_brl(r['infraGain'])}, isento para pessoa física.")
    if r["creditUsed"]:
        steps.append(f"IRRF (dedo-duro) abatido: {fmt_brl(r['creditUsed'])}.")
    if r["carriedIn"]:
        steps.append(f"Valor abaixo de {fmt_brl(DARF_MIN)} trazido de meses anteriores: {fmt_brl(r['carriedIn'])}.")
    if r["darf"]:
        steps.append(f"DARF: {fmt_brl(r['darf'])}, com vencimento em {fmt_date(r['dueDate'])}.")
    elif r["carryOut"]:
        steps.append(f"O total de {fmt_brl(r['carryOut'])} fica abaixo de {fmt_brl(DARF_MIN)}: passa para o mês seguinte, sem DARF agora.")
    else:
        steps.append("Não há DARF a pagar neste mês.")
    return steps


def ledger(sales, opening=None, payments=None, today=None, holidays=()):
    """Apuração mês a mês das vendas na B3, do mês mais antigo ao mais novo, carregando prejuízos,
    IRRF e valores abaixo de R$ 10 → {months, lossCommon, lossFunds, carry, credit}. holidays:
    datas ISO dos feriados cadastrados, que antecipam o vencimento."""
    today = today or today_iso()
    payments = payments or {}
    loss_common = (opening or {}).get("common") or 0.0
    loss_funds = (opening or {}).get("funds") or 0.0
    credit = carry = 0.0
    credit_year = None
    domestic = [s for s in sales if s["category"] not in FOREIGN]
    months = []
    credit_by_year = {}
    for month in sorted({s["month"] for s in domestic}):
        items = [s for s in domestic if s["month"] == month]
        by = lambda *cats: [s for s in items if s["category"] in cats]
        stock_sold = add_up(s["gross"] for s in by("acao"))
        stock_gain = add_up(s["gain"] for s in by("acao"))
        other_gain = add_up(s["gain"] for s in by("etf", "bdr"))
        fund_gain = add_up(s["gain"] for s in by("fii"))
        infra_gain = add_up(s["gain"] for s in by("infra"))
        exempt = stock_sold <= pf.STOCK_EXEMPTION
        exempt_gain = max(stock_gain, 0.0) if exempt else 0.0
        taxable_common = other_gain + (min(stock_gain, 0.0) if exempt else stock_gain)
        base_common, used_common, loss_common = _compensate(taxable_common, loss_common)
        base_funds, used_funds, loss_funds = _compensate(fund_gain, loss_funds)
        tax_common = base_common * RATE_COMMON
        tax_funds = base_funds * RATE_FUNDS
        tax = tax_common + tax_funds
        if credit_year != month[:4]:   # o IRRF que sobra no ano vai para a declaração
            if credit_year:
                credit_by_year[credit_year] = credit
            credit, credit_year = 0.0, month[:4]
        source = source_tax([s for s in items if s["category"] != "infra"])
        credit += source
        credit_used = min(credit, tax)
        credit -= credit_used
        carried_in = carry
        total = tax - credit_used + carry
        darf = total if total >= DARF_MIN else 0.0
        carry = 0.0 if darf else total
        payment = payments.get(month)
        due = due_date(month, holidays)
        status = "paid" if payment else "none" if not darf else "overdue" if today > due else "pending"
        late_days = (date.fromisoformat(today) - date.fromisoformat(due)).days if status == "overdue" else 0
        row = {"month": month, "label": tax_alert.month_label(month), "stockSold": stock_sold, "stockGain": stock_gain,
               "stockExempt": exempt, "exemptGain": exempt_gain, "otherGain": other_gain, "fundGain": fund_gain,
               "infraGain": infra_gain, "taxableCommon": taxable_common, "lossUsedCommon": used_common,
               "baseCommon": base_common, "taxCommon": tax_common, "lossCommon": loss_common,
               "lossUsedFunds": used_funds, "baseFunds": base_funds, "taxFunds": tax_funds, "lossFunds": loss_funds,
               "tax": tax, "sourceTax": source, "creditUsed": credit_used, "carriedIn": carried_in, "darf": darf,
               "carryOut": carry, "dueDate": due, "status": status, "paid": payment,
               "paidDiff": bool(payment and abs(payment["amount"] - darf) >= 0.01), "lateDays": late_days,
               "lateFine": darf * min(late_days * LATE_DAILY, LATE_MAX), "code": DARF_CODE,
               "period": f"{month[5:7]}/{month[:4]}", "sales": items}
        row["steps"] = _month_steps(row)
        months.append(row)
    if credit_year:
        credit_by_year[credit_year] = credit
    return {"months": months, "lossCommon": loss_common, "lossFunds": loss_funds, "carry": carry,
            "creditByYear": credit_by_year}


def foreign_years(sales, dividends=()):
    """Exterior por ano: {ano: {gain, dividends, lossIn, lossUsed, base, tax, lossOut}}, com o
    prejuízo do ano passando para os seguintes. Os dividendos ficam à parte da estimativa porque o
    imposto retido nos EUA pode virar crédito."""
    years = sorted({s["month"][:4] for s in sales if s["category"] in FOREIGN} | {d["exDate"][:4] for d in dividends})
    out, loss = {}, 0.0
    for y in years:
        gain = add_up(s["gain"] for s in sales if s["category"] in FOREIGN and s["month"][:4] == y)
        base, used, new_loss = _compensate(gain, loss)
        out[y] = {"year": y, "gain": gain, "dividends": add_up(d["total"] for d in dividends if d["exDate"][:4] == y),
                  "lossIn": loss, "lossUsed": used, "base": base, "tax": base * RATE_FOREIGN, "lossOut": new_loss}
        loss = new_loss
    return out


def holdings_rows(portfolio, cats, year):
    """Bens e Direitos do ano: quantidade e custo em 31/12 do ano anterior e do ano, com a descrição
    pronta para copiar. Uma linha por ativo com posição em uma das duas datas."""
    start, end = f"{int(year) - 1}-12-31", f"{year}-12-31"
    txs = portfolio.get("transactions") or []
    before = pf.build_positions([t for t in txs if t["date"] <= start])
    after = pf.build_positions([t for t in txs if t["date"] <= end])
    rows = []
    for t in sorted(set(before) | set(after)):
        b, a = before.get(t) or {}, after.get(t) or {}
        if not b.get("quantity") and not a.get("quantity"):
            continue
        info = cats[t]
        label, nouns, code, verify = CATEGORIES[info["category"]]
        name = f" – {info['name']}" if info["name"] else ""
        if a.get("quantity"):
            text = f"{fmt_qty(a['quantity'])} {nouns[0 if a['quantity'] <= 1 else 1]} {t}{name}, custo médio de {fmt_brl(a['avgPrice'])}"
            text += (", em custódia no exterior; custo em reais pelo câmbio de cada compra."
                     if info["category"] in FOREIGN else ", na corretora [nome e CNPJ da corretora].")
        else:
            text = f"{t}{name}: posição zerada em {year}."
        rows.append({"ticker": t, "name": info["name"], "category": info["category"], "categoryLabel": label,
                     "code": code, "verify": verify, "quantityBefore": b.get("quantity") or 0, "costBefore": b.get("cost") or 0,
                     "quantityAfter": a.get("quantity") or 0, "costAfter": a.get("cost") or 0, "text": text})
    return rows


def fixed_income_rows(portfolio, year):
    start, end = f"{int(year) - 1}-12-31", f"{year}-12-31"
    rows = []
    for item in portfolio.get("fixedIncome") or []:
        entries = item.get("entries") or []
        before, after = pf.fixed_income_invested_at(entries, start), pf.fixed_income_invested_at(entries, end)
        if before or after:
            rows.append({"name": item["name"], "before": before, "after": after})
    return rows


def income_rows(dividends, cats, year):
    """Proventos estimados do ano por ativo (pela data-ex; o informe usa a data de pagamento)."""
    totals = {}
    for d in dividends:
        if d["exDate"][:4] == year and d["ticker"] in cats:
            totals.setdefault(d["ticker"], []).append(d["total"])
    return [{"ticker": t, "categoryLabel": CATEGORIES[cats[t]["category"]][0], "total": add_up(v),
             "where": INCOME_WHERE[cats[t]["category"]]} for t, v in sorted(totals.items(), key=lambda kv: -add_up(kv[1]))]


def _pending(months):
    return [m for m in months if m["status"] in ("pending", "overdue")]


def darf_guide(m, today):
    """O tutorial de emitir e pagar o DARF, com os valores do mês m (None: texto geral). Passos:
    [{title, text, fields: [{label, value, copy}], link, warn}]."""
    code_field = {"label": "Código ou nome da receita", "value": DARF_CODE, "copy": DARF_CODE}
    if m:
        fields = [code_field, {"label": "Período de apuração", "value": m["period"], "copy": m["period"]},
                  {"label": "Valor principal", "value": fmt_brl(m["darf"]), "copy": fmt_num(m["darf"], 2)}]
        check = (f"Este DARF é de {fmt_brl(m['darf'])}, referente às vendas de {m['label']}. Use \"Ver conta\" na apuração e "
                 "compare as vendas com as suas notas de corretagem.")
    else:
        fields = [code_field, {"label": "Período de apuração", "value": "o mês da venda (MM/AAAA)"},
                  {"label": "Valor principal", "value": "o valor do DARF na apuração"}]
        check = "Veja o valor na apuração mês a mês e compare as vendas com as suas notas de corretagem."
    overdue = bool(m and m["status"] == "overdue")
    calculate = "Clique em \"Calcular\" e confira se o total está igual ao valor principal."
    late = None
    if overdue:
        calculate = "Clique em \"Calcular\". Como o DARF está atrasado, o total vem maior que o valor principal."
        late = (f"Está atrasado há {m['lateDays']} dia{'s' if m['lateDays'] != 1 else ''}: o Sicalc soma a multa e os juros Selic. "
                f"Não some nada por conta própria ao valor principal; a multa estimada aqui é de {fmt_brl(m['lateFine'])}.")
    deadline = (f"até {fmt_date(m['dueDate'])}" if m and not overdue else "no dia para o qual o documento foi calculado" if overdue
                else "até o vencimento")
    return [
        {"title": "Confira o valor", "text": check},
        {"title": "Abra o Sicalc da Receita Federal",
         "text": "Em \"Geração e impressão do DARF\", escolha \"Preenchimento rápido\". Não precisa instalar nenhum programa.", "link": True},
        {"title": "Identifique-se", "text": "Selecione Pessoa Física, informe seu CPF e sua data de nascimento e confirme que não é um robô."},
        {"title": "Preencha os dados", "text": f"O código {DARF_CODE} é o de ganhos líquidos em operações em bolsa. Deixe o número de referência em branco.",
         "fields": fields},
        {"title": "Calcule", "text": calculate, "warn": late},
        {"title": "Emita o documento", "text": "Selecione o DARF calculado e clique em \"Emitir DARF\". Ele abre em PDF, com o código de barras. Salve o arquivo."},
        {"title": "Pague", "text": f"No aplicativo do banco, use o pagamento de contas ou de tributos e leia o código de barras. Se o documento trouxer "
                                   f"QR Code Pix, você também pode pagar por ele. Pague {deadline}.",
         "warn": "Se passar da data, não pague o documento antigo: emita outro no Sicalc, que recalcula os acréscimos."},
        {"title": "Guarde e registre", "text": "Guarde o PDF e o comprovante por pelo menos 5 anos. Depois, clique em \"Marcar como pago\" aqui: o alerta some "
                                               "e o valor pago entra no resumo da declaração."},
    ]


def _due_phrase(m, today):
    if m["status"] == "overdue":
        return f"venceu em {fmt_date(m['dueDate'])}, há {m['lateDays']} dia{'s' if m['lateDays'] != 1 else ''}"
    days = (date.fromisoformat(m["dueDate"]) - date.fromisoformat(today)).days
    if days == 0:
        return "vence hoje"
    return f"vence em {fmt_date(m['dueDate'])}, em {days} dia{'s' if days != 1 else ''}"


def due_level(pending, today):
    """over = atrasado; near = vence em até DUE_SOON_DAYS dias; info = ainda há tempo."""
    if any(m["status"] == "overdue" for m in pending):
        return "over"
    soonest = min(m["dueDate"] for m in pending)
    return "near" if (date.fromisoformat(soonest) - date.fromisoformat(today)).days <= DUE_SOON_DAYS else "info"


def due_alert_from(months, today):
    """Alerta de vencimento a partir da apuração, ou None sem DARF em aberto."""
    pending = _pending(months)
    if not pending:
        return None
    first = pending[0]
    if len(pending) == 1:
        title = f"DARF de {first['label']}: {fmt_brl(first['darf'])} {_due_phrase(first, today)}"
    else:
        title = (f"{len(pending)} DARFs em aberto, somando {fmt_brl(add_up(m['darf'] for m in pending))}. "
                 f"O mais antigo, de {first['label']}, {_due_phrase(first, today)}")
    lines = []
    if any(m["status"] == "overdue" for m in pending):
        fine = add_up(m["lateFine"] for m in pending)
        lines.append(f"Com atraso há multa de {fmt_num(LATE_DAILY * 100, 2)}% ao dia, até {fmt_num(LATE_MAX * 100, 0)}% "
                     f"(cerca de {fmt_brl(fine)} até hoje), mais juros Selic. O Sicalc calcula o total.")
    lines.append(f"Código {DARF_CODE}. Se já pagou, marque como pago na sub-aba Impostos.")
    return {"show": True, "level": due_level(pending, today), "title": title, "lines": lines,
            "link": {"href": "#impostos", "text": "Ver os dados do DARF"}}


# ---------------------------------------------------------------- Dados salvos

def load_payments(portfolio_id):
    """{mês: {amount, paidOn}} dos DARFs marcados como pagos."""
    try:
        rows = db.read_table(PAYMENTS_TABLE)
    except Exception:
        log.warning("não foi possível ler os DARFs pagos", exc_info=True)
        return {}
    return {r["month"]: {"amount": r["amount"] or 0, "paidOn": r["paid_on"]}
            for r in rows if r["portfolio_id"] == portfolio_id}


def load_opening(portfolio_id):
    """Prejuízos de antes dos lançamentos, informados pelo usuário: {common, funds}."""
    try:
        rows = db.read_table(OPENING_TABLE)
    except Exception:
        log.warning("não foi possível ler os prejuízos iniciais", exc_info=True)
        return {"common": 0.0, "funds": 0.0}
    for r in rows:
        if r["portfolio_id"] == portfolio_id:
            return {"common": r["common"] or 0.0, "funds": r["funds"] or 0.0}
    return {"common": 0.0, "funds": 0.0}


def _ledger_for(portfolio):
    cats = categories(portfolio)
    sales = sales_of(portfolio.get("transactions") or [], cats)
    return cats, sales, ledger(sales, load_opening(portfolio["id"]), load_payments(portfolio["id"]), holidays=hd.keys())


def mark_paid(portfolio, body):
    """{month, paid}: marca o DARF do mês como pago (pelo valor apurado, hoje) ou desfaz a marcação.
    → mensagem; ValueError com a mensagem quando o mês não tem o que marcar."""
    month = body.get("month")
    months = {m["month"]: m for m in _ledger_for(portfolio)[2]["months"]}
    rows = [r for r in db.read_table(PAYMENTS_TABLE)
            if not (r["portfolio_id"] == portfolio["id"] and r["month"] == month)]
    if body.get("paid") is False:
        db.write_tables({PAYMENTS_TABLE: rows})
        return "Marcação desfeita."
    row = months.get(month)
    if not row or not row["darf"]:
        raise ValueError("Este mês não tem DARF a pagar.")
    rows.append({"portfolio_id": portfolio["id"], "month": month, "amount": row["darf"], "paid_on": today_iso()})
    db.write_tables({PAYMENTS_TABLE: rows})
    return f"DARF de {row['label']} marcado como pago."


def save_opening(portfolio, body):
    """{common, funds} em reais, vazio = 0. ValueError quando o valor é inválido ou negativo."""
    common, funds = pf.parse_number(body.get("common"), 0), pf.parse_number(body.get("funds"), 0)
    if common is None or funds is None or common < 0 or funds < 0:
        raise ValueError("Informe os prejuízos em reais, sem sinal negativo.")
    rows = [r for r in db.read_table(OPENING_TABLE) if r["portfolio_id"] != portfolio["id"]]
    rows.append({"portfolio_id": portfolio["id"], "common": common, "funds": funds})
    db.write_tables({OPENING_TABLE: rows})
    return "Prejuízos salvos. A apuração já foi refeita com eles."


# ---------------------------------------------------------------- O que a página mostra

def due_alert(portfolio, settings=None):
    """Topo da Visão geral: DARF em aberto, ou None (sem DARF ou com o aviso de imposto desligado)."""
    if not (settings or tax_alert.load())["enabled"]:
        return None
    return due_alert_from(_ledger_for(portfolio)[2]["months"], today_iso())


def _moving_warning(months, keys):
    """Aviso quando um feriado móvel fora da lista antecipa o vencimento de um DARF em aberto. Sem
    esse caso, nada a dizer: os móveis raramente caem no último dia útil do mês."""
    for m in _pending(months):
        year = int(m["dueDate"][:4])
        with_moving = set(keys) | {h["date"] for h in hd.moving(year)}
        if due_date(m["month"], with_moving) != m["dueDate"]:
            return {"level": "near", "title": f"O DARF de {m['label']} pode vencer antes",
                    "text": f"{fmt_date(m['dueDate'])} é um feriado móvel que não está na sua lista. Inclua os feriados móveis de {year} "
                            "na aba Configurações, no topo da página."}
    return None


def _attention(portfolio, cats, sales, data, foreign, year, today, portfolio_count, dividends_loaded):
    """O que merece atenção agora: [{level, title, text}], do mais urgente ao informativo."""
    out = []
    alert = due_alert_from(data["months"], today)
    if alert:
        out.append({"level": alert["level"], "title": alert["title"], "text": " ".join(alert["lines"][:-1])})
    moving_warning = _moving_warning(data["months"], hd.keys())
    if moving_warning:
        out.append(moving_warning)
    month_status = tax_alert.status(portfolio, settings={"enabled": True, "warnAt": tax_alert.load()["warnAt"]})
    if month_status and month_status["show"]:
        out.append({"level": month_status["level"], "title": month_status["title"], "text": " ".join(month_status["lines"])})
    if any(m["paidDiff"] for m in data["months"]):
        names = ", ".join(m["label"] for m in data["months"] if m["paidDiff"])
        out.append({"level": "near", "title": "O valor pago difere da apuração atual",
                    "text": f"Em {names}, os lançamentos mudaram depois do pagamento. Confira se falta pagar ou se há DARF a retificar."})
    same_day = sorted({(s["ticker"], s["date"]) for s in sales if s["sameDayBuy"]}, key=lambda x: x[1])
    if same_day:
        names = ", ".join(f"{t} em {fmt_date(d)}" for t, d in same_day[-5:])
        out.append({"level": "near", "title": "Possível day trade",
                    "text": f"Há compra e venda no mesmo dia: {names}. Day trade paga 20%, com prejuízo em controle separado, e o "
                            "aplicativo calcula como operação comum. Confira na nota de corretagem."})
    assumed = sorted(t for t, c in cats.items() if c["assumed"])
    if assumed:
        out.append({"level": "near", "title": "Tipo de ativo deduzido da classe",
                    "text": f"{', '.join(assumed)} não está na lista de ativos. Se for ETF de renda fixa, a regra é outra: "
                            "o imposto é retido na fonte e não há DARF."})
    if any(c["category"] == "infra" for c in cats.values()):
        out.append({"level": "info", "title": "FI-Infra", "text": "Tratado como isento pelo nome do fundo. Confirme que é mesmo um FI-Infra no regulamento."})
    if any(c["category"] == "cripto" for c in cats.values()):
        out.append({"level": "info", "title": "Cripto tratada como aplicação no exterior",
                    "text": "Em corretora brasileira a regra é outra: vendas de até R$ 35 mil no mês isentas e, acima disso, DARF mensal "
                            "de código 4600. Confira onde está a custódia."})
    y = foreign.get(year)
    if y and (y["gain"] or y["dividends"]):
        out.append({"level": "info", "title": f"Exterior em {year}: acerto na declaração, sem DARF mensal",
                    "text": f"Resultado de vendas de {fmt_brl(y['gain'])}, imposto estimado de {fmt_brl(y['tax'])} e dividendos brutos de "
                            f"{fmt_brl(y['dividends'])}. Reserve o valor para o ajuste anual."})
    if portfolio_count > 1:
        out.append({"level": "info", "title": "A Receita apura por CPF, não por carteira",
                    "text": "Esta conta usa só esta carteira. O limite de R$ 20 mil e os prejuízos valem para o total das suas "
                            "corretoras: se outras carteiras também forem reais, some os valores."})
    if not dividends_loaded:
        out.append({"level": "near", "title": "Proventos indisponíveis agora",
                    "text": "As cotações não carregaram, então a declaração está sem os proventos. O restante não depende delas."})
    return out


def method():
    """A descrição do cálculo, com os valores das constantes."""
    limit = fmt_brl(pf.STOCK_EXEMPTION)
    return [
        {"title": "1. Lucro de cada venda",
         "lines": ["Lucro = valor da venda − taxas − preço médio × quantidade.",
                   "O preço médio soma as compras e as taxas delas. Vendas não mudam o preço médio, e uma posição zerada recomeça do zero."]},
        {"title": "2. Isenção de ações",
         "lines": [f"Conta-se o valor bruto vendido em ações no mês, não o lucro. Até {limit}, o lucro das ações é isento.",
                   f"Se passar de {limit}, mesmo por pouco, o lucro de todas as vendas de ações do mês é tributado.",
                   "Prejuízo em ações continua compensável, mesmo em mês isento. Units seguem a conta de ações; em caso de dúvida, confirme com um contador."]},
        {"title": "3. Alíquota por tipo de ativo",
         "lines": [f"Ações, ETFs de renda variável e BDRs: {fmt_num(RATE_COMMON * 100, 0)}%. ETFs e BDRs não têm isenção.",
                   f"Fundos imobiliários e Fiagro: {fmt_num(RATE_FUNDS * 100, 0)}%, sem isenção.",
                   "FI-Infra: ganho isento para pessoa física.",
                   "ETF de renda fixa: imposto retido na fonte, sem DARF. Day trade: 20%, com controle próprio; o aplicativo só avisa."]},
        {"title": "4. Compensação de prejuízos",
         "lines": ["Prejuízos de ações, ETFs e BDRs formam um saldo; os de fundos imobiliários, outro. Um não abate o outro.",
                   "Os saldos não expiram, mas precisam constar na declaração de cada ano. Lucro isento de ações não consome o saldo.",
                   "Se você já tinha prejuízo antes dos lançamentos desta carteira, informe em \"Prejuízos anteriores\"."]},
        {"title": "5. Do imposto ao DARF",
         "lines": [f"Imposto do mês − IRRF (dedo-duro de {fmt_num(SOURCE_RATE * 100, 3)}% das vendas, estimado aqui; o valor exato está na nota) "
                   f"= valor do DARF, código {DARF_CODE}.",
                   f"Abaixo de {fmt_brl(DARF_MIN)} não se emite DARF: o valor soma ao do mês seguinte.",
                   "O vencimento é o último dia útil do mês seguinte ao da venda. Se esse dia estiver na lista de feriados da aba Configurações, o vencimento antecipa para o dia útil anterior.",
                   f"Com atraso: multa de {fmt_num(LATE_DAILY * 100, 2)}% ao dia, até {fmt_num(LATE_MAX * 100, 0)}%, mais juros Selic."]},
        {"title": "6. Exterior (EUA e cripto)",
         "lines": [f"Não há DARF mensal: ganhos e rendimentos pagam {fmt_num(RATE_FOREIGN * 100, 0)}% na declaração anual (Lei 14.754/2023), sem isenção por valor.",
                   "O custo e a venda são convertidos pelo câmbio de cada lançamento. Prejuízos abatem ganhos do exterior e passam para os anos seguintes.",
                   "Os 30% retidos nos EUA sobre dividendos podem virar crédito; confirme com um contador."]},
        {"title": "7. Proventos",
         "lines": ["Dividendos de empresas brasileiras são isentos até R$ 50 mil por mês de cada empresa. Acima disso há retenção de 10% (Lei 15.270/2025).",
                   f"JCP tem {fmt_num(pf.JCP_TAX * 100, 1)}% retidos na fonte desde 2026 (15% até 2025). Rendimentos de fundos imobiliários seguem isentos, conforme as regras do fundo.",
                   "Rendas altas podem pagar também o imposto mínimo anual (IRPFM), a partir de R$ 600 mil por ano."]},
    ]


def payload(portfolio, year=None, analysis=None, portfolio_count=1):
    """Tudo o que a sub-aba Impostos mostra. analysis ({"dividends": os proventos já pagos}, de
    portfolio_service.receivables); sem ela, o resto funciona. ValueError com um ano inválido."""
    today = today_iso()
    txs = portfolio.get("transactions") or []
    years = sorted({t["date"][:4] for t in txs} | {today[:4]}, reverse=True)
    year = year or today[:4]
    if year not in years:
        raise ValueError("Ano sem lançamentos nesta carteira.")
    cats, sales, data = _ledger_for(portfolio)
    dividends = (analysis or {}).get("dividends") or []
    foreign_dividends = [d for d in dividends if cats.get(d["ticker"], {}).get("category") in FOREIGN]
    foreign = foreign_years(sales, foreign_dividends)
    months = [m for m in data["months"] if m["month"][:4] == year]
    pending = _pending(data["months"])
    next_darf = pending[0] if pending else None
    exempt = add_up(m["exemptGain"] for m in months)
    return {
        "today": today, "year": year, "years": years, "code": DARF_CODE, "sicalcUrl": SICALC_URL,
        "guide": {m["month"]: darf_guide(m, today) for m in data["months"] if m["darf"]},
        "guideGeneral": darf_guide(None, today),
        "kpis": {"pending": add_up(m["darf"] for m in pending), "pendingCount": len(pending),
                 "nextDue": next_darf["dueDate"] if next_darf else None, "level": due_level(pending, today) if pending else None,
                 "yearTax": add_up(m["tax"] for m in months), "yearExempt": exempt,
                 "lossCommon": data["lossCommon"], "lossFunds": data["lossFunds"], "carry": data["carry"]},
        "attention": _attention(portfolio, cats, sales, data, foreign, year, today, portfolio_count, analysis is not None),
        "months": months, "nextDarf": next_darf,
        "opening": load_opening(portfolio["id"]),
        "foreign": foreign.get(year),
        "declaration": {
            "year": year, "deliveredIn": int(year) + 1,
            "holdings": holdings_rows(portfolio, cats, year),
            "fixedIncome": fixed_income_rows(portfolio, year),
            "income": income_rows(dividends, cats, year),
            "incomeLoaded": analysis is not None,
            "exemptGain": exempt,
            "exemptWhere": "Rendimentos Isentos e Não Tributáveis, código 20 (ganhos com ações em vendas de até R$ 20 mil no mês).",
            "sourceLeft": data["creditByYear"].get(year) or 0,
            "sold": add_up(s["gross"] for s in sales if s["month"][:4] == year),
            "darfPaid": add_up(m["paid"]["amount"] for m in months if m["paid"]),
            "lossCommonEnd": months[-1]["lossCommon"] if months else None,
            "lossFundsEnd": months[-1]["lossFunds"] if months else None,
        },
        "method": method(),
    }
