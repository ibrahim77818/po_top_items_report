# Part of po_top_items_report. License OPL-1.
import base64
import logging
from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date, format_datetime

from ..tools import report_engine
from ..tools.xlsx_builder import build_xlsx

_logger = logging.getLogger(__name__)

MAX_LIMIT = 1000


class PoTopItemsReportWizard(models.TransientModel):
    _name = "po.top.items.report.wizard"
    _description = "Top Purchased Items Report"

    report_type = fields.Selection(
        [("basic", "Basic"), ("comparison", "Comparison")],
        string="Report Type", required=True, default="basic")
    date_from = fields.Date(
        string="From", required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(
        string="To", required=True,
        default=lambda self: fields.Date.context_today(self))
    compare_preset = fields.Selection(
        [("previous_period", "Previous Period"),
         ("previous_year", "Same Period Last Year"),
         ("custom", "Custom")],
        string="Compare With", default="previous_period")
    compare_date_from = fields.Date(string="Comparison From")
    compare_date_to = fields.Date(string="Comparison To")
    limit = fields.Integer(
        string="Number of Items", required=True, default=20,
        help="Number of items to show (Top N). Maximum %s." % MAX_LIMIT)
    rank_by = fields.Selection(
        [("amount", "Purchased Amount"),
         ("qty", "Purchased Quantity"),
         ("po_count", "Number of POs")],
        string="Rank By", required=True, default="amount")
    company_id = fields.Many2one(
        "res.company", string="Report Company", required=True,
        default=lambda self: self.env.company,
        help="Amounts of all selected companies are converted to this "
             "company's currency.")
    currency_id = fields.Many2one(related="company_id.currency_id")
    company_ids = fields.Many2many(
        "res.company", string="Companies", required=True,
        default=lambda self: self.env.companies,
        domain=lambda self: [("id", "in", self.env.user.company_ids.ids)])
    warehouse_ids = fields.Many2many(
        "stock.warehouse", string="Warehouses",
        domain="[('company_id', 'in', company_ids)]",
        help="Purchases: POs delivered to these warehouses. Sales: customer "
             "invoice lines linked to sale orders of these warehouses "
             "(requires the Sales app). Leave empty for all warehouses.")
    categ_ids = fields.Many2many(
        "product.category", string="Product Categories",
        help="Includes sub-categories. Leave empty for all products.")
    xlsx_file = fields.Binary(string="Excel File", attachment=False, readonly=True)
    xlsx_filename = fields.Char(readonly=True)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    @api.onchange("date_from", "date_to", "compare_preset", "report_type")
    def _onchange_compare_dates(self):
        for wiz in self:
            if wiz.report_type != "comparison" or not wiz.date_from or not wiz.date_to:
                continue
            if wiz.compare_preset == "previous_period":
                days = (wiz.date_to - wiz.date_from).days + 1
                wiz.compare_date_to = wiz.date_from - timedelta(days=1)
                wiz.compare_date_from = wiz.date_from - timedelta(days=days)
            elif wiz.compare_preset == "previous_year":
                wiz.compare_date_from = wiz.date_from - relativedelta(years=1)
                wiz.compare_date_to = wiz.date_to - relativedelta(years=1)

    @api.onchange("company_ids")
    def _onchange_company_ids(self):
        company_ids = self.company_ids._origin.ids
        self.warehouse_ids = self.warehouse_ids.filtered(
            lambda w: w.company_id._origin.id in company_ids)

    @api.constrains("date_from", "date_to", "compare_date_from", "compare_date_to",
                    "report_type", "limit")
    def _check_parameters(self):
        for wiz in self:
            if wiz.date_from > wiz.date_to:
                raise ValidationError(_("The start date must be before the end date."))
            if not 1 <= wiz.limit <= MAX_LIMIT:
                raise ValidationError(
                    _("The number of items must be between 1 and %s.", MAX_LIMIT))
            if wiz.report_type == "comparison":
                if not wiz.compare_date_from or not wiz.compare_date_to:
                    raise ValidationError(_("Please set the comparison period."))
                if wiz.compare_date_from > wiz.compare_date_to:
                    raise ValidationError(
                        _("The comparison start date must be before its end date."))

    # ------------------------------------------------------------------
    # Engine parameters
    # ------------------------------------------------------------------
    def _check_company_access(self):
        self.ensure_one()
        forbidden = (self.company_ids | self.company_id) - self.env.user.company_ids
        if forbidden:
            raise AccessError(_("You are not allowed to report on: %s",
                                ", ".join(forbidden.mapped("name"))))
        if not self.company_ids:
            raise UserError(_("Please select at least one company."))

    def _to_utc(self, day):
        """Local midnight of ``day`` (user timezone) as a naive UTC datetime,
        matching how Odoo stores ``purchase_order.date_approve``."""
        tz = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        local = tz.localize(datetime.combine(day, time.min))
        return local.astimezone(pytz.UTC).replace(tzinfo=None)

    def _period_bounds(self, date_from, date_to):
        return self._to_utc(date_from), self._to_utc(date_to + timedelta(days=1))

    def _sale_warehouse_filter_available(self):
        return "sale.order" in self.env and "warehouse_id" in self.env["sale.order"]._fields

    def _get_picking_types(self):
        if not self.warehouse_ids:
            return self.env["stock.picking.type"]
        return self.env["stock.picking.type"].with_context(active_test=False).search(
            [("warehouse_id", "in", self.warehouse_ids.ids)])

    def _get_engine_params(self):
        self.ensure_one()
        self._check_company_access()
        report_currency = self.currency_id
        rate_date = min(self.date_to, fields.Date.context_today(self))
        factors = [
            company.currency_id._get_conversion_rate(
                company.currency_id, report_currency, self.company_id, rate_date)
            for company in self.company_ids
        ]
        dt_from, dt_to = self._period_bounds(self.date_from, self.date_to)
        params = {
            "company_ids": self.company_ids.ids,
            "fx_company_ids": self.company_ids.ids,
            "fx_factors": factors,
            "dt_from": dt_from,
            "dt_to": dt_to,
            "sale_date_from": self.date_from,
            "sale_date_to": self.date_to,
            "limit": self.limit,
            "rank_by": self.rank_by,
        }
        if self.warehouse_ids:
            # An empty id list would mean "no warehouse matches", not "all".
            params["picking_type_ids"] = self._get_picking_types().ids or [0]
            if self._sale_warehouse_filter_available():
                params["sale_warehouse_ids"] = self.warehouse_ids.ids
        if self.categ_ids:
            params["categ_ids"] = self.env["product.category"].search(
                [("id", "child_of", self.categ_ids.ids)]).ids
        if self.report_type == "comparison":
            params["cmp_dt_from"], params["cmp_dt_to"] = self._period_bounds(
                self.compare_date_from, self.compare_date_to)
        return report_engine.prepare_params(params)

    def _fetch_rows(self):
        params = self._get_engine_params()
        if self.report_type == "comparison":
            query = report_engine.build_comparison_query(params)
        else:
            query = report_engine.build_basic_query(params)
        # Raw SQL: push pending ORM writes first.
        self.env.flush_all()
        self.env.cr.execute(query, params)
        return self.env.cr.dictfetchall()

    # ------------------------------------------------------------------
    # Report data (single source for web, PDF and Excel)
    # ------------------------------------------------------------------
    def _product_info(self, product_ids):
        products = self.env["product.product"].with_context(active_test=False).browse(product_ids)
        return {
            p.id: {
                "name": p.display_name,
                "name_lines": self._wrap_text(p.display_name),
                "barcode": p.barcode or "",
                "uom": p.uom_id.name,
            }
            for p in products
        }

    def _get_labels(self):
        return {
            "rank": _("#"),
            "item": _("Item"),
            "barcode": _("Barcode"),
            "uom": _("UoM"),
            "purchased_qty": _("Purchased Qty"),
            "purchased_amount": _("Purchased Amount"),
            "avg_rate": _("Avg PO Rate"),
            "min_rate": _("Min PO Rate"),
            "max_rate": _("Max PO Rate"),
            "po_count": _("No. of POs"),
            "received_qty": _("Received Qty"),
            "pending_qty": _("Pending Qty"),
            "sold_qty": _("Sold Qty"),
            "sale_rate": _("Sale Rate"),
            "gross_profit": _("Gross Profit"),
            "gp_margin": _("GP %"),
            "qty_a": _("Qty (Current)"),
            "qty_b": _("Qty (Comparison)"),
            "qty_diff": _("Qty Change"),
            "qty_diff_pct": _("Qty Change %"),
            "amount_a": _("Amount (Current)"),
            "amount_b": _("Amount (Comparison)"),
            "rate_a": _("PO Rate (Current)"),
            "rate_b": _("PO Rate (Comparison)"),
            "rate_diff_pct": _("Rate Change %"),
            "po_a": _("POs (Current)"),
            "po_b": _("POs (Comparison)"),
            "status": _("Status"),
            "total": _("Total"),
            "lost_title": _("Lost Products"),
            "lost_help": _("Purchased in the comparison period but not in the current period."),
            "main_title_basic": _("Top Purchased Items"),
            "main_title_comparison": _("Top Purchased Items - Period Comparison"),
            "status_new": _("New"),
            "status_up": _("Increase"),
            "status_down": _("Decrease"),
            "status_same": _("No Change"),
            "status_lost": _("Lost"),
            "no_data": _("No confirmed purchase order matches these filters."),
            "generated": _("Generated on"),
            "filters": _("Filters"),
            "sheet_main": _("Top Items"),
            "sheet_lost": _("Lost Products"),
        }

    @staticmethod
    def _split_label(label, width=14):
        """Split a column header into at most two lines for the PDF.

        wkhtmltopdf (QtWebKit) does not wrap Arabic text inside table
        headers, so lines are broken explicitly: before a parenthesis, or at
        the space closest to the middle of a long label."""
        if " (" in label:
            head, _sep, tail = label.partition(" (")
            return [head, "(" + tail]
        if len(label) <= width or " " not in label:
            return [label]
        middle = len(label) // 2
        cut = min((i for i, c in enumerate(label) if c == " "), key=lambda i: abs(i - middle))
        return [label[:cut], label[cut + 1:]]

    @staticmethod
    def _wrap_text(text, width=30):
        """Greedy word wrap for product names in the PDF (wkhtmltopdf does
        not wrap Arabic text inside table cells)."""
        lines, current = [], ""
        for word in (text or "").split():
            if current and len(current) + 1 + len(word) > width:
                lines.append(current)
                current = word
            else:
                current = (current + " " + word).strip()
        if current:
            lines.append(current)
        return lines or [""]

    def _status(self, row):
        if not row["po_count_b"]:
            return "new"
        diff = (row["qty_a"] or 0.0) - (row["qty_b"] or 0.0)
        rounding = 10 ** -self.env["decimal.precision"].precision_get("Product Unit of Measure")
        if abs(diff) < rounding:
            return "same"
        return "up" if diff > 0 else "down"

    @staticmethod
    def _pct(new, old):
        if not old:
            return None
        return (new - old) / abs(old) * 100.0

    def _get_filters_summary(self, labels):
        lang = self.env.lang
        res = [{
            "label": _("Period"),
            "value": "%s - %s" % (format_date(self.env, self.date_from, lang_code=lang),
                                  format_date(self.env, self.date_to, lang_code=lang)),
        }]
        if self.report_type == "comparison":
            res.append({
                "label": _("Comparison Period"),
                "value": "%s - %s" % (format_date(self.env, self.compare_date_from, lang_code=lang),
                                      format_date(self.env, self.compare_date_to, lang_code=lang)),
            })
        rank_label = dict(self._fields["rank_by"]._description_selection(self.env))[self.rank_by]
        res += [
            {"label": _("Top"), "value": "%s (%s)" % (self.limit, rank_label)},
            {"label": _("Companies"), "value": ", ".join(self.company_ids.mapped("name"))},
            {"label": _("Warehouses"),
             "value": ", ".join(self.warehouse_ids.mapped("name")) or _("All")},
        ]
        if self.categ_ids:
            res.append({"label": _("Product Categories"),
                        "value": ", ".join(self.categ_ids.mapped("display_name"))})
        res.append({"label": _("Currency"), "value": self.currency_id.name})
        return res

    def get_report_data(self):
        """Everything the three renderers need, as plain JSON-able values."""
        self.ensure_one()
        rows = self._fetch_rows()
        labels = self._get_labels()
        currency = self.currency_id
        lang = self.env["res.lang"]._lang_get(self.env.lang or "en_US")
        data = {
            "wizard_id": self.id,
            "report_type": self.report_type,
            "title": labels["main_title_comparison" if self.report_type == "comparison"
                            else "main_title_basic"],
            "labels": labels,
            "header_lines": {key: self._split_label(value) for key, value in labels.items()},
            "filters": self._get_filters_summary(labels),
            "company_name": self.company_id.name,
            "currency": {
                "id": currency.id,
                "name": currency.name,
                "symbol": currency.symbol,
                "position": currency.position,
                "decimal_places": currency.decimal_places,
            },
            "qty_digits": self.env["decimal.precision"].precision_get("Product Unit of Measure"),
            "direction": lang.direction or "ltr",
            "generated_at": format_datetime(self.env, fields.Datetime.now()),
            "lines": [],
            "lost_lines": [],
            "kpis": [],
            "totals": {},
        }
        if self.report_type == "comparison":
            self._fill_comparison(data, rows)
        else:
            self._fill_basic(data, rows)
        return data

    def _fill_basic(self, data, rows):
        info = self._product_info([r["product_id"] for r in rows])
        totals = dict.fromkeys(
            ("purchased_qty", "purchased_amount", "po_count", "received_qty",
             "pending_qty", "sold_qty", "revenue", "gross_profit"), 0.0)
        for rank, r in enumerate(rows, start=1):
            qty = r["qty"] or 0.0
            amount = r["amount"] or 0.0
            avg_rate = r["avg_rate"] or 0.0
            sold = r["sold_qty"] or 0.0
            revenue = r["revenue"] or 0.0
            gross_profit = revenue - sold * avg_rate
            line = {
                "rank": rank,
                "product_id": r["product_id"],
                **info[r["product_id"]],
                "purchased_qty": qty,
                "purchased_amount": amount,
                "avg_rate": avg_rate,
                "min_rate": r["min_rate"] or 0.0,
                "max_rate": r["max_rate"] or 0.0,
                "po_count": r["po_count"] or 0,
                "received_qty": r["rcv_qty"] or 0.0,
                "pending_qty": r["pending_qty"] or 0.0,
                "sold_qty": sold,
                "sale_rate": revenue / sold if sold else 0.0,
                "revenue": revenue,
                "gross_profit": gross_profit,
                "gp_margin": gross_profit / revenue * 100.0 if revenue else None,
            }
            data["lines"].append(line)
            for key in totals:
                totals[key] += line[key]
        totals["gp_margin"] = (totals["gross_profit"] / totals["revenue"] * 100.0
                               if totals["revenue"] else None)
        data["totals"] = totals
        if rows:
            all_amount = rows[0]["all_amount"] or 0.0
            data["kpis"] = [
                {"label": _("Items Purchased"), "value": rows[0]["all_items"], "type": "int"},
                {"label": _("Total Purchases"), "value": all_amount, "type": "amount"},
                {"label": _("Top %s Share", len(rows)),
                 "value": totals["purchased_amount"] / all_amount * 100.0 if all_amount else 0.0,
                 "type": "pct"},
                {"label": _("Gross Profit (Top Items)"), "value": totals["gross_profit"],
                 "type": "amount"},
            ]

    def _fill_comparison(self, data, rows):
        labels = data["labels"]
        products = [r for r in rows if r["section"]]
        info = self._product_info([r["product_id"] for r in products])
        totals = dict.fromkeys(
            ("qty_a", "qty_b", "amount_a", "amount_b", "po_a", "po_b"), 0.0)
        lost_totals = dict.fromkeys(("qty_b", "amount_b", "po_b"), 0.0)
        rank = 0
        for r in products:
            base = {"product_id": r["product_id"], **info[r["product_id"]]}
            if r["section"] == "lost":
                line = dict(base, rank=len(data["lost_lines"]) + 1,
                            qty_b=r["qty_b"] or 0.0, amount_b=r["amount_b"] or 0.0,
                            rate_b=r["rate_b"] or 0.0, po_b=r["po_count_b"] or 0,
                            status="lost", status_label=labels["status_lost"])
                data["lost_lines"].append(line)
                for key in lost_totals:
                    lost_totals[key] += line[key]
                continue
            rank += 1
            status = self._status(r)
            qty_a, qty_b = r["qty_a"] or 0.0, r["qty_b"] or 0.0
            rate_a = r["rate_a"] or 0.0
            # no purchase in the comparison period: no rate (not a zero price)
            rate_b = r["rate_b"] if r["po_count_b"] else None
            line = dict(
                base, rank=rank,
                qty_a=qty_a, qty_b=qty_b, qty_diff=qty_a - qty_b,
                qty_diff_pct=self._pct(qty_a, qty_b),
                amount_a=r["amount_a"] or 0.0, amount_b=r["amount_b"] or 0.0,
                rate_a=rate_a, rate_b=rate_b,
                rate_diff_pct=self._pct(rate_a, rate_b) if rate_b else None,
                po_a=r["po_count_a"] or 0, po_b=r["po_count_b"] or 0,
                status=status, status_label=labels["status_" + status],
            )
            data["lines"].append(line)
            for key in totals:
                totals[key] += line[key]
        totals["qty_diff"] = totals["qty_a"] - totals["qty_b"]
        totals["qty_diff_pct"] = self._pct(totals["qty_a"], totals["qty_b"])
        data["totals"] = totals
        data["lost_totals"] = lost_totals
        if rows:
            s = rows[0]
            data["kpis"] = [
                {"label": _("Items (Current)"), "value": s["items_a"], "type": "int"},
                {"label": _("Items (Comparison)"), "value": s["items_b"], "type": "int"},
                {"label": _("New Products"), "value": s["new_items"], "type": "int",
                 "tone": "success"},
                {"label": _("Lost Products"), "value": s["lost_items"], "type": "int",
                 "tone": "danger"},
                {"label": _("Purchases (Current)"), "value": s["all_amount_a"], "type": "amount"},
                {"label": _("Purchases (Comparison)"), "value": s["all_amount_b"],
                 "type": "amount"},
                {"label": _("Change %"),
                 "value": self._pct(s["all_amount_a"] or 0.0, s["all_amount_b"] or 0.0),
                 "type": "pct"},
            ]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_view(self):
        self.ensure_one()
        self._check_company_access()
        return {
            "type": "ir.actions.client",
            "tag": "po_top_items_report.report_view",
            "name": _("Top Purchased Items"),
            "target": "current",
            "params": {"wizard_id": self.id},
            "context": {"active_id": self.id},
        }

    def action_print_pdf(self):
        self.ensure_one()
        self._check_company_access()
        return self.env.ref("po_top_items_report.action_report_po_top_items").report_action(self)

    def action_export_xlsx(self):
        self.ensure_one()
        data = self.get_report_data()
        content = build_xlsx(data)
        filename = "%s_%s_%s.xlsx" % (
            "top_purchased_items" if self.report_type == "basic" else "top_items_comparison",
            self.date_from, self.date_to)
        self.write({"xlsx_file": base64.b64encode(content), "xlsx_filename": filename})
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s/%s/xlsx_file/%s?download=true" % (
                self._name, self.id, filename),
            "target": "self",
        }

    def action_open_filters(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Top Purchased Items"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(self.env.ref("po_top_items_report.view_po_top_items_wizard_form").id, "form")],
            "target": "new",
        }

    def action_drilldown(self, product_id, kind="po", period="a"):
        """Open the documents behind a figure of the web view."""
        self.ensure_one()
        self._check_company_access()
        product = self.env["product.product"].browse(int(product_id)).exists()
        if not product:
            raise UserError(_("This product no longer exists."))
        if period == "b" and self.report_type == "comparison":
            date_from, date_to = self.compare_date_from, self.compare_date_to
        else:
            date_from, date_to = self.date_from, self.date_to
        if kind == "sale":
            domain = [
                ("product_id", "=", product.id),
                ("display_type", "=", "product"),
                ("parent_state", "=", "posted"),
                ("move_id.move_type", "in", ("out_invoice", "out_refund")),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("company_id", "in", self.company_ids.ids),
            ]
            if self.warehouse_ids and self._sale_warehouse_filter_available():
                domain.append(("sale_line_ids.order_id.warehouse_id", "in", self.warehouse_ids.ids))
            return {
                "type": "ir.actions.act_window",
                "name": _("Sales of %s", product.display_name),
                "res_model": "account.move.line",
                "view_mode": "tree,form",
                "views": [(self.env.ref("account.view_move_line_tree").id, "list"), (False, "form")],
                "domain": domain,
                "context": {"create": False},
                "target": "current",
            }
        dt_from, dt_to = self._period_bounds(date_from, date_to)
        domain = [
            ("product_id", "=", product.id),
            ("display_type", "=", False),
            ("order_id.state", "in", list(report_engine.PO_STATES)),
            ("order_id.date_approve", ">=", fields.Datetime.to_string(dt_from)),
            ("order_id.date_approve", "<", fields.Datetime.to_string(dt_to)),
            ("order_id.company_id", "in", self.company_ids.ids),
        ]
        if self.warehouse_ids:
            domain.append(("order_id.picking_type_id", "in", self._get_picking_types().ids))
        return {
            "type": "ir.actions.act_window",
            "name": _("Purchases of %s", product.display_name),
            "res_model": "purchase.order.line",
            "view_mode": "tree,form",
            "views": [(self.env.ref("purchase.purchase_order_line_tree").id, "list"),
                      (False, "form")],
            "domain": domain,
            "context": {"create": False},
            "target": "current",
        }
