# Part of po_top_items_report. License OPL-1.
{
    "name": "Top Purchased Items Report",
    "version": "19.0.1.0.0",
    "category": "Inventory/Purchase",
    "summary": "Top N purchased items at actual PO rates: received / pending / "
               "sold quantities, gross profit and period comparison "
               "(web, PDF, Excel, Arabic)",
    "description": """
Top Purchased Items Report
==========================
Interactive report under Purchase > Reporting:

* Basic report: Top N items by purchased amount, quantity or number of POs,
  valued at the actual PO line rates (never product costs) with received,
  pending, sold quantity, sale rate and gross profit.
* Comparison report: current vs. comparison period with new and lost items,
  quantities, actual PO rates and number of POs for both periods.
* Web view with sorting, search and drill-down, QWeb PDF (Arabic RTL),
  formatted Excel export.
* Multi-company, multi-currency, warehouse and product category filters.
* Set-based SQL engine designed for databases with millions of lines.
""",
    "author": "Ibrahim Yahia",
    "website": "https://www.linkedin.com/in/ibrahim-yahia",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["purchase_stock"],
    "data": [
        "security/ir.model.access.csv",
        "report/report_paperformat.xml",
        "report/report_po_top_items.xml",
        "wizard/po_top_items_wizard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "po_top_items_report/static/src/scss/po_top_items_report.scss",
            "po_top_items_report/static/src/js/po_top_items_report_action.js",
            "po_top_items_report/static/src/xml/po_top_items_report_action.xml",
        ],
        "web.report_assets_common": [
            "po_top_items_report/static/src/scss/po_top_items_report_pdf.scss",
        ],
    },
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}
