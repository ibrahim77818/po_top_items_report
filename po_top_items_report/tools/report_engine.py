# Part of po_top_items_report. License OPL-1.
"""Set-based SQL engine for the Top Purchased Items report.

Design notes (large databases, 1M+ purchase / invoice lines):

* Every figure is aggregated inside PostgreSQL in a single statement; Python
  only receives the final Top-N rows (no ORM loop over purchase lines).
* Amounts use each PO line's own ``price_subtotal`` (actual, discounted,
  untaxed rate) converted with the PO's stored ``currency_rate`` - the rate
  Odoo itself stored on the order - and a per-company factor so several
  companies with different currencies roll up into one report currency.
  Product costs (``standard_price``) are never read.
* Quantities are expressed in the product's UoM: ``product_uom_qty`` is
  stored that way by Odoo; the received quantity (stored in the line UoM) is
  converted with the line's own ``product_uom_qty / product_qty`` ratio, which
  avoids joining product / template / UoM tables for every line.
* Pending quantity is computed per line (``GREATEST(ordered - received, 0)``)
  so an over-receipt on one PO never hides a shortage on another.
* Ranking needs only per-product sums, so all lines are aggregated in one
  hash aggregate keyed by product (a few thousand groups). The distinct
  "Number of POs" - which would otherwise need a (product, order) grouping
  of every line - is then counted for the Top-N products only, through the
  standard ``purchase_order_line.product_id`` index. Ranking *by* number of
  POs falls back to a two-level aggregate over all lines.
* Line-level arithmetic is done in double precision (as Odoo's own purchase
  analysis does) and rounded only when presented.
* Sales are only looked up for the Top-N products already selected, through
  Odoo's own ``account_move_line.product_id`` index (17.0+) (the v16 version of this
  module had to add its own index for this).

The module is deliberately free of ORM imports so it can be unit-tested
directly against PostgreSQL.
"""

RANK_COLUMNS = ("amount", "qty", "po_count")
# Odoo 19: the "done" (Locked) state was replaced by the ``locked`` flag on
# orders in state "purchase", so confirmed and locked orders are both "purchase".
PO_STATES = ("purchase",)
# Index created by the v16 version of this module; dropped on uninstall
# for databases migrated from 16.0.
SALES_INDEX_NAME = "po_top_items_aml_product_date_idx"



def _fx(company_col):
    """Factor converting an amount in the company currency of ``company_col``
    into the report currency. Written as a plain, parallel-safe expression
    (a CTE or VALUES list here would prevent PostgreSQL parallel workers)."""
    return (
        f"(%(fx_factors)s::float8[])"
        f"[array_position(%(fx_company_ids)s::int[], {company_col})]"
    )


def _rank(params):
    rank = params.get("rank_by") or "amount"
    if rank not in RANK_COLUMNS:
        raise ValueError("Invalid ranking column %r" % rank)
    return rank


def _po_lines_cte(params, period_sql, extra_select="", name="lines", only_from=None):
    """Confirmed PO lines in report currency and product base UoM.

    ``only_from``: name of a CTE whose ``product_id`` column restricts the
    lines (used to count POs of the Top-N products only)."""
    joins, filters = "", ""
    if only_from:
        filters += f"\n       AND pol.product_id IN (SELECT product_id FROM {only_from})"
    if params.get("picking_type_ids"):
        filters += "\n       AND po.picking_type_id = ANY(%(picking_type_ids)s)"
    if params.get("categ_ids"):
        joins += (
            "\n      JOIN product_product pp ON pp.id = pol.product_id"
            "\n      JOIN product_template pt ON pt.id = pp.product_tmpl_id"
        )
        filters += "\n       AND pt.categ_id = ANY(%(categ_ids)s)"
    if params.get("product_ids"):
        filters += "\n       AND pol.product_id = ANY(%(product_ids)s)"
    return f"""{name} AS (
    SELECT pol.product_id,
           pol.order_id,
           pol.product_uom_qty::float8 AS qty,
           CASE WHEN COALESCE(pol.product_qty, 0) = 0 THEN pol.qty_received::float8
                ELSE pol.qty_received::float8 * pol.product_uom_qty::float8 / pol.product_qty::float8
           END AS rcv_qty,
           pol.price_subtotal::float8
               / COALESCE(NULLIF(po.currency_rate::float8, 0), 1.0)
               * {_fx("po.company_id")} AS amount{extra_select}
      FROM purchase_order po
      JOIN purchase_order_line pol ON pol.order_id = po.id{joins}
     WHERE po.state IN %(po_states)s
       AND po.company_id = ANY(%(company_ids)s)
       AND pol.display_type IS NULL
       AND pol.product_id IS NOT NULL
       AND ({period_sql}){filters}
)"""


