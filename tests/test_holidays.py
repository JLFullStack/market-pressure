"""Calendário de feriados: padrões de data fixa, edição, móveis e efeito no vencimento do DARF."""

from marketpressure.comum import database as db
from marketpressure.configuracoes import holidays as hd
from marketpressure.carteira import tax_report as tr

from .support import DatabaseTestCase


class HolidaysTest(DatabaseTestCase):
    def test_fixed_dates_come_by_default(self):
        self.assertFalse(db.exists(hd.TABLE))
        keys = hd.keys()
        self.assertEqual(len(keys), len(hd.FIXED))
        self.assertTrue(hd.is_holiday("2026-12-25", keys))
        self.assertTrue(hd.is_holiday("2031-11-20", keys))
        self.assertFalse(hd.is_holiday("2026-12-24", keys))
        self.assertFalse(db.exists(hd.TABLE))   # só grava quando o usuário muda algo

    def test_moving_dates(self):
        self.assertEqual(hd.easter(2026).isoformat(), "2026-04-05")
        self.assertEqual(hd.easter(2027).isoformat(), "2027-03-28")
        days = {h["date"]: h["name"] for h in hd.moving(2026)}
        self.assertEqual(days, {"2026-02-16": "Carnaval (segunda)", "2026-02-17": "Carnaval (terça)",
                                "2026-04-03": "Sexta-feira Santa", "2026-06-04": "Corpus Christi"})

    def test_add_annual_and_single_year(self):
        hd.change({"op": "add", "date": "2026-12-08", "name": " Municipal ", "annual": True})
        hd.change({"op": "add", "date": "2026-07-03", "name": "Ponte"})
        keys = hd.keys()
        self.assertTrue(hd.is_holiday("2030-12-08", keys))
        self.assertTrue(hd.is_holiday("2026-07-03", keys))
        self.assertFalse(hd.is_holiday("2027-07-03", keys))
        names = {r["key"]: r["name"] for r in hd.payload()["annual"]}
        self.assertEqual(names["12-08"], "Municipal")
        with self.assertRaises(ValueError):
            hd.change({"op": "add", "date": "2027-12-08", "annual": True})   # o mesmo dia, todo ano
        for bad in ("x", "", None, "2026-13-01"):
            with self.assertRaises(ValueError):
                hd.change({"op": "add", "date": bad})

    def test_kinds(self):
        kinds = {r["date"]: r["kind"] for r in hd.load()}
        self.assertEqual(kinds["12-25"], "nacional")
        self.assertEqual(kinds["12-31"], "bancario")
        moving = {h["name"]: h["kind"] for h in hd.moving(2026)}
        self.assertEqual(moving["Sexta-feira Santa"], "nacional")
        self.assertEqual(moving["Carnaval (terça)"], "bancario")
        self.assertEqual(moving["Corpus Christi"], "bancario")
        hd.change({"op": "add", "date": "2026-07-09", "name": "Revolução", "annual": True, "kind": "estadual"})
        hd.change({"op": "add", "date": "2026-09-08", "name": "Sem tipo", "annual": True})
        rows = {r["key"]: r for r in hd.payload()["annual"]}
        self.assertEqual((rows["07-09"]["kind"], rows["07-09"]["kindLabel"]), ("estadual", "Estadual"))
        self.assertEqual(rows["09-08"]["kind"], hd.DEFAULT_KIND)
        hd.change({"op": "edit", "key": "09-08", "date": "2026-09-08", "name": "Sem tipo", "annual": True, "kind": "municipal"})
        self.assertEqual(next(r["kind"] for r in hd.load() if r["date"] == "09-08"), "municipal")
        with self.assertRaises(ValueError):
            hd.change({"op": "add", "date": "2026-03-03", "kind": "x"})
        self.assertEqual(hd.payload()["kinds"][0], {"key": "nacional", "label": "Nacional"})

    def test_every_kind_moves_the_due_date(self):
        for kind in hd.KINDS:
            hd.change({"op": "add", "date": "2026-05-29", "name": "Teste", "kind": kind})
            self.assertEqual(tr.due_date("2026-04", hd.keys()), "2026-05-28")
            hd.change({"op": "remove", "key": "2026-05-29"})

    def test_edit(self):
        hd.change({"op": "edit", "key": "04-21", "date": "2026-04-22", "name": "Outro nome", "annual": True})
        keys = hd.keys()
        self.assertNotIn("04-21", keys)
        self.assertIn("04-22", keys)
        hd.change({"op": "edit", "key": "04-22", "date": "2026-04-22", "name": "Só o nome", "annual": True})   # mesma data
        self.assertEqual(next(r["name"] for r in hd.load() if r["date"] == "04-22"), "Só o nome")
        hd.change({"op": "edit", "key": "04-22", "date": "2026-04-22", "name": "Um ano só"})   # anual → um ano
        self.assertIn("2026-04-22", hd.keys())
        with self.assertRaises(ValueError):
            hd.change({"op": "edit", "key": "01-01", "date": "2026-05-01", "annual": True})   # já existe
        with self.assertRaises(ValueError):
            hd.change({"op": "edit", "key": "09-09", "date": "2026-09-10"})   # não existe

    def test_remove_all_stays_empty_and_restore(self):
        for key in list(hd.keys()):
            hd.change({"op": "remove", "key": key})
        self.assertEqual(hd.load(), [])   # a lista vazia é a escolha do usuário, não volta o padrão
        self.assertIn(str(len(hd.FIXED)), hd.change({"op": "restore"}))
        self.assertEqual(len(hd.keys()), len(hd.FIXED))
        self.assertIn("já estavam", hd.change({"op": "restore"}))

    def test_moving_button(self):
        self.assertIn("4 feriados", hd.change({"op": "moving", "year": "2026"}))
        self.assertIn("já estavam", hd.change({"op": "moving", "year": 2026}))
        data = hd.payload()
        self.assertEqual(len(data["dated"]), 4)
        self.assertEqual(len(data["annual"]), len(hd.FIXED))
        self.assertEqual(data["dated"][0]["label"], "16/02/2026")
        self.assertEqual(data["annual"][0]["label"], "01/01")
        for bad in ("abc", "1500", ""):
            with self.assertRaises(ValueError):
                hd.change({"op": "moving", "year": bad})
        with self.assertRaises(ValueError):
            hd.change({"op": "x"})

    def test_due_date_follows_the_calendar(self):
        self.assertEqual(tr.due_date("2026-11"), "2026-12-31")
        self.assertEqual(tr.due_date("2026-11", {"12-31"}), "2026-12-30")
        self.assertEqual(tr.due_date("2026-11", {"12-31", "2026-12-30"}), "2026-12-29")
        self.assertEqual(tr.due_date("2026-04", {"2026-05-29"}), "2026-05-28")   # 30 e 31/05 são fim de semana
        self.assertEqual(tr.due_date("2026-11", hd.keys()), "2026-12-30")   # o padrão já traz 31/12


