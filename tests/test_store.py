"""Ações da Carteira gravando num banco temporário."""

import unittest
from unittest import mock

from marketpressure import database, portfolio_store as store
from tests.support import DatabaseTestCase


class ActionsTest(DatabaseTestCase):
    def act(self, action, **body):
        return store.apply_action({"action": action, **body})

    def new_portfolio(self):
        return self.act("criar_carteira", name="Principal")["id"]

    def buy(self, pid, ticker="PETR4", qty="100", date="2021-06-01", kind="compra"):
        return self.act("nova_operacao", id=pid, ticker=ticker, market="b3", type=kind, date=date,
                        quantity=qty, price="37,10", fees="0")

    def test_new_portfolio_starts_with_default_classes(self):
        pid = self.new_portfolio()
        p = store.get_portfolio(pid)
        self.assertEqual([c["name"] for c in p["classes"]], ["Ações", "FIIs", "Renda Fixa", "Ativos Internacionais"])

    def test_transaction_is_saved_with_suggested_class_and_backup(self):
        pid = self.new_portfolio()
        result = self.buy(pid)
        self.assertEqual(result["message"], "Compra de 100 PETR4 registrada.")
        p = store.get_portfolio(pid)
        self.assertEqual(p["assets"]["PETR4"], {"class": "Ações", "market": "b3"})
        self.assertEqual(p["transactions"][0]["price"], 37.1)
        self.assertTrue((database.BACKUP_DIR / "transactions.parquet").exists())

    def test_invalid_action_writes_nothing(self):
        pid = self.new_portfolio()
        self.buy(pid)
        before = store.read_doc()
        with self.assertRaisesRegex(store.ActionError, "excede a quantidade"):
            self.buy(pid, qty="101", date="2021-06-02", kind="venda")
        self.assertEqual(store.read_doc(), before)

    def test_class_rename_updates_assets_and_removal_requires_empty_class(self):
        pid = self.new_portfolio()
        self.buy(pid)
        self.act("renomear_classe", id=pid, previous="Ações", name="Ações BR")
        self.assertEqual(store.get_portfolio(pid)["assets"]["PETR4"]["class"], "Ações BR")
        with self.assertRaisesRegex(store.ActionError, "PETR4"):
            self.act("remover_classe", id=pid, name="Ações BR")
        self.act("nova_classe", id=pid, name="Cripto", target="2,5")
        self.assertIn({"name": "Cripto", "target": 2.5}, store.get_portfolio(pid)["classes"])

    def test_deleting_last_entry_removes_asset(self):
        pid = self.new_portfolio()
        self.buy(pid)
        entry = store.get_portfolio(pid)["transactions"][0]["id"]
        self.act("excluir_lancamento", id=pid, kind="tx", entryId=entry)
        self.assertEqual(store.get_portfolio(pid)["assets"], {})

    def test_batch_is_all_or_nothing(self):
        pid = self.new_portfolio()
        rows = [{"kind": "quote", "key": "PETR4", "market": "b3", "cls": "Ações", "include": True,
                 "date": "2021-06-01", "qty": "10", "price": "30", "fees": "0"},
                {"kind": "quote", "key": "VALE3", "market": "b3", "cls": "Ações", "include": True,
                 "date": "2021-06-01", "qty": "0", "price": "60", "fees": "0"}]
        with self.assertRaises(store.ActionError) as ctx:
            self.act("registrar_lote", id=pid, rows=rows)
        self.assertEqual(list(ctx.exception.extra["rowErrors"]), [1])
        self.assertEqual(store.get_portfolio(pid)["transactions"], [])
        rows[1]["qty"] = "5"
        self.assertEqual(self.act("registrar_lote", id=pid, rows=rows)["message"], "2 compras registradas.")

    def test_tables_round_trip(self):
        pid = self.new_portfolio()
        self.buy(pid)
        self.act("salvar_meta", id=pid, **GOAL)
        doc = store.read_doc()
        store.write_doc(doc)
        self.assertEqual(store.read_doc(), doc)

    def test_goal_is_created_edited_and_deleted(self):
        pid = self.new_portfolio()
        result = self.act("salvar_meta", id=pid, **GOAL)
        self.assertEqual(result["message"], 'Meta "Aposentadoria" criada.')
        goal = store.get_portfolio(pid)["goals"][0]
        self.assertEqual((goal["id"], goal["target"], goal["usePortfolio"], goal["adjust"], goal["targetDate"]),
                         (result["goalId"], 10000, False, True, None))
        self.assertEqual(goal["created"], store.today_iso())
        with mock.patch.object(store, "today_iso", return_value="2030-01-01"):
            self.act("salvar_meta", id=pid, goalId=goal["id"], **{**GOAL, "name": "Liberdade", "targetDate": "2050-01-01"})
        self.assertEqual([(g["name"], g["targetDate"], g["created"]) for g in store.get_portfolio(pid)["goals"]],
                         [("Liberdade", "2050-01-01", goal["created"])])  # editar não muda a data de criação
        with self.assertRaises(store.ActionError):
            self.act("salvar_meta", id=pid, **{**GOAL, "monthly": "-5"})
        self.assertEqual(len(store.get_portfolio(pid)["goals"]), 1)
        self.act("excluir_meta", id=pid, goalId=goal["id"])
        self.assertEqual(store.get_portfolio(pid)["goals"], [])


GOAL = {"name": "Aposentadoria", "kind": "renda", "target": "10000", "withdrawal": "4", "initial": "1000",
        "usePortfolio": False, "monthly": "2000", "rate": "10", "inflation": "4", "adjust": True}


if __name__ == "__main__":
    unittest.main()
