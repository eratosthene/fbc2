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

The Purchase Lots list shows each lot's Units, how many are sold, its
Profit and its B/E (the price a Unit must sell for to cover the lot after
fees). Open a lot for its Breakdown. These come from the same maths as the
home page's Purchase Lots table, so the two always agree: a Sales Receipt's
money is split between its Units, and a lot's price evenly between its
Units. Fee % and ROI are percents, and B/E is worked out from the lot's own
fee %.

Adding a Unit without a Retail Price still saves it, but warns, with a link to
edit the Unit and add one.

### Archiving

Old, finished records can be archived so the long lists leave them out. It
only changes the lists. The home page, Purchase Lot figures and earnings
totals still count archived records, so no money figure changes.

The Units, eBay Listings, eBay Orders and Sales Receipts lists hide archived
records by default. Above each list, **Show archived** shows everything and
**Hide archived** goes back. To see only the archived ones, search Archived
equal to Yes. Related lists, such as the Units on a Purchase Lot, always
show everything.

`flask --app fbc archive` archives what has aged out, and only shows what it
would do until you add `--apply`. Each age is in days in `config.py`
(`ARCHIVE_AFTER_DAYS`, 365 by default; 0 turns one off):

- **Units** that are Sold, from when their Sales Receipt is dated. A Unit
  with no receipt uses the date of the first eBay Order on its listing, and
  with neither it can't be dated, so it's never archived by the command.
- **eBay Listings** whose Units are all Sold, from when the listing started.
- **eBay Orders** that have shipped (are on a Sales Receipt) and have no
  return or refund open, from when the order was placed. An open return or
  dispute is read from the Sales Receipt's earnings lines (eBay holding the
  money back), so an order with no earnings lines counts as clear.
- **Sales Receipts**, from their date.

To archive or unarchive by hand, select rows in a list and choose Archive or
Unarchive. The command only archives, never unarchives, so it archives an
unarchived record again on the next run if it still qualifies. Run it now
and then, such as monthly.

## Discogs

The Discogs collection, synced in, with Add Unit for releases that have none
yet.

### Identify Record

Inventory > Identify Record finds the Discogs release a record in hand is.
Give what you know: artist, title, catalogue number or barcode (at least one),
the country (US to start with), the vinyl matrix, and the Purchase Lot it
came in. Only vinyl is searched. Type is LP unless you choose 7" (45 RPM), 12"
or Any vinyl. Reissue is blank for either, or Reissue or Not a reissue to
filter on that. Color is optional, like red or clear: a release whose format
notes list it ranks higher, and none is hidden for lacking it. It searches Discogs, reads the matrix of each release it finds
(Discogs can't search on a matrix, so this is a call per release; it checks
the first 12, and Discogs allows 60 calls a minute), and lists them best match
first, with pictures, format, label, catalogue number, year and the matrix
text, the part that matched highlighted.

How the matrix is scored: case, spaces and punctuation are ignored. An
**exact** match is the same text as a matrix listed on the release. A
**partial** match shares a run of at least 4 letters and digits that covers at
least 60% of the shorter one. A release that lists **no matrix** is kept, with
a note to look at its pictures. One that lists matrices and none close is a
**mismatch** and goes last. A matching country moves a release up.

Each result has two buttons.

- **This one: add to Discogs** opens a form for a copy of the release in your
  Discogs collection: Folder, Media Condition, Sleeve Condition, Notes, Dust
  Jacket Condition and Extras Included (any of those not set up as custom
  fields on your Discogs account are left off, and it says which). Only its
  button, "Add to my Discogs collection and add the Unit", **writes to your
  Discogs account**: it adds the copy, sets the fields, saves the release in
  fbc as Sync Discogs would, and opens Add Unit with the release, Purchase Lot,
  matrix and instance chosen, the two conditions as the grading (like
  "NM or M-/VG+") and the Notes. Dust Jacket Condition and Extras Included only
  go to Discogs. If a field fails to set, the copy is still added and it says
  which. If the copy is added but fbc can't save it, run Sync Discogs.
- **Just add the Unit** saves the release in fbc only and adds nothing to
  Discogs, so the Unit has no collection copy (Ship This has nothing to remove
  for it).

Results are kept for five minutes. If Discogs isn't connected, says no or
asks for fewer requests, the page says so; when the rate limit hits part way,
the releases not yet checked are listed unchecked. In Dev Mode it says "Not
available in dev mode." and offers "Try the sample data", which ranks a made-up
list so the matching can be tried without Discogs.

### Price Research on Add Unit

Under the Add Unit form is a closed Price Research block. Nothing is fetched
until you open it and click Look up prices, so the page opens as fast as
before. It starts with links to the release on Discogs and to its sold
history there, to open in another tab (the sold history needs you signed in
to Discogs; fbc doesn't read it), then shows, each labelled with where it comes from:

- **Your own sales of this master**: the average sold price of your Units of
  the same Discogs master. This is also what pre-fills the Retail Price when
  you come from a Discogs release.
- **Discogs, asking prices**: Discogs's suggested price for each condition
  (Mint to Poor), the lowest price now for sale and how many are, and how many
  collections and wantlists have the release. Suggested prices need seller
  settings filled in on your Discogs account; without them the rest still
  shows.
- **eBay, asking prices**: active Buy It Now listings in eBay's Records
  category that match the Unit's name, grouped by condition, with the lowest,
  median and highest price. They match on words, so some may be other
  pressings. Only the first page of results is used.

None of these are sold prices. Discogs's API has no sold history (its "Last
sold" figures are only on discogs.com), and eBay gives sold prices only to
approved partners. Under the eBay figures is a link to eBay's own sold-items
search for the Unit's name, which opens on eBay.

Each figure has a Use button that puts it in the Retail Price field. Results
are kept for ten minutes, so looking again doesn't call the sites again, and
a site that fails or isn't connected shows its message without stopping the
other or the form. In Dev Mode the block says "Not available in Dev Mode."

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

### SKUs on old listings

Listings made from eBay Drafts get `fbc-<unit id>` as their SKU (Custom
label), so fbc can tell which Unit they are. For older listings, `flask --app fbc set-listing-skus` sets it from the Unit linked to each listing. It
only shows what it would do until you add `--apply`, and lists the listings
it can't set, with why, in a CSV. See
[Running fbc in production](production.md) for the live run.

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
