# Using fbc

fbc keeps track of a record shop's stock and sales, and keeps them in step
with Discogs and eBay. This is what each part of the app does. To set it up,
see [Running fbc in production](production.md).

Before syncing, sign in to each site once: Sales > Connect eBay and
Discogs > Connect Discogs. The app keeps the tokens in the database.

## Inventory

Units (one copy of a record each), the Purchase Lots they came in and the
Storage Boxes they're kept in.

## Discogs

The Discogs collection, synced in, with Add Unit for releases that have none
yet.

## Sales

eBay listings and orders, synced in. eBay Drafts turn a Unit into a listing,
and Ship This turns an order into a Sales Receipt.

### Earnings

A Sales Receipt keeps how its order's earnings add up to Net Sold, as eBay's
Finances API gives them: the sale, each fee eBay took out of it (final value
fee, regulatory fee and so on), the shipping label, refunds and other
charges such as ad fees. A refund is the whole amount given back to the
buyer, with any fees eBay credited back as lines of their own. Ship This
saves them with the receipt, and the receipt's page shows them.

The lines always add up to Net Sold. When Net Sold is typed in, or edited
later, and differs from eBay's lines, a manual adjustment line makes up the
difference. A receipt with no lines from eBay (dev mode, eBay unreachable, or
added by hand) has none at all, and reports count its fees as not broken
down.

A refund or a label bought after shipping changes an order's earnings. Refresh
earnings from eBay, on the receipt's page, reads them again and shows what
would change. Saving replaces the lines, and changes Net Sold to eBay's total
unless you untick that. It's off in dev mode.

Receipts from before this was added have no lines. To fill them in from eBay,
newest first:

```bash
flask --app fbc backfill-earnings
```

It only shows what it would do. Add `--apply` to save. Where eBay's total
differs from Net Sold, often a refund that wasn't recorded, it keeps Net Sold
and adds a manual adjustment; check those receipts with Refresh earnings, or
add `--update-net` to take eBay's total for all of them. `--since 2025-01-01`
and `--limit 50` run it in parts. eBay may no longer have older orders' sales,
and those receipts are left as they are. Run it in the live container like
`setup-db` (see [Running fbc in production](production.md)).

## Supplies

The packing supplies sales use up, and Purchase Orders to restock them.

Supply Use records each time stock changes, with its date: Ship This, Add
Unit, edits to Quantity on the Supply List (a stock-take or a restock, which
counts below 0) and Reconcile Supplies. Each supply's page lists its own. It
starts from when this was added, with nothing before.

Cost Per Use on a supply, set by hand, is what one use costs, for costing
each sale's supplies.

### Reconcile Supplies

Ship This and Add Unit take supplies out of stock by estimate, so the Supply
List drifts from what's on hand. Under Supplies > Reconcile Supplies, count
what's on hand and enter it for any supplies, then check the differences and
save them all at once. For a roll, enter how many are on hand, counting the
one in use, and about how many uses are left on it.

Supply Use keeps each difference apart from Supply List edits, so you can
see which estimates drift. Purchase Orders aren't changed. Ship This's and
Add Unit's "There aren't enough" messages and the home page's reorder banner
link here.
