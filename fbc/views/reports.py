"""The Reports menu (see fbc/sales/reports.py)."""

from flask import Response, request
from flask_appbuilder import BaseView, expose, has_access

from fbc.sales import report_csv, reports


class CsvDownload:
    """
    Adds /download to a report: its table as CSV for the same period. A view
    names the function in fbc.sales.report_csv as `csv_report`.
    """

    csv_report = None

    def _csv_response(self, name, rows):
        return Response(
            report_csv.render(rows),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )

    @expose("/download")
    @has_access
    def download(self):
        period = reports.Period(request.args)
        args = [period]
        if self.csv_report == "aging":
            args.append(reports.old_after_months(request.args.get("old")))
        name, rows = getattr(report_csv, self.csv_report)(*args)
        return self._csv_response(name, rows)


class ProfitAndLossView(CsvDownload, BaseView):
    """Reports > Profit & Loss: a period's sales and profit, split by marketplace."""

    csv_report = "profit_and_loss"
    route_base = "/profitandloss"
    default_view = "report"

    @expose("/")
    @has_access
    def report(self):
        period = reports.Period(request.args)
        return self.render_template(
            "reports/profit_and_loss.html",
            period=period,
            presets=reports.PRESETS,
            groups=reports.GROUPS,
            marketplace_names=reports.MARKETPLACES,
            **reports.profit_and_loss(period),
        )

    @expose("/records")
    @has_access
    def records(self):
        """Every record in the period, for an accountant."""
        name, rows = report_csv.records(reports.Period(request.args))
        return self._csv_response(name, rows)


class InventoryReportView(CsvDownload, BaseView):
    """Reports > Inventory: stock in, out and left, in Units and money."""

    csv_report = "inventory"
    route_base = "/inventoryreport"
    default_view = "report"

    @expose("/")
    @has_access
    def report(self):
        period = reports.Period(request.args)
        return self.render_template(
            "reports/inventory.html",
            period=period,
            presets=reports.PRESETS,
            groups=reports.GROUPS,
            **reports.inventory(period),
        )


class LotsReportView(CsvDownload, BaseView):
    """Reports > Lots: the Purchase Lots bought in the period, and how they did."""

    csv_report = "lots"
    route_base = "/lotsreport"
    default_view = "report"

    @expose("/")
    @has_access
    def report(self):
        period = reports.Period(request.args)
        return self.render_template(
            "reports/lots.html",
            period=period,
            presets=reports.PRESETS,
            groups=reports.GROUPS,
            **reports.lots_report(period),
        )


class SalesReportView(CsvDownload, BaseView):
    """Reports > Sales: averages, days to sell, and gross by year."""

    csv_report = "sales"
    route_base = "/salesreport"
    default_view = "report"

    @expose("/")
    @has_access
    def report(self):
        period = reports.Period(request.args)
        return self.render_template(
            "reports/sales.html",
            period=period,
            presets=reports.PRESETS,
            groups=reports.GROUPS,
            marketplace_names=reports.MARKETPLACES,
            **reports.sales_report(period),
        )


class AgingReportView(CsvDownload, BaseView):
    """Reports > Aging: the Units in stock, by how long ago their Lot was bought."""

    csv_report = "aging"
    route_base = "/agingreport"
    default_view = "report"

    @expose("/")
    @has_access
    def report(self):
        period = reports.Period(request.args)
        old_after = reports.old_after_months(request.args.get("old"))
        return self.render_template(
            "reports/aging.html",
            period=period,
            presets=reports.PRESETS,
            groups=reports.GROUPS,
            old_after=old_after,
            **reports.aging(period, old_after),
        )


class SuppliesReportView(CsvDownload, BaseView):
    """Reports > Supplies: what supplies cost and how many are used, and break-even."""

    csv_report = "supplies"
    route_base = "/suppliesreport"
    default_view = "report"

    @expose("/")
    @has_access
    def report(self):
        period = reports.Period(request.args)
        return self.render_template(
            "reports/supplies.html",
            period=period,
            presets=reports.PRESETS,
            groups=reports.GROUPS,
            **reports.supplies_report(period),
        )
