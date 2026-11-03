/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ControlPanel } from "@web/search/control_panel/control_panel";
import { formatFloat, formatMonetary } from "@web/views/fields/formatters";
import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, useState } from "@odoo/owl";

const WIZARD_MODEL = "po.top.items.report.wizard";

/**
 * Interactive web view of the Top Purchased Items report.
 * All figures come from the server (same data as PDF / Excel); the client
 * only sorts, searches and opens drill-downs.
 */
export class PoTopItemsReport extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.controlPanelDisplay = {};
        this.wizardId = this.props.action.params && this.props.action.params.wizard_id;
        this.state = useState({
            loading: true,
            error: false,
            data: null,
            search: "",
            sortKey: null,
            sortAsc: false,
            busy: false,
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        if (!this.wizardId) {
            this.state.error = true;
            this.state.loading = false;
            return;
        }
        try {
            this.state.data = await this.orm.call(WIZARD_MODEL, "get_report_data", [[this.wizardId]]);
            this.state.error = false;
        } catch (e) {
            // Transient wizard records are garbage-collected after a while.
            this.state.error = true;
            console.warn("po_top_items_report: cannot load report", e);
        }
        this.state.loading = false;
    }

    // ------------------------------------------------------------------
    // Getters
    // ------------------------------------------------------------------
    get data() {
        return this.state.data;
    }
    get labels() {
        return (this.data && this.data.labels) || {};
    }
    get expiredMessage() {
        return PoTopItemsReport.expiredMessage;
    }
    get isComparison() {
        return this.data && this.data.report_type === "comparison";
    }
    _filterAndSort(lines) {
        const term = this.state.search.trim().toLowerCase();
        let result = lines;
        if (term) {
            result = lines.filter(
                (l) =>
                    (l.name || "").toLowerCase().includes(term) ||
                    (l.barcode || "").toLowerCase().includes(term)
            );
        }
        const key = this.state.sortKey;
        if (key) {
            const dir = this.state.sortAsc ? 1 : -1;
            result = [...result].sort((a, b) => {
                const va = a[key];
                const vb = b[key];
                if (va === vb) {
                    return a.rank - b.rank;
                }
                if (va === null || va === undefined) {
                    return 1;
                }
                if (vb === null || vb === undefined) {
                    return -1;
                }
                if (typeof va === "string") {
                    return va.localeCompare(vb) * dir;
                }
                return (va - vb) * dir;
            });
        }
        return result;
    }
    get lines() {
        return this.data ? this._filterAndSort(this.data.lines) : [];
    }
    get hasSearch() {
        return Boolean(this.state.search.trim());
    }
    /**
     * Totals of the rows currently shown: the server totals, or - while a
     * search is active - the sums of the matching rows only.
     */
    _visibleTotals(serverTotals, lines) {
        if (!this.hasSearch) {
            return serverTotals;
        }
        const totals = {};
        for (const key of Object.keys(serverTotals || {})) {
            if (key.endsWith("_pct") || key === "gp_margin") {
                continue;
            }
            totals[key] = lines.reduce((sum, line) => sum + (line[key] || 0), 0);
        }
        if ("gp_margin" in (serverTotals || {})) {
            totals.gp_margin = totals.revenue ? (totals.gross_profit / totals.revenue) * 100 : null;
        }
        if ("qty_diff_pct" in (serverTotals || {})) {
            totals.qty_diff_pct = totals.qty_b
                ? ((totals.qty_a - totals.qty_b) / Math.abs(totals.qty_b)) * 100
                : null;
        }
        return totals;
    }
    get footTotals() {
        return this._visibleTotals(this.data.totals, this.lines);
    }
    get lostFootTotals() {
        return this._visibleTotals(this.data.lost_totals, this.lostLines);
    }
    get lostLines() {
        return this.data ? this._filterAndSort(this.data.lost_lines) : [];
    }
    get basicColumns() {
        const L = this.labels;
        return [
            { key: "purchased_qty", label: L.purchased_qty, type: "qty" },
            { key: "purchased_amount", label: L.purchased_amount, type: "money", strong: true, drill: "po" },
            { key: "avg_rate", label: L.avg_rate, type: "rate" },
            { key: "po_count", label: L.po_count, type: "int", drill: "po" },
            { key: "received_qty", label: L.received_qty, type: "qty" },
            { key: "pending_qty", label: L.pending_qty, type: "qty", warn: true },
            { key: "sold_qty", label: L.sold_qty, type: "qty", drill: "sale" },
            { key: "sale_rate", label: L.sale_rate, type: "money" },
            { key: "gross_profit", label: L.gross_profit, type: "money", strong: true, signed: true },
            { key: "gp_margin", label: L.gp_margin, type: "pct" },
        ];
    }
    get comparisonColumns() {
        const L = this.labels;
        return [
            { key: "qty_a", label: L.qty_a, type: "qty", strong: true, drill: "po", period: "a" },
            { key: "qty_b", label: L.qty_b, type: "qty", drill: "po", period: "b" },
            { key: "qty_diff_pct", label: L.qty_diff_pct, type: "pct", signed: true },
            { key: "amount_a", label: L.amount_a, type: "money", strong: true },
            { key: "amount_b", label: L.amount_b, type: "money" },
            { key: "rate_a", label: L.rate_a, type: "money" },
            { key: "rate_b", label: L.rate_b, type: "money" },
            // a higher purchase price is bad news: colours are inverted
            { key: "rate_diff_pct", label: L.rate_diff_pct, type: "pct", signed: true, invert: true },
            { key: "po_a", label: L.po_a, type: "int", drill: "po", period: "a" },
            { key: "po_b", label: L.po_b, type: "int", drill: "po", period: "b" },
        ];
    }
    get lostColumns() {
        const L = this.labels;
        return [
            { key: "qty_b", label: L.qty_b, type: "qty", drill: "po", period: "b" },
            { key: "amount_b", label: L.amount_b, type: "money", strong: true },
            { key: "rate_b", label: L.rate_b, type: "money" },
            { key: "po_b", label: L.po_b, type: "int", drill: "po", period: "b" },
        ];
    }

    // ------------------------------------------------------------------
    // Formatting
    // ------------------------------------------------------------------
    fmtMoney(value) {
        if (value === null || value === undefined) {
            return "-";
        }
        return formatMonetary(value, { currencyId: this.data.currency.id });
    }
    fmtQty(value) {
        if (value === null || value === undefined) {
            return "-";
        }
        return formatFloat(value, { digits: [16, this.data.qty_digits] });
    }
    fmtPct(value) {
        if (value === null || value === undefined) {
            return "-";
        }
        return `${formatFloat(value, { digits: [16, 1] })}%`;
    }
    fmtValue(col, line) {
        const value = line[col.key];
        switch (col.type) {
            case "money":
            case "rate":
                return this.fmtMoney(value);
            case "qty":
                return this.fmtQty(value);
            case "pct":
                return this.fmtPct(value);
            case "int":
                return value === null || value === undefined ? "-" : String(Math.round(value));
            default:
                return value;
        }
    }
    fmtKpi(kpi) {
        if (kpi.type === "amount") {
            return this.fmtMoney(kpi.value);
        }
        if (kpi.type === "pct") {
            return this.fmtPct(kpi.value);
        }
        return formatFloat(kpi.value, { digits: [16, 0] });
    }
    cellClass(col, line) {
        const value = line[col.key];
        const classes = ["text-end", "text-nowrap"];
        if (col.strong) {
            classes.push("fw-bold");
        }
        if (col.signed && typeof value === "number" && value !== 0) {
            const good = col.invert ? value < 0 : value > 0;
            if (!good) {
                classes.push("text-danger");
            } else if (col.type === "pct") {
                classes.push("text-success");
            }
        }
        if (col.warn && value > 0) {
            classes.push("o_pti_warn");
        }
        if (col.drill) {
            classes.push("o_pti_drill");
        }
        return classes.join(" ");
    }
    hasRange(line) {
        return line.max_rate - line.min_rate > 0.0001;
    }
    sortIcon(key) {
        if (this.state.sortKey !== key) {
            return "fa fa-sort o_pti_sort_idle";
        }
        return this.state.sortAsc ? "fa fa-sort-amount-asc" : "fa fa-sort-amount-desc";
    }

    // ------------------------------------------------------------------
    // Handlers
    // ------------------------------------------------------------------
    onSort(key) {
        if (this.state.sortKey === key) {
            if (!this.state.sortAsc) {
                this.state.sortAsc = true;
            } else {
                this.state.sortKey = null; // back to server ranking
            }
        } else {
            this.state.sortKey = key;
            this.state.sortAsc = false;
        }
    }
    onSearch(ev) {
        this.state.search = ev.target.value;
    }
    async onDrill(col, line) {
        if (!col.drill) {
            return;
        }
        const action = await this.orm.call(WIZARD_MODEL, "action_drilldown", [
            [this.wizardId],
            line.product_id,
            col.drill,
            col.period || "a",
        ]);
        this.action.doAction(action);
    }
    async _run(method) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const action = await this.orm.call(WIZARD_MODEL, method, [[this.wizardId]]);
            await this.action.doAction(action);
        } finally {
            this.state.busy = false;
        }
    }
    onClickPdf() {
        return this._run("action_print_pdf");
    }
    onClickXlsx() {
        return this._run("action_export_xlsx");
    }
    onClickFilters() {
        if (this.state.error) {
            return this.action.doAction("po_top_items_report.action_po_top_items_wizard");
        }
        return this.action.doAction(
            {
                type: "ir.actions.act_window",
                res_model: WIZARD_MODEL,
                res_id: this.wizardId,
                views: [[false, "form"]],
                target: "new",
                name: _t("Top Purchased Items"),
            },
            { onClose: () => this.load() }
        );
    }
    onClickRefresh() {
        return this.load();
    }
}

PoTopItemsReport.template = "po_top_items_report.ReportView";
PoTopItemsReport.components = { ControlPanel };
PoTopItemsReport.expiredMessage = _t(
    "This report has expired. Open the filters to run it again."
);

registry.category("actions").add("po_top_items_report.report_view", PoTopItemsReport);
