"""
Archiving: old, finished records are marked `archived` so the lists leave
them out by default. It changes nothing but those lists: the home page and
fbc/finance.py never look at the column, so every money figure counts
archived records too.

Each model has an `archived` column. `archive_all` (flask archive) sets it
on whatever has aged out under the rules below; the Archive and Unarchive
list actions set and clear it by hand. The column is only ever set by those,
never by a rule that is re-checked on each page load.

The ages are in days, from `ARCHIVE_AFTER_DAYS` in config.py. A model
whose age is missing, 0 or None is never archived by the command.
"""

import datetime

from flask import request, url_for
from flask_appbuilder.actions import action
from flask_appbuilder.models.sqla.filters import FilterNotEqual
from sqlalchemy import and_, exists, func, or_

DEFAULT_DAYS = 365

# The ARCHIVE_AFTER_DAYS keys, in the order the command reports them.
MODELS = ("UNITS", "EBAY_LISTINGS", "EBAY_ORDERS", "SALES_RECEIPTS")


def cutoffs(config, now=None):
    """
    {model key: the moment before which a record counts as old}, from
    config's ARCHIVE_AFTER_DAYS. A model it leaves out gets DEFAULT_DAYS; one
    set to 0 or None isn't archived at all, and isn't in the result.
    """
    now = now or datetime.datetime.now()
    days = config.get("ARCHIVE_AFTER_DAYS") or {}
    result = {}
    for key in MODELS:
        age = days.get(key, DEFAULT_DAYS)
        if age:
            result[key] = now - datetime.timedelta(days=age)
    return result


def _held_below_zero(db):
    """
    Ids of the Sales Receipts eBay is still holding money for, because a
    return or payment dispute is open (see fbc.shipping.held): their
    disputes less the holds released are below 0.
    """
    from fbc.models.sales import SalesReceiptLine
    from fbc.shipping import HOLD_RELEASE

    return (
        db.session.query(SalesReceiptLine.sales_receipt_id)
        .filter(
            or_(
                SalesReceiptLine.kind == "DISPUTE",
                SalesReceiptLine.transaction_id.like(HOLD_RELEASE + "%"),
            )
        )
        .group_by(SalesReceiptLine.sales_receipt_id)
        .having(func.sum(SalesReceiptLine.amount) < 0)
    )


def candidates(db, cutoff):
    """
    {model key: a query of the records that should be archived but aren't},
    for the keys in `cutoff` ({key: datetime}, from `cutoffs`).
    """
    from fbc.models.ebay import EbayListing, EbayOrder
    from fbc.models.inventory import Unit
    from fbc.models.sales import SalesReceipt, sales_receipt_ebay_order

    session = db.session
    found = {}

    if "UNITS" in cutoff:
        # A Unit has no date of its own. It sold when its Sales Receipt is
        # dated, or, with no receipt, when its listing's first order came in.
        # With neither it can't be dated, so only the list action archives it.
        first_order = (
            session.query(func.min(EbayOrder.created_time))
            .filter(EbayOrder.ebay_listing_id == Unit.ebay_listing_id)
            .correlate(Unit)
            .scalar_subquery()
        )
        receipt_date = (
            session.query(SalesReceipt.date)
            .filter(SalesReceipt.id == Unit.sales_receipt_id)
            .correlate(Unit)
            .scalar_subquery()
        )
        found["UNITS"] = session.query(Unit).filter(
            Unit.sold.is_(True),
            Unit.archived.is_(False),
            or_(
                receipt_date < cutoff["UNITS"].date(),
                and_(Unit.sales_receipt_id.is_(None), first_order < cutoff["UNITS"]),
            ),
        )

    if "EBAY_LISTINGS" in cutoff:
        # Sold: it has Units, and every one of them is sold. Its age is when
        # it started.
        has_unit = exists().where(Unit.ebay_listing_id == EbayListing.item_id)
        has_unsold = exists().where(
            Unit.ebay_listing_id == EbayListing.item_id, Unit.sold.is_(False)
        )
        found["EBAY_LISTINGS"] = session.query(EbayListing).filter(
            EbayListing.archived.is_(False),
            EbayListing.start_time < cutoff["EBAY_LISTINGS"],
            has_unit,
            ~has_unsold,
        )

    if "EBAY_ORDERS" in cutoff:
        # Shipped: it's on a Sales Receipt. No outstanding return or refund:
        # none of its receipts has eBay holding money back. An order whose
        # receipt has no earnings lines can't show a hold, so counts as clear.
        receipts = session.query(sales_receipt_ebay_order.c.ebay_order_id).filter(
            sales_receipt_ebay_order.c.sales_receipt_id.in_(_held_below_zero(db))
        )
        found["EBAY_ORDERS"] = session.query(EbayOrder).filter(
            EbayOrder.archived.is_(False),
            EbayOrder.created_time < cutoff["EBAY_ORDERS"],
            EbayOrder.sales_receipts.any(),
            ~EbayOrder.order_id.in_(receipts),
        )

    if "SALES_RECEIPTS" in cutoff:
        found["SALES_RECEIPTS"] = session.query(SalesReceipt).filter(
            SalesReceipt.archived.is_(False),
            SalesReceipt.date < cutoff["SALES_RECEIPTS"].date(),
        )

    return found


