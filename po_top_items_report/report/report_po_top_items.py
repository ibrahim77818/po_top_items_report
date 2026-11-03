# Part of po_top_items_report. License OPL-1.
from odoo import api, models


class ReportPoTopItems(models.AbstractModel):
    _name = "report.po_top_items_report.report_po_top_items"
    _description = "Top Purchased Items PDF Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env["po.top.items.report.wizard"].browse(docids)
        return {
            "doc_ids": docs.ids,
            "doc_model": "po.top.items.report.wizard",
            "docs": docs,
            "report_data": {doc.id: doc.get_report_data() for doc in docs},
        }