class DarfCalendarTest(DatabaseTestCase):
    def portfolio(self, sale):
        return {"id": "c1", "name": "T", "classes": [], "fixedIncome": [], "assets": {"IVVB11": {"class": "ETFs Brasil", "market": "b3"}},
                "transactions": [{"id": "a", "ticker": "IVVB11", "type": "compra", "date": "2020-01-02", "quantity": 10, "price": 300, "fees": 0, "fx": 1},
                                 {"id": "b", "ticker": "IVVB11", "type": "venda", "date": sale, "quantity": 10, "price": 350, "fees": 0, "fx": 1}]}

    def test_editing_the_calendar_moves_the_due_date(self):
        p = self.portfolio("2020-11-03")
        self.assertEqual(tr._ledger_for(p)[2]["months"][0]["dueDate"], "2020-12-30")
        hd.change({"op": "remove", "key": "12-31"})
        self.assertEqual(tr._ledger_for(p)[2]["months"][0]["dueDate"], "2020-12-31")

    def test_warns_only_when_a_moving_holiday_would_change_the_due_date(self):
        # Sexta-feira Santa de 2029 é 30/03, o último dia útil de março.
        months = [{"month": "2029-02", "label": "fevereiro de 2029", "status": "pending", "dueDate": tr.due_date("2029-02", hd.keys())}]
        self.assertEqual(months[0]["dueDate"], "2029-03-30")
        warning = tr._moving_warning(months, hd.keys())
        self.assertIn("fevereiro de 2029", warning["title"])
        hd.change({"op": "moving", "year": 2029})
        months[0]["dueDate"] = tr.due_date("2029-02", hd.keys())   # a apuração é refeita com a lista nova
        self.assertEqual(months[0]["dueDate"], "2029-03-29")
        self.assertIsNone(tr._moving_warning(months, hd.keys()))
        other = [{"month": "2029-03", "label": "março de 2029", "status": "pending", "dueDate": tr.due_date("2029-03", hd.keys())}]
        self.assertIsNone(tr._moving_warning(other, hd.keys()))