def _sales_cte(params):
    """Net sold quantity / revenue from posted customer invoices and credit
    notes (company currency -> report currency), Top-N products only."""
    wh_filter = ""
    if params.get("sale_warehouse_ids"):
        # Only when sale_stock is installed: keep invoice lines coming from a
        # sale order of the selected warehouses.
        wh_filter = """
       AND EXISTS (
            SELECT 1
              FROM sale_order_line_invoice_rel rel
              JOIN sale_order_line sol ON sol.id = rel.order_line_id
              JOIN sale_order so ON so.id = sol.order_id
             WHERE rel.invoice_line_id = aml.id
               AND so.warehouse_id = ANY(%(sale_warehouse_ids)s))"""
    return f"""sales AS (
    SELECT aml.product_id,
           SUM(CASE WHEN am.move_type = 'out_refund' THEN -1 ELSE 1 END
               * CASE WHEN aml.product_uom_id IS NULL OR aml.product_uom_id = pt.uom_id
                      THEN aml.quantity::float8
                      -- Odoo 19 UoM: qty_in_to = qty * from.factor / to.factor
                      ELSE aml.quantity::float8 * lu.factor::float8 / NULLIF(pu.factor, 0)::float8
                 END) AS sold_qty,
           SUM(-aml.balance::float8 * {_fx("aml.company_id")}) AS revenue
      FROM top
      JOIN account_move_line aml ON aml.product_id = top.product_id
      JOIN account_move am ON am.id = aml.move_id
      JOIN product_product pp ON pp.id = aml.product_id
      JOIN product_template pt ON pt.id = pp.product_tmpl_id
      JOIN uom_uom pu ON pu.id = pt.uom_id
      LEFT JOIN uom_uom lu ON lu.id = aml.product_uom_id
     WHERE aml.display_type = 'product'
       AND aml.parent_state = 'posted'
       AND aml.date >= %(sale_date_from)s
       AND aml.date <= %(sale_date_to)s
       AND aml.company_id = ANY(%(company_ids)s)
       AND am.move_type IN ('out_invoice', 'out_refund'){wh_filter}
     GROUP BY aml.product_id
)"""


