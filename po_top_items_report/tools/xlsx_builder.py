# Part of po_top_items_report. License OPL-1.
"""Excel (.xlsx) renderer. Pure function of the report data dict, so it can
be tested without an Odoo server."""
import io

import xlsxwriter

BRAND = "#714B67"
BRAND_LIGHT = "#F3EDF2"
GREY = "#6C757D"


def _num_format(decimals):
    return "#,##0" + ("." + "0" * decimals if decimals > 0 else "")


def _formats(wb, data):
    cur = data["currency"]
    money = _num_format(cur["decimal_places"])
    qty = _num_format(min(data["qty_digits"], 3))
    base = {"font_name": "Arial", "font_size": 10, "valign": "vcenter"}
    rtl = data.get("direction") == "rtl"
    start = "right" if rtl else "left"

    def f(**kw):
        d = dict(base)
        d.update(kw)
        return wb.add_format(d)

    head = dict(bold=True, font_color="#FFFFFF", bg_color=BRAND, border=1,
                border_color="#5A3A52", text_wrap=True, align="center")
    tot = dict(bold=True, bg_color=BRAND_LIGHT, top=2, top_color=BRAND, bottom=1)
    return {
        "title": f(bold=True, font_size=16, font_color=BRAND, align=start),
        "subtitle": f(font_color=GREY, italic=True, align=start),
        "flabel": f(bold=True, font_color=GREY, align=start),
        "fvalue": f(align=start),
        "kpi_label": f(font_color=GREY, font_size=9, align="center", bg_color="#F8F9FA",
                       top=1, left=1, right=1, border_color="#DEE2E6", text_wrap=True),
        "kpi_int": f(bold=True, font_size=13, align="center", num_format="#,##0",
                     bg_color="#F8F9FA", bottom=1, left=1, right=1, border_color="#DEE2E6"),
        "kpi_amount": f(bold=True, font_size=13, align="center", num_format=money,
                        bg_color="#F8F9FA", bottom=1, left=1, right=1, border_color="#DEE2E6"),
        "kpi_pct": f(bold=True, font_size=13, align="center", num_format='0.0"%"',
                     bg_color="#F8F9FA", bottom=1, left=1, right=1, border_color="#DEE2E6"),
        "head": f(**head),
        "section": f(bold=True, font_size=12, font_color=BRAND, align=start),
        "text": f(border=1, border_color="#E9ECEF", align=start),
        "center": f(border=1, border_color="#E9ECEF", align="center"),
        "int": f(border=1, border_color="#E9ECEF", num_format="#,##0"),
        "qty": f(border=1, border_color="#E9ECEF", num_format=qty),
        "money": f(border=1, border_color="#E9ECEF", num_format=money),
        "pct": f(border=1, border_color="#E9ECEF", num_format='0.0"%";[Red]-0.0"%"'),
        # price changes: an increase is unfavourable for purchasing
        "pct_inv": f(border=1, border_color="#E9ECEF", num_format='[Red]0.0"%";[Color10]-0.0"%"'),
        "signed_qty": f(border=1, border_color="#E9ECEF", num_format=qty + ";[Red]-" + qty),
        "money_signed": f(border=1, border_color="#E9ECEF", num_format=money + ";[Red]-" + money),
        "tot_text": f(**tot, align=start),
        "tot_int": f(**tot, num_format="#,##0"),
        "tot_qty": f(**tot, num_format=qty),
        "tot_money": f(**tot, num_format=money),
        "tot_pct": f(**tot, num_format='0.0"%";[Red]-0.0"%"'),
        "tot_blank": f(**tot),
        "st_new": f(border=1, border_color="#E9ECEF", align="center", bold=True,
                    font_color="#0F5132", bg_color="#D1E7DD"),
        "st_up": f(border=1, border_color="#E9ECEF", align="center", font_color="#0F5132"),
        "st_down": f(border=1, border_color="#E9ECEF", align="center", font_color="#842029"),
        "st_same": f(border=1, border_color="#E9ECEF", align="center", font_color=GREY),
        "st_lost": f(border=1, border_color="#E9ECEF", align="center", bold=True,
                     font_color="#842029", bg_color="#F8D7DA"),
        "empty": f(italic=True, font_color=GREY, align=start),
    }