def archive_all(apply=False, config=None, now=None, echo=print):
    """
    Archive everything that has aged out. Without `apply` it only counts.
    Returns {model key: how many}.
    """
    from fbc import app, db

    cutoff = cutoffs(config if config is not None else app.config, now)
    counts = {}
    for key, query in candidates(db, cutoff).items():
        counts[key] = query.count()
        if apply:
            for record in query:
                record.archived = True
        echo(
            ("Archived " if apply else "Would archive ")
            + str(counts[key])
            + " "
            + key.replace("_", " ").lower()
            + ", from before "
            + str(cutoff[key].date())
            + "."
        )
    if apply:
        db.session.commit()
    return counts


class ArchivableViewMixin:
    """
    For a ModelView of a model with an `archived` column: its list leaves
    archived records out unless it's told otherwise, and Archive and Unarchive
    actions change them by hand.

    The list shows everything with `?archived=all`, and only the archived
    with `_flt_0_archived=y` (the Archived search). Any filter on Archived
    turns the default off. Related lists, on another record's page, always
    show everything.
    """

    @action(
        "archive",
        "Archive",
        "Archive the selected records?",
        "fa-archive",
        single=False,
    )
    def archive_items(self, items):
        return self._set_archived(items, True)

    @action(
        "unarchive",
        "Unarchive",
        "Unarchive the selected records?",
        "fa-undo",
        single=False,
    )
    def unarchive_items(self, items):
        return self._set_archived(items, False)

    def _set_archived(self, items, value):
        from flask import flash, redirect

        from fbc import db

        if not isinstance(items, list):
            items = [items]
        for item in items:
            item.archived = value
        db.session.commit()
        flash(
            ("Archived " if value else "Unarchived ") + str(len(items)) + " records.",
            "success",
        )
        return redirect(self.get_redirect())

    def _hides_archived(self, filters):
        """Whether this request is this view's own list, with no say on Archived."""
        if request.endpoint != self.endpoint + ".list":
            return False
        if request.args.get("archived") == "all":
            return False
        return filters.get_filter_value("archived") is None

    def render_template(self, template, **kwargs):
        # A link above the list to show or hide the archived records.
        if request.endpoint == self.endpoint + ".list":
            args = request.args.to_dict(flat=False)
            if request.args.get("archived") == "all":
                args.pop("archived")
                kwargs["archive_link"] = (
                    "Showing archived records too.",
                    "Hide archived",
                    url_for(self.endpoint + ".list", **args),
                )
            elif not any(
                a.startswith("_flt_") and a.endswith("_archived") for a in args
            ):
                args["archived"] = "all"
                kwargs["archive_link"] = (
                    "Archived records are hidden.",
                    "Show archived",
                    url_for(self.endpoint + ".list", **args),
                )
        return super().render_template(template, **kwargs)

    def _get_list_widget(self, filters, *args, **kwargs):
        if self._hides_archived(filters):
            filters.add_filter("archived", FilterNotEqual, "y")
        return super()._get_list_widget(filters, *args, **kwargs)