def build_basic_query(params):
    """Top-N items purchased in one period, with receipts and sales."""
    rank = _rank(params)
    period = "po.date_approve >= %(dt_from)s AND po.date_approve < %(dt_to)s"
    lines = _po_lines_cte(params, period)
    line_aggs = """SUM(qty) AS qty,
           SUM(amount) AS amount,
           SUM(rcv_qty) AS rcv_qty,
           SUM(GREATEST(qty - rcv_qty, 0)) AS pending_qty,
           MIN(amount / NULLIF(qty, 0)) AS min_rate,
           MAX(amount / NULLIF(qty, 0)) AS max_rate"""
    if rank == "po_count":
        # exact distinct count needed for every product: two-level aggregate
        agg = f"""per_po AS (
    SELECT product_id, order_id, {line_aggs}
      FROM lines
     GROUP BY product_id, order_id
),
agg AS (
    SELECT product_id,
           SUM(qty) AS qty, SUM(amount) AS amount, SUM(rcv_qty) AS rcv_qty,
           SUM(pending_qty) AS pending_qty, MIN(min_rate) AS min_rate,
           MAX(max_rate) AS max_rate, COUNT(*) AS po_count
      FROM per_po
     GROUP BY product_id
)"""
        po_counts = ""
        po_count_expr = "top.po_count"
        po_count_join = ""
    else:
        agg = f"""agg AS (
    SELECT product_id, {line_aggs}
      FROM lines
     GROUP BY product_id
)"""
        top_lines = _po_lines_cte(params, period, name="top_lines", only_from="top")
        po_counts = f""",
{top_lines},
po_counts AS (
    SELECT product_id, COUNT(DISTINCT order_id) AS po_count
      FROM top_lines
     GROUP BY product_id
)"""
        po_count_expr = "COALESCE(pc.po_count, 0)"
        po_count_join = "\n  LEFT JOIN po_counts pc ON pc.product_id = top.product_id"
    return f"""
WITH {lines},
{agg},
top AS (
    SELECT agg.*,
           SUM(amount) OVER () AS all_amount,
           SUM(qty) OVER () AS all_qty,
           COUNT(*) OVER () AS all_items
      FROM agg
     ORDER BY {rank} DESC NULLS LAST, product_id
     LIMIT %(limit)s
){po_counts},
{_sales_cte(params)}
SELECT top.product_id,
       top.qty,
       top.amount,
       top.amount / NULLIF(top.qty, 0) AS avg_rate,
       top.min_rate,
       top.max_rate,
       {po_count_expr} AS po_count,
       top.rcv_qty,
       top.pending_qty,
       COALESCE(sales.sold_qty, 0) AS sold_qty,
       COALESCE(sales.revenue, 0) AS revenue,
       top.all_amount,
       top.all_qty,
       top.all_items
  FROM top{po_count_join}
  LEFT JOIN sales ON sales.product_id = top.product_id
 ORDER BY {"top.po_count" if rank == "po_count" else "top." + rank} DESC NULLS LAST, top.product_id
"""