def _header_block(ws, fmt, data, ncols, title):
    """Title, filters and KPI cards. Returns the next free row."""
    ws.merge_range(0, 0, 0, ncols - 1, title, fmt["title"])
    ws.set_row(0, 26)
    labels = data["labels"]
    ws.merge_range(1, 0, 1, ncols - 1, "%s - %s %s" % (
        data["company_name"], labels["generated"], data["generated_at"]), fmt["subtitle"])
    row = 3
    for flt in data["filters"]:
        ws.merge_range(row, 0, row, 1, flt["label"], fmt["flabel"])
        ws.merge_range(row, 2, row, ncols - 1, flt["value"], fmt["fvalue"])
        row += 1
    if data["kpis"]:
        row += 1
        width = max(1, min(2, ncols // max(len(data["kpis"]), 1)))
        col = 0
        for kpi in data["kpis"]:
            if col + width > ncols:
                break
            value = kpi["value"]
            kfmt = fmt["kpi_" + kpi["type"]]
            if width > 1:
                ws.merge_range(row, col, row, col + width - 1, kpi["label"], fmt["kpi_label"])
                if value is None:
                    ws.merge_range(row + 1, col, row + 1, col + width - 1, "-", kfmt)
                else:
                    ws.merge_range(row + 1, col, row + 1, col + width - 1, value, kfmt)
            else:
                ws.write(row, col, kpi["label"], fmt["kpi_label"])
                ws.write(row + 1, col, "-" if value is None else value, kfmt)
            col += width
        ws.set_row(row, 28)
        ws.set_row(row + 1, 24)
        row += 2
    return row + 1


def _write_table(ws, fmt, start_row, columns, lines, totals_label, totals=None):
    """Write header, lines and a SUBTOTAL() totals row.

    Formulas are written with their pre-computed result (from ``totals``) so
    viewers that do not recalculate on open still show the right figures.

    ``columns``: list of (key, label, kind, width, total) where ``total`` is
    "sum", a callable(first_row, last_row, col_letters) returning a formula,
    or None."""
    for col, (_k, label, _kind, width, _t) in enumerate(columns):
        ws.write(start_row, col, label, fmt["head"])
        ws.set_column(col, col, width)
    ws.set_row(start_row, 32)
    first = start_row + 1
    for i, line in enumerate(lines):
        r = first + i
        for col, (key, _label, kind, _w, _t) in enumerate(columns):
            value = line.get(key)
            if kind == "status":
                ws.write(r, col, line.get("status_label", ""), fmt["st_" + line["status"]])
            elif value is None:
                ws.write_blank(r, col, None, fmt["center"] if kind != "text" else fmt["text"])
            elif kind in ("text", "center"):
                ws.write_string(r, col, str(value), fmt[kind])
            else:
                ws.write_number(r, col, value, fmt[kind])
    last = first + len(lines) - 1
    tot_row = last + 1
    letters = {key: xlsxwriter.utility.xl_col_to_name(c) for c, (key, *_r) in enumerate(columns)}
    totals = totals or {}

    def cached(key):
        value = totals.get(key)
        return value if isinstance(value, (int, float)) else ""
    for col, (key, _label, kind, _w, total) in enumerate(columns):
        if col == 0:
            ws.write(tot_row, col, totals_label, fmt["tot_text"])
        elif total == "sum" and lines:
            letter = letters[key]
            tfmt = {"int": "tot_int", "qty": "tot_qty", "signed_qty": "tot_qty",
                    "money": "tot_money", "money_signed": "tot_money"}.get(kind, "tot_blank")
            ws.write_formula(tot_row, col, "=SUBTOTAL(9,%s%d:%s%d)" % (
                letter, first + 1, letter, last + 1), fmt[tfmt], cached(key))
        elif callable(total) and lines:
            ws.write_formula(tot_row, col, total(first + 1, last + 1, letters),
                             fmt["tot_pct"] if kind == "pct" else fmt["tot_money"], cached(key))
        else:
            ws.write_blank(tot_row, col, None, fmt["tot_blank"])
    if lines:
        ws.autofilter(start_row, 0, last, len(columns) - 1)
    return tot_row + 2


def _basic_columns(L):
    def gp_pct(a, b, x):
        return '=IFERROR({gp}{t}/SUMPRODUCT({sq}{a}:{sq}{b},{sr}{a}:{sr}{b})*100,"")'.format(
            gp=x["gross_profit"], sq=x["sold_qty"], sr=x["sale_rate"], a=a, b=b, t=b + 1)

    def avg_rate(a, b, x):
        return '=IFERROR({am}{t}/{q}{t},"")'.format(am=x["purchased_amount"], q=x["purchased_qty"], t=b + 1)

    return [
        ("rank", L["rank"], "int", 5, None),
        ("name", L["item"], "text", 42, None),
        ("barcode", L["barcode"], "text", 16, None),
        ("uom", L["uom"], "center", 9, None),
        ("purchased_qty", L["purchased_qty"], "qty", 13, "sum"),
        ("purchased_amount", L["purchased_amount"], "money", 16, "sum"),
        ("avg_rate", L["avg_rate"], "money", 13, avg_rate),
        ("min_rate", L["min_rate"], "money", 13, None),
        ("max_rate", L["max_rate"], "money", 13, None),
        ("po_count", L["po_count"], "int", 9, "sum"),
        ("received_qty", L["received_qty"], "qty", 13, "sum"),
        ("pending_qty", L["pending_qty"], "qty", 13, "sum"),
        ("sold_qty", L["sold_qty"], "signed_qty", 12, "sum"),
        ("sale_rate", L["sale_rate"], "money", 13, None),
        ("gross_profit", L["gross_profit"], "money_signed", 15, "sum"),
        ("gp_margin", L["gp_margin"], "pct", 9, gp_pct),
    ]


def _comparison_columns(L):
    def diff_pct(a, b, x):
        return '=IFERROR(({qa}{t}-{qb}{t})/ABS({qb}{t})*100,"")'.format(
            qa=x["qty_a"], qb=x["qty_b"], t=b + 1)

    return [
        ("rank", L["rank"], "int", 5, None),
        ("name", L["item"], "text", 42, None),
        ("barcode", L["barcode"], "text", 16, None),
        ("uom", L["uom"], "center", 9, None),
        ("status", L["status"], "status", 12, None),
        ("qty_a", L["qty_a"], "qty", 13, "sum"),
        ("qty_b", L["qty_b"], "qty", 13, "sum"),
        ("qty_diff", L["qty_diff"], "signed_qty", 13, "sum"),
        ("qty_diff_pct", L["qty_diff_pct"], "pct", 11, diff_pct),
        ("amount_a", L["amount_a"], "money", 16, "sum"),
        ("amount_b", L["amount_b"], "money", 16, "sum"),
        ("rate_a", L["rate_a"], "money", 13, None),
        ("rate_b", L["rate_b"], "money", 13, None),
        ("rate_diff_pct", L["rate_diff_pct"], "pct_inv", 11, None),
        ("po_a", L["po_a"], "int", 10, "sum"),
        ("po_b", L["po_b"], "int", 10, "sum"),
    ]


def _lost_columns(L):
    return [
        ("rank", L["rank"], "int", 5, None),
        ("name", L["item"], "text", 42, None),
        ("barcode", L["barcode"], "text", 16, None),
        ("uom", L["uom"], "center", 9, None),
        ("status", L["status"], "status", 12, None),
        ("qty_b", L["qty_b"], "qty", 13, "sum"),
        ("amount_b", L["amount_b"], "money", 16, "sum"),
        ("rate_b", L["rate_b"], "money", 13, None),
        ("po_b", L["po_b"], "int", 10, "sum"),
    ]


def build_xlsx(data):
    """Return the .xlsx file content (bytes) for a report data dict."""
    out = io.BytesIO()
    wb = xlsxwriter.Workbook(out, {"in_memory": True})
    wb.set_properties({"title": data["title"], "company": data["company_name"]})
    fmt = _formats(wb, data)
    L = data["labels"]
    rtl = data.get("direction") == "rtl"

    def sheet(name):
        ws = wb.add_worksheet(name[:31])
        if rtl:
            ws.right_to_left()
        ws.set_landscape()
        ws.set_paper(9)  # A4
        ws.fit_to_pages(1, 0)
        ws.hide_gridlines(2)
        return ws

    if data["report_type"] == "comparison":
        cols = _comparison_columns(L)
    else:
        cols = _basic_columns(L)
    ws = sheet(L["sheet_main"])
    row = _header_block(ws, fmt, data, len(cols), data["title"])
    if not data["lines"] and not data["lost_lines"]:
        ws.write(row, 0, L["no_data"], fmt["empty"])
    header_row = row
    totals = dict(data.get("totals") or {})
    if totals.get("purchased_qty"):
        totals["avg_rate"] = totals["purchased_amount"] / totals["purchased_qty"]
    _write_table(ws, fmt, row, cols, data["lines"], L["total"], totals)
    ws.freeze_panes(header_row + 1, 2)
    ws.repeat_rows(header_row)

    if data["report_type"] == "comparison":
        lcols = _lost_columns(L)
        ws2 = sheet(L["sheet_lost"])
        r2 = _header_block(ws2, fmt, data, len(lcols), L["lost_title"])
        ws2.merge_range(r2, 0, r2, len(lcols) - 1, L["lost_help"], fmt["subtitle"])
        r2 += 2
        _write_table(ws2, fmt, r2, lcols, data["lost_lines"], L["total"], data.get("lost_totals"))
        ws2.freeze_panes(r2 + 1, 2)

    wb.close()
    return out.getvalue()
