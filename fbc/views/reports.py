"""The Reports menu (see fbc/sales/reports.py)."""

from flask import request
from flask_appbuilder import BaseView, expose, has_access

from fbc.sales import reports


class ProfitAndLossView(BaseView):
    """Reports > Profit & Loss: a period's sales and profit, split by marketplace."""

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


class InventoryReportView(BaseView):
    """Reports > Inventory: stock in, out and left, in Units and money."""

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


class LotsReportView(BaseView):
    """Reports > Lots: the Purchase Lots bought in the period, and how they did."""

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


class SalesReportView(BaseView):
    """Reports > Sales: averages, days to sell, and gross by year."""

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


class AgingReportView(BaseView):
    """Reports > Aging: the Units in stock, by how long ago their Lot was bought."""

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