def build_comparison_query(params):
    """Current period vs comparison period.

    Returns the Top-N items of the current period (section ``main``) followed
    by the Top-N "lost" items, i.e. bought in the comparison period only
    (section ``lost``). Every row carries the global statistics; when nothing
    was purchased in either period a single row with ``section = NULL`` is
    returned."""
    rank = _rank(params)
    in_a = "(po.date_approve >= %(dt_from)s AND po.date_approve < %(dt_to)s)"
    in_b = "(po.date_approve >= %(cmp_dt_from)s AND po.date_approve < %(cmp_dt_to)s)"
    extra = f",\n           {in_a} AS in_a,\n           {in_b} AS in_b"
    lines = _po_lines_cte(params, f"{in_a} OR {in_b}", extra_select=extra)
    if rank == "po_count":
        agg = """per_po AS (
    SELECT product_id, order_id, bool_or(in_a) AS in_a, bool_or(in_b) AS in_b,
           SUM(qty) FILTER (WHERE in_a) AS qty_a, SUM(qty) FILTER (WHERE in_b) AS qty_b,
           SUM(amount) FILTER (WHERE in_a) AS amount_a, SUM(amount) FILTER (WHERE in_b) AS amount_b
      FROM lines
     GROUP BY product_id, order_id
),
agg AS (
    SELECT product_id,
           COUNT(*) FILTER (WHERE in_a) AS n_a,
           COUNT(*) FILTER (WHERE in_b) AS n_b,
           COALESCE(SUM(qty_a), 0) AS qty_a, COALESCE(SUM(qty_b), 0) AS qty_b,
           COALESCE(SUM(amount_a), 0) AS amount_a, COALESCE(SUM(amount_b), 0) AS amount_b,
           COUNT(*) FILTER (WHERE in_a) AS po_count_a,
           COUNT(*) FILTER (WHERE in_b) AS po_count_b
      FROM per_po
     GROUP BY product_id
)"""
        counts = ""
        cnt_a, cnt_b, cnt_join = "r.po_count_a", "r.po_count_b", ""
    else:
        agg = """agg AS (
    SELECT product_id,
           COUNT(*) FILTER (WHERE in_a) AS n_a,
           COUNT(*) FILTER (WHERE in_b) AS n_b,
           COALESCE(SUM(qty) FILTER (WHERE in_a), 0) AS qty_a,
           COALESCE(SUM(qty) FILTER (WHERE in_b), 0) AS qty_b,
           COALESCE(SUM(amount) FILTER (WHERE in_a), 0) AS amount_a,
           COALESCE(SUM(amount) FILTER (WHERE in_b), 0) AS amount_b
      FROM lines
     GROUP BY product_id
)"""
        sel_lines = _po_lines_cte(
            params, f"{in_a} OR {in_b}", extra_select=extra,
            name="sel_lines", only_from="selected")
        counts = f""",
selected AS (
    SELECT product_id FROM main UNION ALL SELECT product_id FROM lost
),
{sel_lines},
po_counts AS (
    SELECT product_id,
           COUNT(DISTINCT order_id) FILTER (WHERE in_a) AS po_count_a,
           COUNT(DISTINCT order_id) FILTER (WHERE in_b) AS po_count_b
      FROM sel_lines
     GROUP BY product_id
)"""
        cnt_a = "COALESCE(pc.po_count_a, 0)"
        cnt_b = "COALESCE(pc.po_count_b, 0)"
        cnt_join = "\n  LEFT JOIN po_counts pc ON pc.product_id = r.product_id"
    return f"""
WITH {lines},
{agg},
stats AS (
    SELECT COUNT(*) FILTER (WHERE n_a > 0) AS items_a,
           COUNT(*) FILTER (WHERE n_b > 0) AS items_b,
           COUNT(*) FILTER (WHERE n_a > 0 AND n_b = 0) AS new_items,
           COUNT(*) FILTER (WHERE n_a = 0 AND n_b > 0) AS lost_items,
           COALESCE(SUM(amount_a), 0) AS all_amount_a,
           COALESCE(SUM(amount_b), 0) AS all_amount_b,
           COALESCE(SUM(qty_a), 0) AS all_qty_a,
           COALESCE(SUM(qty_b), 0) AS all_qty_b
      FROM agg
),
main AS (
    SELECT 'main'::text AS section, agg.*
      FROM agg
     WHERE n_a > 0
     ORDER BY {rank}_a DESC NULLS LAST, product_id
     LIMIT %(limit)s
),
lost AS (
    SELECT 'lost'::text AS section, agg.*
      FROM agg
     WHERE n_a = 0 AND n_b > 0
     ORDER BY {rank}_b DESC NULLS LAST, product_id
     LIMIT %(limit)s
){counts}
SELECT r.section, r.product_id,
       r.qty_a, r.qty_b, r.amount_a, r.amount_b,
       r.amount_a / NULLIF(r.qty_a, 0) AS rate_a,
       r.amount_b / NULLIF(r.qty_b, 0) AS rate_b,
       {cnt_a} AS po_count_a, {cnt_b} AS po_count_b,
       s.items_a, s.items_b, s.new_items, s.lost_items,
       s.all_amount_a, s.all_amount_b, s.all_qty_a, s.all_qty_b
  FROM stats s
  LEFT JOIN (SELECT * FROM main UNION ALL SELECT * FROM lost) r ON TRUE{cnt_join}
 ORDER BY r.section DESC NULLS LAST,
          CASE WHEN r.section = 'main' THEN r.{rank}_a ELSE r.{rank}_b END DESC NULLS LAST,
          r.product_id
"""


def prepare_params(params):
    """Normalise python values into what psycopg2 expects."""
    p = dict(params)
    p["po_states"] = tuple(p.get("po_states") or PO_STATES)
    p["company_ids"] = list(p["company_ids"])
    p["fx_company_ids"] = list(p["fx_company_ids"])
    p["fx_factors"] = [float(f) for f in p["fx_factors"]]
    p["limit"] = int(p["limit"])
    for key in ("picking_type_ids", "categ_ids", "sale_warehouse_ids", "product_ids"):
        if p.get(key):
            p[key] = list(p[key])
    return p
