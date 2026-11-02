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

## Expenses

Business costs that aren't Purchase Lots or Purchase Orders, like an eBay
Store subscription, equipment, booth fees or mileage, under Supplies >
Expenses. Each has a date, category and amount, and optionally a vendor, a
note and a link to the receipt.

The categories follow Schedule C's lines, so a year's totals map onto the
tax form. `EXPENSE_CATEGORIES` in `config.py` changes them.

For mileage, enter Miles and leave Amount blank: it's Miles × `MILEAGE_RATE`
from `config.py`, or the Rate Per Mile typed in. Each expense keeps the rate
it was entered with, since the IRS changes it every year.

Packing supplies still go on a Purchase Order, so the Supply List stays
right. Equipment is recorded when it's bought; depreciating it is left to the
accountant.

The home page takes expenses off the total net profit, with a total per
category.
