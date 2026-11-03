# Part of po_top_items_report. License OPL-1.
"""Calculation tests for the Top Purchased Items report.

Scenario (report company: company_1 in USD; Gold company: 1 USD = 2 Gold;
user timezone Asia/Riyadh, UTC+3). Current period = January 2026,
comparison period = December 2025.

=====  =========  ==========  =====================================  ========
PO     company    currency    lines                                  approved
=====  =========  ==========  =====================================  ========
PO1    company_1  USD         P1 10 @ 10 (rcv 10), P2 5 @ 20 (rcv 2)  Jan 05
PO2    company_1  Gold (x2)   P1 2 Dozens @ 180 Gold (rcv 1 Dozen)    Jan 10
PO3    gold co.   Gold        P1 5 @ 11 Gold (rcv 7 = over-receipt)   Jan 15
PO4    company_1  USD         P1 100 @ 10 - CANCELLED                 Jan 16
PO5    company_1  USD         P1 8 @ 9, P3 4 @ 50                     Dec 10
PO6    company_1  USD (WH2)   P4 3 @ 30                               Jan 20
PO7    company_1  USD         P2 1 @ 20 at 2026-01-31 22:00 UTC       Feb 01
                              (= Feb 1st 01:00 in Riyadh -> excluded)
=====  =========  ==========  =====================================  ========

Customer invoices (January): company_1 P1 6 @ 20 + 1 Dozen @ 250, credit
note P1 2 @ 20, draft invoice P1 50 (ignored), vendor bill P1 (ignored);
Gold company P2 3 @ 50 Gold.
"""
import base64

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "po_top_items_report")
class TestPoTopItemsReport(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        env = cls.env
        cls.env.user.write({
            "tz": "Asia/Riyadh",
            "groups_id": [
                Command.link(env.ref("purchase.group_purchase_manager").id),
                Command.link(env.ref("stock.group_stock_manager").id),
                Command.link(env.ref("stock.group_stock_multi_warehouses").id),
            ],
        })
        cls.company = cls.company_data["company"]
        cls.usd = cls.company.currency_id
        cls.gold = cls.currency_data["currency"]  # 1 USD = 2 Gold (2017 rate onwards)
        cls.gold_company_data = cls.setup_company_data(
            "Gold Company", currency_id=cls.gold.id)
        cls.gold_company = cls.gold_company_data["company"]
        cls.env.user.company_ids |= cls.gold_company

        cls.uom_unit = env.ref("uom.product_uom_unit")
        cls.uom_dozen = env.ref("uom.product_uom_dozen")
        cls.categ_a = env["product.category"].create({"name": "PTI Category A"})
        cls.categ_b = env["product.category"].create({"name": "PTI Category B"})

        def product(name, barcode=False, categ=cls.categ_a, ptype="service", cost=999.0):
            return env["product.product"].create({
                "name": name, "barcode": barcode, "type": ptype, "categ_id": categ.id,
                "standard_price": cost,  # must never be used by the report
                "list_price": 1.0, "purchase_method": "purchase",
                "taxes_id": [Command.clear()], "supplier_taxes_id": [Command.clear()],
            })

        cls.p1 = product("PTI Item 1", "PTI0001")
        cls.p2 = product("PTI Item 2", "PTI0002")
        cls.p3 = product("PTI Item 3")
        cls.p4 = product("PTI Item 4", categ=cls.categ_b)
        cls.vendor = cls.partner_a
        cls.customer = cls.partner_b

        cls.wh1 = env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.wh2 = env["stock.warehouse"].create({
            "name": "PTI Second WH", "code": "PTI2", "company_id": cls.company.id})

        cls.po1 = cls._po([(cls.p1, 10, 10.0, None, 10), (cls.p2, 5, 20.0, None, 2)],
                          "2026-01-05 10:00:00")
        cls.po2 = cls._po([(cls.p1, 2, 180.0, cls.uom_dozen, 1)],
                          "2026-01-10 10:00:00", currency=cls.gold)
        cls.po3 = cls._po([(cls.p1, 5, 11.0, None, 7)],
                          "2026-01-15 10:00:00", company=cls.gold_company)
        cls.po4 = cls._po([(cls.p1, 100, 10.0, None, 0)],
                          "2026-01-16 10:00:00", cancel=True)
        cls.po5 = cls._po([(cls.p1, 8, 9.0, None, 8), (cls.p3, 4, 50.0, None, 4)],
                          "2025-12-10 10:00:00")
        cls.po6 = cls._po([(cls.p4, 3, 30.0, None, 0)],
                          "2026-01-20 10:00:00", picking_type=cls.wh2.in_type_id)
        cls.po7 = cls._po([(cls.p2, 1, 20.0, None, 0)], "2026-01-31 22:00:00")

        cls._invoice("out_invoice", "2026-01-12", [(cls.p1, 6, 20.0, None), (cls.p1, 1, 250.0, cls.uom_dozen)])
        cls._invoice("out_refund", "2026-01-25", [(cls.p1, 2, 20.0, None)])
        cls._invoice("out_invoice", "2026-01-14", [(cls.p1, 50, 20.0, None)], post=False)
        cls._invoice("in_invoice", "2026-01-14", [(cls.p1, 10, 10.0, None)])
        cls._invoice("out_invoice", "2026-02-02", [(cls.p1, 9, 20.0, None)])
        cls._invoice("out_invoice", "2026-01-14", [(cls.p2, 3, 50.0, None)],
                     company_data=cls.gold_company_data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @classmethod
    def _po(cls, lines, date_approve, company=None, currency=None, picking_type=None, cancel=False):
        company = company or cls.company
        vals = {
            "partner_id": cls.vendor.id,
            "company_id": company.id,
            "currency_id": (currency or company.currency_id).id,
            "date_order": date_approve,
            "order_line": [Command.create({
                "product_id": product.id,
                "product_qty": qty,
                "product_uom": (uom or product.uom_po_id).id,
                "price_unit": price,
                "taxes_id": [Command.clear()],
            }) for product, qty, price, uom, _rcv in lines],
        }
        if not picking_type:
            picking_type = cls.env["stock.warehouse"].search(
                [("company_id", "=", company.id)], order="id", limit=1).in_type_id
        vals["picking_type_id"] = picking_type.id
        po = cls.env["purchase.order"].with_company(company).create(vals)
        po.button_confirm()
        for line, (_p, _q, _pr, _u, received) in zip(po.order_line, lines):
            if received:
                line.qty_received = received
        if cancel:
            po.button_cancel()
        po.date_approve = date_approve
        return po

    @classmethod
    def _invoice(cls, move_type, date, lines, post=True, company_data=None):
        company_data = company_data or cls.company_data
        journal = company_data["default_journal_purchase" if move_type.startswith("in_")
                               else "default_journal_sale"]
        move = cls.env["account.move"].with_company(company_data["company"]).create({
            "move_type": move_type,
            "partner_id": (cls.vendor if move_type.startswith("in_") else cls.customer).id,
            "invoice_date": date,
            "date": date,
            "journal_id": journal.id,
            "invoice_line_ids": [Command.create({
                "product_id": product.id,
                "quantity": qty,
                "price_unit": price,
                "product_uom_id": (uom or product.uom_id).id,
                "tax_ids": [Command.clear()],
            }) for product, qty, price, uom in lines],
        })
        if post:
            move.action_post()
        return move

    def _wizard(self, **vals):
        values = {
            "report_type": "basic",
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
            "limit": 20,
            "rank_by": "amount",
            "company_id": self.company.id,
            "company_ids": [Command.set((self.company | self.gold_company).ids)],
        }
        values.update(vals)
        return self.env["po.top.items.report.wizard"].create(values)

    def _lines(self, data, key="lines"):
        return {line["product_id"]: line for line in data[key]}

    # ------------------------------------------------------------------
    # Basic report
    # ------------------------------------------------------------------
    def test_01_basic_actual_po_rates_multi_currency(self):
        data = self._wizard().get_report_data()
        self.assertEqual([line["product_id"] for line in data["lines"]],
                         [self.p1.id, self.p2.id, self.p4.id],
                         "Ranked by amount; cancelled POs, December and the post-midnight PO excluded")
        p1 = self._lines(data)[self.p1.id]
        # 100 USD + 360 Gold / 2 + 55 Gold (Gold company) / 2
        self.assertAlmostEqual(p1["purchased_amount"], 100 + 180 + 27.5)
        self.assertAlmostEqual(p1["purchased_qty"], 10 + 24 + 5, msg="Dozens converted to units")
        self.assertEqual(p1["po_count"], 3)
        self.assertAlmostEqual(p1["avg_rate"], 307.5 / 39)
        # per-line rates in USD: 10, 180/24 = 7.5, 27.5/5 = 5.5
        self.assertAlmostEqual(p1["min_rate"], 5.5)
        self.assertAlmostEqual(p1["max_rate"], 10.0)
        self.assertEqual(p1["barcode"], "PTI0001")

    def test_02_product_cost_is_ignored(self):
        before = self._lines(self._wizard().get_report_data())[self.p1.id]["purchased_amount"]
        self.p1.standard_price = 123456.0
        after = self._lines(self._wizard().get_report_data())[self.p1.id]["purchased_amount"]
        self.assertAlmostEqual(before, after)

    def test_03_received_and_pending(self):
        lines = self._lines(self._wizard().get_report_data())
        p1 = lines[self.p1.id]
        self.assertAlmostEqual(p1["received_qty"], 10 + 12 + 7)
        # PO3 is over-received (7/5): it must not hide PO2's 12 missing units
        self.assertAlmostEqual(p1["pending_qty"], 12)
        self.assertAlmostEqual(lines[self.p2.id]["pending_qty"], 3)
        self.assertAlmostEqual(lines[self.p4.id]["pending_qty"], 3)

    def test_04_sales_and_gross_profit(self):
        lines = self._lines(self._wizard().get_report_data())
        p1 = lines[self.p1.id]
        # 6 + 12 (1 Dozen) - 2 (credit note); draft / vendor bill / February ignored
        self.assertAlmostEqual(p1["sold_qty"], 16)
        self.assertAlmostEqual(p1["revenue"], 120 + 250 - 40)
        self.assertAlmostEqual(p1["sale_rate"], 330 / 16)
        self.assertAlmostEqual(p1["gross_profit"], 330 - 16 * (307.5 / 39))
        p2 = lines[self.p2.id]
        self.assertAlmostEqual(p2["sold_qty"], 3)
        self.assertAlmostEqual(p2["revenue"], 75.0, msg="150 Gold -> 75 USD")
        self.assertAlmostEqual(p2["gross_profit"], 75 - 3 * 20)
        self.assertEqual(lines[self.p4.id]["sold_qty"], 0)
        self.assertIsNone(lines[self.p4.id]["gp_margin"])

    def test_05_totals_and_kpis(self):
        data = self._wizard().get_report_data()
        totals = data["totals"]
        self.assertAlmostEqual(totals["purchased_amount"], 307.5 + 100 + 90)
        self.assertEqual(totals["po_count"], 3 + 1 + 1)
        self.assertEqual(data["kpis"][0]["value"], 3, "Items purchased in the period")
        self.assertAlmostEqual(data["kpis"][1]["value"], 497.5, msg="Total purchases")
        self.assertAlmostEqual(data["kpis"][2]["value"], 100.0, msg="Top 3 = 100% of purchases")

    def test_06_limit_and_ranking(self):
        data = self._wizard(limit=1, rank_by="qty").get_report_data()
        self.assertEqual([line["product_id"] for line in data["lines"]], [self.p1.id])
        self.assertEqual(data["kpis"][0]["value"], 3, "Global KPIs ignore the Top-N limit")
        data = self._wizard(rank_by="po_count").get_report_data()
        self.assertEqual(data["lines"][0]["product_id"], self.p1.id)
        self.assertEqual(data["lines"][0]["po_count"], 3)

    def test_07_warehouse_category_company_filters(self):
        lines = self._lines(self._wizard(warehouse_ids=[Command.set(self.wh1.ids)]).get_report_data())
        self.assertEqual(set(lines), {self.p1.id, self.p2.id})
        self.assertAlmostEqual(lines[self.p1.id]["purchased_amount"], 100 + 180)
        lines = self._lines(self._wizard(categ_ids=[Command.set(self.categ_b.ids)]).get_report_data())
        self.assertEqual(set(lines), {self.p4.id})
        data = self._wizard(company_id=self.gold_company.id,
                            company_ids=[Command.set(self.gold_company.ids)]).get_report_data()
        p1 = self._lines(data)[self.p1.id]
        self.assertAlmostEqual(p1["purchased_amount"], 55.0, msg="Reported in Gold")
        self.assertEqual(data["currency"]["id"], self.gold.id)

    def test_08_timezone_boundary(self):
        # PO7 is approved 2026-01-31 22:00 UTC = 2026-02-01 01:00 in Riyadh.
        feb = self._lines(self._wizard(date_from="2026-02-01", date_to="2026-02-28").get_report_data())
        self.assertIn(self.p2.id, feb)
        self.env.user.tz = "UTC"
        jan = self._lines(self._wizard().get_report_data())
        self.assertAlmostEqual(jan[self.p2.id]["purchased_qty"], 6, msg="In UTC, PO7 is in January")

    def test_09_real_stock_receipt_in_dozens(self):
        storable = self.env["product.product"].create({
            "name": "PTI Storable", "type": "product", "categ_id": self.categ_a.id})
        po = self._po([(storable, 2, 120.0, self.uom_dozen, 0)], "2026-01-21 10:00:00")
        picking = po.picking_ids
        self.assertTrue(picking)
        picking.move_ids.quantity_done = 1  # 1 Dozen out of 2
        res = picking.button_validate()
        Form(self.env[res["res_model"]].with_context(res["context"])).save().process()
        line = self._lines(self._wizard().get_report_data())[storable.id]
        self.assertAlmostEqual(line["purchased_qty"], 24)
        self.assertAlmostEqual(line["received_qty"], 12)
        self.assertAlmostEqual(line["pending_qty"], 12)
        self.assertAlmostEqual(line["purchased_amount"], 240)

    # ------------------------------------------------------------------
    # Comparison report
    # ------------------------------------------------------------------
    def test_10_comparison_new_lost_and_rates(self):
        wiz = self._wizard(report_type="comparison", compare_preset="custom",
                           compare_date_from="2025-12-01", compare_date_to="2025-12-31")
        data = wiz.get_report_data()
        main = self._lines(data)
        lost = self._lines(data, "lost_lines")
        self.assertEqual(list(main), [self.p1.id, self.p2.id, self.p4.id])
        self.assertEqual(list(lost), [self.p3.id])
        p1 = main[self.p1.id]
        self.assertEqual(p1["status"], "up")
        self.assertAlmostEqual(p1["qty_b"], 8)
        self.assertAlmostEqual(p1["rate_b"], 9.0)
        self.assertEqual((p1["po_a"], p1["po_b"]), (3, 1))
        self.assertAlmostEqual(p1["qty_diff_pct"], (39 - 8) / 8 * 100)
        self.assertAlmostEqual(p1["rate_diff_pct"], ((307.5 / 39) - 9) / 9 * 100)
        self.assertEqual(main[self.p2.id]["status"], "new")
        self.assertEqual(main[self.p4.id]["status"], "new")
        self.assertAlmostEqual(lost[self.p3.id]["amount_b"], 200)
        self.assertEqual(lost[self.p3.id]["po_b"], 1)
        values = [k["value"] for k in data["kpis"]]
        self.assertEqual(values[:4], [3, 2, 2, 1], "items current/comparison, new, lost")
        self.assertAlmostEqual(values[4], 497.5)
        self.assertAlmostEqual(values[5], 272.0)

    def test_11_comparison_presets(self):
        with Form(self.env["po.top.items.report.wizard"]) as f:
            f.report_type = "comparison"
            f.date_from = fields.Date.to_date("2026-01-01")
            f.date_to = fields.Date.to_date("2026-01-31")
            f.compare_preset = "previous_period"
            self.assertEqual(str(f.compare_date_from), "2025-12-01")
            self.assertEqual(str(f.compare_date_to), "2025-12-31")
            f.compare_preset = "previous_year"
            self.assertEqual(str(f.compare_date_from), "2025-01-01")
            self.assertEqual(str(f.compare_date_to), "2025-01-31")

    def test_12_comparison_ranked_by_po_count(self):
        data = self._wizard(report_type="comparison", rank_by="po_count", compare_preset="custom",
                            compare_date_from="2025-12-01",
                            compare_date_to="2025-12-31").get_report_data()
        self.assertEqual(data["lines"][0]["product_id"], self.p1.id)
        self.assertEqual([line["product_id"] for line in data["lost_lines"]], [self.p3.id])

    def test_13_empty_periods(self):
        data = self._wizard(date_from="2020-01-01", date_to="2020-01-31").get_report_data()
        self.assertEqual(data["lines"], [])
        data = self._wizard(report_type="comparison", date_from="2020-01-01", date_to="2020-01-31",
                            compare_preset="custom", compare_date_from="2019-01-01",
                            compare_date_to="2019-01-31").get_report_data()
        self.assertEqual((data["lines"], data["lost_lines"]), ([], []))

    # ------------------------------------------------------------------
    # Outputs, validation, security
    # ------------------------------------------------------------------
    def test_20_xlsx_export(self):
        for report_type in ("basic", "comparison"):
            wiz = self._wizard(report_type=report_type, compare_preset="custom",
                               compare_date_from="2025-12-01", compare_date_to="2025-12-31")
            action = wiz.action_export_xlsx()
            self.assertEqual(action["type"], "ir.actions.act_url")
            self.assertTrue(wiz.xlsx_filename.endswith(".xlsx"))
            self.assertEqual(base64.b64decode(wiz.xlsx_file)[:2], b"PK")

    def test_21_pdf_html_and_rtl(self):
        report = self.env.ref("po_top_items_report.action_report_po_top_items")
        wiz = self._wizard()
        html = report._render_qweb_html(report.report_name, wiz.ids)[0].decode()
        self.assertIn("PTI Item 1", html)
        self.assertIn("PTI0001", html)
        self.env["res.lang"]._activate_lang("ar_001")
        html_ar = report.with_context(lang="ar_001")._render_qweb_html(
            report.report_name, wiz.ids)[0].decode()
        self.assertIn('dir="rtl"', html_ar)
        wiz_cmp = self._wizard(report_type="comparison", compare_preset="custom",
                               compare_date_from="2025-12-01", compare_date_to="2025-12-31")
        html_cmp = report._render_qweb_html(report.report_name, wiz_cmp.ids)[0].decode()
        self.assertIn("PTI Item 3", html_cmp, "Lost product listed")

    def test_22_web_view_and_drilldown_actions(self):
        both = (self.company | self.gold_company).ids
        self.env = self.env(context=dict(self.env.context, allowed_company_ids=both))
        wiz = self._wizard()
        action = wiz.action_view()
        self.assertEqual(action["tag"], "po_top_items_report.report_view")
        drill = wiz.action_drilldown(self.p1.id, "po", "a")
        lines = self.env["purchase.order.line"].search(drill["domain"])
        self.assertEqual(lines.order_id, self.po1 | self.po2 | self.po3)
        drill = wiz.action_drilldown(self.p1.id, "sale", "a")
        amls = self.env["account.move.line"].search(drill["domain"])
        self.assertEqual(len(amls), 3)
        self.assertTrue(wiz.action_print_pdf()["report_name"].endswith("report_po_top_items"))

    def test_23_validation(self):
        with self.assertRaises(ValidationError):
            self._wizard(date_from="2026-02-01", date_to="2026-01-01")
        with self.assertRaises(ValidationError):
            self._wizard(limit=0)
        with self.assertRaises(ValidationError):
            self._wizard(limit=1001)
        with self.assertRaises(ValidationError):
            self._wizard(report_type="comparison", compare_preset="custom",
                         compare_date_from=False, compare_date_to=False)

    def test_24_company_access(self):
        other = self.env["res.company"].sudo().create({"name": "PTI Foreign Company"})
        wiz = self._wizard(company_ids=[Command.set((self.company | other).ids)])
        with self.assertRaises(AccessError):
            wiz.get_report_data()
        wiz = self._wizard(company_id=other.id)
        with self.assertRaises(AccessError, msg="Report currency company must be allowed too"):
            wiz.get_report_data()

    def test_25_sales_index_exists(self):
        self.env.cr.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'po_top_items_aml_product_date_idx'")
        self.assertTrue(self.env.cr.fetchone())
