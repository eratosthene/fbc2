"""
The line above a list that says when its synced data was last synced.

A view lists the syncs that feed it in `synced_by`; crud/list.html shows one
phrase per sync ("eBay listings last synced 2026-11-13 5:45 PM."), linking to
that sync. Adding a synced view is one line: mix in SyncedViewMixin and set
`synced_by`.
"""

from collections import namedtuple

from fbc.sync import jobs

# `name` is the sync_record name, `label` says what synced, `url` starts it.
Source = namedtuple("Source", "name label url")

DISCOGS = Source(jobs.DISCOGS_SYNC, "Discogs collection", "/syncdiscogs")
DISCOGS_LISTINGS = Source(
    jobs.DISCOGS_LISTINGS_SYNC, "Discogs listings", "/syncdiscogslistings"
)
DISCOGS_ORDERS = Source(
    jobs.DISCOGS_ORDERS_SYNC, "Discogs orders", "/syncdiscogsorders"
)
EBAY_LISTINGS = Source(jobs.LISTINGS_SYNC, "eBay listings", "/syncebaylistings")
EBAY_ORDERS = Source(jobs.ORDERS_SYNC, "eBay orders", "/syncebayorders")


def lines(sources):
    """[(Source, last finish or None)] for the sources, in one query."""
    from fbc import db
    from fbc.models.sync import SyncRecord

    names = [s.name for s in sources]
    found = {}
    if names:
        rows = db.session.query(SyncRecord).filter(SyncRecord.name.in_(names)).all()
        found = {r.name: r.finished for r in rows}
    return [(s, found.get(s.name)) for s in sources]


class SyncedViewMixin:
    """For a ModelView of synced data: its list says when each sync last ran."""

    synced_by = ()
    # A line of text above the list, under the sync lines.
    view_note = None

    def render_template(self, template, **kwargs):
        if template == self.list_template and self.synced_by:
            kwargs["sync_lines"] = lines(self.synced_by)
        if template == self.list_template and self.view_note:
            kwargs["view_note"] = self.view_note
        return super().render_template(template, **kwargs)
