# Top Purchased Items Report (`po_top_items_report`)

Odoo 16 (Community & Enterprise). Adds **Purchase › Reporting › Top Purchased Items**
without touching the standard purchase reports. Depends only on `purchase_stock`.

## What it does

| Mode | Shows |
|---|---|
| **Basic** | Top N items of a period: Item, Barcode, Purchased Qty, Purchased Amount (actual PO rates), Avg / Min / Max PO rate, No. of POs, Received Qty, Pending Qty, Sold Qty, Sale Rate, Gross Profit, GP % |
| **Comparison** | Current vs comparison period (previous period, same period last year or custom): status *New / Increase / Decrease / No change*, quantities, amounts, actual PO rates, rate change %, POs in both periods, plus a separate **Lost Products** table |

Filters: report type, dates, Top N (1–1000), rank by amount / quantity / number of POs,
companies, report currency company, warehouses, product categories (with children).

Outputs, all built from one server-side data method (`get_report_data`):

* **Web view** (OWL client action): KPI cards, sortable columns, search by name/barcode,
  click-through to the purchase lines or invoice lines behind a figure.
* **PDF** (QWeb, A4 landscape): RTL layout in Arabic.
* **Excel**: frozen headers, auto-filter, `SUBTOTAL` totals (follow the filter), RTL sheets in
  Arabic, a second sheet for lost products in comparison mode.

## Calculation rules

* **Purchased amount** = Σ PO line `price_subtotal` (actual unit price after discount, before
  tax) ÷ the PO's stored `currency_rate`, then × company→report-currency rate.
  `standard_price` is never read.
* **Purchases counted**: POs in state *Purchase Order* or *Locked*, by confirmation date
  (`date_approve`) in the user's timezone. Section/note lines excluded.
* **Quantities** in the product's base UoM (a line of 2 Dozens counts 24 Units).
* **Pending qty** = Σ per line of max(ordered − received, 0); an over-receipt on one PO does
  not hide a shortage on another.
* **Sold qty / revenue**: posted customer invoices minus posted credit notes, by accounting
  date. With a warehouse filter and the Sales app installed, only invoice lines linked to sale
  orders of those warehouses.
* **Sale rate** = revenue ÷ sold qty. **Gross profit** = revenue − sold qty × average PO rate.
* **Comparison status** compares quantities; *New* = bought in the current period only,
  *Lost* = bought in the comparison period only.

## Performance (large databases)

All aggregation is one SQL statement in PostgreSQL; Python only receives the final Top-N rows.
Measured on PostgreSQL 16, default settings (`work_mem` 4 MB, 2 CPU), with
**1,131,429 PO lines** (220k POs) and **1,800,000 journal items** (1.2M invoice product lines):

| Scenario | Naive first version | Final |
|---|---:|---:|
| Basic, 1 month, Top 20 | 0.42 s | 0.30 s |
| Basic, 12 months, Top 20 | 3.50 s | **0.94 s** |
| Basic, all data (1.1M lines), Top 20 | 5.48 s | **1.52 s** |
| Basic, all data, Top 1000 | 6.55 s | 4.11 s |
| Comparison, month vs previous month | 0.32 s | 0.31 s |
| Comparison, 10 months vs previous 12 (all data) | 2.23 s | **0.72 s** |

What made the difference:

1. Line math in double precision and no per-row joins to product/UoM tables (received quantity
   is converted with the line's own stored `product_uom_qty / product_qty` ratio).
2. Currency factors written as a parallel-safe expression so PostgreSQL uses parallel workers.
3. Distinct PO counts computed only for the Top-N products (instead of grouping every line by
   product and order).
4. A partial index for the sales lookup, which Odoo 16 does not have:
   `po_top_items_aml_product_date_idx ON account_move_line (product_id, date)
   WHERE display_type = 'product' AND parent_state = 'posted'`
   (≈ 0.7 s / 23 MB to build on 1.8M journal items; dropped on uninstall).

> On very large databases the index is built during installation and briefly blocks writes on
> `account_move_line`. Install outside business hours, or create it beforehand with
> `CREATE INDEX CONCURRENTLY` using the definition above; the module skips it if it exists.

## Security

Menu and wizard: *Purchase / Administrator*. Every run checks that the user is allowed on all
selected companies (and on the report currency company).

## Tests

```
odoo-bin -d <db> -i po_top_items_report --test-tags /po_top_items_report --stop-after-init
```

`tests/test_po_top_items_report.py` (based on `AccountTestInvoicingCommon`, needs a chart of
accounts): actual PO rates across a foreign-currency PO and a second company in another
currency, product cost ignored, UoM conversion, received/pending with over-receipt, real stock
receipt with backorder, sales net of credit notes, gross profit, totals and KPIs, Top-N and all
ranking modes, warehouse / category / company filters, timezone boundary, comparison new / lost /
rates / presets, empty periods, Excel export, PDF in English and Arabic RTL, drill-down domains,
validation and company access.

## Translations

`i18n/ar_001.po` (114 terms), `i18n/po_top_items_report.pot` for other languages.
