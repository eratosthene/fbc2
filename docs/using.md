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
