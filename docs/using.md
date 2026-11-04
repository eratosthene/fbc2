# Using fbc

fbc keeps track of a record shop's stock and sales, and keeps them in step
with Discogs and eBay. This is what each part of the app does. To set it up,
see [Running fbc in production](production.md).

Before syncing, sign in to each site once: Sales > Connect eBay and
Discogs > Connect Discogs. The app keeps the tokens in the database.

## Searching by id

Every list can be searched by its id (for eBay Listings and Orders, the item
or order id; for Discogs Releases, the release id). Where the id is a plain
number, "Matches id" is the first choice, and it takes a single id (`42`), a
range (`100-200`) or a mix (`5, 100-200`).

## Inventory

Units (one copy of a record each), the Purchase Lots they came in and the
Storage Boxes they're kept in.

Adding a Unit without a Retail Price still saves it, but warns, with a link to
edit the Unit and add one.

## Discogs

The Discogs collection, synced in, with Add Unit for releases that have none
yet.

## Sales

eBay listings and orders, synced in. eBay Drafts turn a Unit into a listing,
and Ship This turns an order into a Sales Receipt.

### Ship This

Once the label is bought on eBay, Ship This on an order records its Sales
Receipt, marks its Units sold, takes the supplies used out of stock and
removes each Unit's own copy from Discogs. An order can have several items:
it finds every Unit on the order's listings, as many as were bought from
each. Order syncs keep every item; an order synced before fbc did only knows
its first, so Sync the orders again if Ship This says eBay has more. When
it can't tell which Units an order sold, it says why and links to adding
the Sales Receipt by hand.

The order list has a Ship This link for each order not shipped yet, and a
link to its Sales Receipt once it is. The Title shows every item on an order
of several. When the buyer has other orders without a Sales Receipt from the
same week, Ship This lists them; tick the ones going in the package.
They get one Sales Receipt, dated the first order, with every order's
earnings, and the supplies for one package.

eBay charges shipping on each order. Buy one label, on any of the orders,
for the package. Ship This shows what the buyer paid for shipping, what the
label cost, and how much to refund on one of the orders on eBay to pass on
the saving. Reload the page once it's refunded, so the refund is in the
earnings.

### Combining old Sales Receipts

Before Ship This handled several Units, such shipments were recorded by hand,
as a Sales Receipt per Unit of one order, or a receipt per order of one
package. Each is one Sales Receipt now. To find them and put them together:

```bash
flask --app fbc combine-receipts
```

It finds receipts that share an order, and orders that went in another
order's package: eBay's lines show their sale but no label of their own,
and the buyer has an order with a label within three days. It shows each
group and how the home page's numbers change, and only saves with
`--apply`. `--receipts 516,517` combines just those receipts. To combine
receipts it doesn't find, select them in the Sales Receipts list and choose
Combine.

A combined receipt covers every order and Unit, and the supplies used. Its
Sold Price is the orders' prices and its Net Sold the receipts' added up.
It keeps the receipts' earnings lines when all of them had lines. Otherwise
it has none: Refresh earnings, or run `backfill-earnings`, to get them from
eBay.

The home page splits a receipt of several Units between them by what each
sold for, rather than evenly. That's the price on the order, or for a
combined receipt the Unit's share of its old receipt. Failing that, it's the
listing's asking price, then its Retail Price, and last the average of the
receipt's other Units.

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

So does a receipt whose eBay order is on other receipts too, from splitting
an order of several Units into a receipt per Unit by hand. eBay's earnings
are for the whole order and can't be split between them, so Refresh earnings
and the backfill leave those receipts alone until they're combined (see
Combining old Sales Receipts). Their page says so.

A receipt of several orders keeps the earnings of all of them, and each
line says which order it came from.

A refund or a label bought after shipping changes an order's earnings. Refresh
earnings from eBay, on the receipt's page, reads them again and shows what
would change. Saving replaces the lines, and changes Net Sold to eBay's total
unless you untick that. It's off in dev mode.

While a return request or a payment dispute is open, eBay holds the sale's
money, so its total is low until it closes. If the buyer never sends the item
back, eBay releases the money and doesn't charge for the unused return label.
Refresh earnings says when eBay is holding money, and leaves the Net Sold
change unticked. Refresh again once it's closed.

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
and those receipts are left as they are. So are receipts eBay is holding money
on, until a later run. Run it in the live container like
`setup-db` (see [Running fbc in production](production.md)).

## Supplies

The packing supplies sales use up, and Purchase Orders to restock them.

A Purchase Order is one order, with its whole price. It can restock any
number of supplies. Anything else on it, like record crates, is an Expense
on the order: add it from the order's page, under Expenses. Those come out
of the order's price, and the rest is the order's Supply Cost. The
Expenses on an order can't come to more than its price.

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
right. Something bought on a Purchase Order along with supplies is an
Expense on that order (see [Supplies](#supplies)), so its cost isn't counted
twice. Deleting the order keeps its Expenses.

Equipment is recorded when it's bought; depreciating it is left to the
accountant.

The home page takes expenses off the total net profit, with a total per
category. Its total supply cost leaves out the Expenses on Purchase Orders.
