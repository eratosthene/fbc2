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

## The home page

It has the alerts first (supplies to reorder, a To do banner, and free
insertions close to or over the limit), then three sections:

- **Listings**: the active listings and this month's free insertions (see
  below).
- **Stock**: the Units in stock.
- **Money**: supply cost, other expenses, net profit and the breakeven price
  per Unit, with Expense Details and Profit Details to open. Profit Details
  has a table by Purchase Lot and one by Storage Box, each with its Units,
  Units Sold and Stock.

Every page has the shop's name in small type at the bottom.

### Tasks

The shop keeps the Discogs and eBay syncs up to date by itself, every few
minutes to a couple of hours (see "Scheduled syncs" in
[Running fbc in production](production.md)). What they bring in becomes a task.
The To do banner on the home page counts each kind and links to its list, and
the Tasks menu has the same four lists. A task leaves its list as soon as the
work is done, so there is nothing to tick off:

- **Orders to Ship**: eBay Orders that have no Sales Receipt yet. Use Ship This
  on each. Archived orders are left out.
- **Releases to Make Units**: Discogs Releases with more copies in your Discogs
  collection than unsold Units. Add a Unit for the new copy and price it. A
  copy that sold leaves the collection, so its sold Unit doesn't count.
- **Units to Draft**: unsold Units priced over $2, with no eBay Listing and no
  eBay Draft. (Cheaper Units go in the discount boxes.) Make an eBay Draft
  from the Unit.
- **Drafts to List**: eBay Drafts that aren't published yet. Take the pictures
  and publish.

## Inventory

Units (one copy of a record each), the Purchase Lots they came in and the
Storage Boxes they're kept in.

A Purchase Lot can be linked to a Discogs Folder (the Discogs Folder field on
its form, chosen from the folders synced from Discogs). It's optional, and
several lots may share a folder. The link is only a starting choice: Identify
Record chooses that folder for you on its add-to-Discogs form, and nothing is
added or moved on Discogs until you press that form's button. Run Sync Discogs
first if a folder you made on Discogs isn't offered.

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
Give what you know: artist, title, catalogue number, barcode or matrix (at
least one), the country (US to start with), and the Purchase Lot it came in.
Only vinyl is searched. Type is LP unless you choose 7" (45 RPM), 12" or Any
vinyl. Reissue is blank for either, or Reissue or Not a reissue to filter on
that. Color is optional, like red or clear: a release whose format notes list
it ranks higher, and none is hidden for lacking it.

The best way in is the master, as on Discogs. If you know it, type its number
or address into Master. If not, fill in Artist and Title and press **Find
master from Artist and Title**: it lists the masters Discogs finds, and **Use
this master** fills in the Master field (keeping what else you typed). Then
press **Search Discogs**. With a Master it lists that master's versions,
filtered by Country, Type and Reissue, instead of searching on Artist, Title,
Catalogue Number and Barcode, so no version is missed because of how Discogs
ranks a search. It checks the matrix of 20 versions at a time. With a matrix
typed in it carries on to the next 20 by itself until a batch has an exact
match, checking at most 40 versions in one search; otherwise, or to go on past
that, press "Check more versions". It looks through at most 500 versions of
one master. Discogs has no matrix filter for a master's versions, so each
version is a call to read its matrix, and Discogs allows 60 calls a minute.

Without a Master, it searches Discogs, then reads the matrix of each release
it finds. Discogs documents no way to search on a matrix, so that's a call per
release. It checks the first 12 releases, or 20 when you give a matrix (Discogs allows 60
calls a minute). When you give a matrix it also asks Discogs's own text search
for it, and what that finds is checked first and marked "Found by matrix". If
a release you expect isn't listed, give more to narrow the search (the artist
and title, or a catalogue number), or paste its Release ID.

Releases are listed best match first, with every picture (click one to see it
full size, and use the arrows to step through them), format, label, catalogue
number, year and the matrix text with the part that matched highlighted. A
closed "Notes, barcode and other identifiers" section under each shows the
release's notes and every identifier Discogs lists, such as the full barcode.

How the matrix is scored: case, spaces and punctuation are ignored. An
**exact** match is the same text as a matrix listed on the release. A
**partial** match shares a run of at least 4 letters and digits that covers at
least 60% of the shorter one, and scores higher the more it covers. A release
that lists **no matrix** is kept, with a note to look at its pictures. One that
lists matrices and none close is a **mismatch** and goes last. The matrix
counts for most: an exact match outranks a release that only has the right
country. A matching country and a matching color move a release up too.

Each result has two buttons.

- **This one: add to Discogs** opens a form for a copy of the release in your
  Discogs collection: Folder (the Discogs Folder set on the Purchase Lot, if it
  has one, is chosen to start with; change it if you like), Media Condition, Sleeve Condition, Notes, Dust
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

If you've already found the release on Discogs, paste its number, [r123456] or
address into Release ID. That replaces the other fields: Search Discogs
fetches just that release (one call) and shows it as the only result. Country
and Matrix still rank it, so you can see whether they match.

Add Unit from here, and from the Add Unit link on the Releases list, names the Unit with the master's year, "Artist - Title
(1970)", and fills in Pressing as the country code, the release's year and
Original or Reissue, like "US 1970 Original" or "UK 1990 Reissue". It's a
Reissue if Discogs lists the release as a Reissue or Repress. Countries Discogs
spells out are shortened for the common ones (Germany is DE); any other is used
as Discogs spells it.

### Price Research on Add Unit

Under the Add Unit form is a closed Price Research block. Nothing is fetched
until you open it and click Look up prices, so the page opens as fast as
before. It starts with links to the release on Discogs and to its sold
history there, to open in another tab (the sold history needs you signed in
to Discogs; fbc doesn't read it), then shows, each labelled with where it comes from:

- **Your own sales of this master**: the average sold price of your Units of
  the same Discogs master. This is also what pre-fills the Retail Price when
  you come from a Discogs release.
- **Discogs, asking prices**: the release's Genre and Style on Discogs, and
  its master's if it has one, to help you choose a Storage Box, then Discogs's suggested price for each condition
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

### Active listings and free insertions

The eBay listings sync reads eBay's whole active list. Any listing fbc has
that isn't in it is marked Ended (the eBay Listings list shows a column, and
can filter on it), and fbc remembers when the sync last finished. If a page
doesn't come back, or the listings don't add up to the count eBay reports, the
sync fails and changes nothing, so a bad answer can't mark listings ended.
Run it again.

The home page shows the active listings as of that sync, and this month's
free insertions: used so far, renewals still due before the month ends, and
the projected total, against the limit (250 unless
`EBAY_SETTINGS['LISTING']['FREE_INSERTIONS']` in `config.py` says
otherwise; a Store subscription raises it). It warns when the projected total
is within 10 of the limit. Nothing shows until the first listings sync, as
every listing looks active before it. After a sync, the Ended flags are right
for the listings fbc already had, so the first one after an upgrade ends all
the old listings that aren't on eBay now.

The count is fbc's estimate, from these assumptions:

- Each new listing, and each Good 'Til Cancelled renewal, uses one. A
  listing renews once a month, on the day of the month and the Pacific clock
  time it started, so every active listing uses one insertion a month. That
  matches eBay's Time Left on the three listings I checked. A listing that
  started on the 29th to 31st is taken to renew on the last day of a shorter
  month, which is a guess. The listings sync saves the date eBay's Time Left
  gives, and when it falls in the month, fbc uses that date instead. Ending
  a listing early doesn't give the insertion back.
- The month is a calendar month in US Pacific time.
- A listing that ended is counted up to the last sync that saw it active.
  One fbc never saw before it ended is missed, so the count may be a little
  low. eBay has no call for the exact number, so the home page has a box for
  the free listings left that the eBay app (or Seller Hub) shows.

To calibrate, run the listings sync, then open Calibrate with the eBay app on
the home page and enter the app's number. fbc works out how far its own count was from it and adds that to the
month's used count; its own count carries on from there. Enter it again any
time, and the new number replaces the old one. A number applies to its month
only: on the 1st the count starts from fbc's own again, so it's worth
entering the app's number once early in each month to check.

Publishing an eBay Draft that would take the projected count past the limit
shows a warning and needs Publish anyway ticked, but still goes ahead. If the
last Check with eBay reported an insertion fee that no discount covered, the
draft page says so too: that is eBay saying the free ones are used up.

### Pictures on an eBay Draft

Pictures go to eBay's picture hosting when you choose them on the draft page,
and the draft keeps only their eBay addresses. A photo that is only upright
through its phone's orientation tag is turned upright first, so it doesn't
come out sideways on eBay.

Each picture has Rotate left and Rotate right buttons, next to Make main and
Remove. fbc fetches the picture from eBay, turns it 90 degrees, uploads it as
a new picture and puts it where the old one was. The old copy stays on eBay,
unused. Each turn is saved again as a JPEG at high quality, so a picture
turned many times loses a little. If the fetch or upload fails, the draft is
left as it was and a message says why. This changes drafts only, not
listings that are already published. Dev Mode turns the buttons off, since
they reach eBay.

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

### Mystery boxes

Some listings sell several random Units from one Storage Box with each sale,
like "5 albums selected at random". On the listing's edit page, set Mystery
Box: Pick From Storage Box and Mystery Box: Units Per Sale. Set both, or
neither. No Unit is linked to such a listing. When eBay relists it, fbc keeps
both settings.

Ship This on a mystery box order picks the Units, as many as the sale's Units
per sale times the boxes bought, from the Storage Box's unsold Units. It
leaves out Units with a listing of their own that is still active, and
prefers records whose release or master this buyer didn't get on an earlier
Sales Receipt. The picks show as a pull list:

- Re-roll replaces one pick with another random one, for a record that's
  damaged or missing.
- To swap one for a Unit you choose, type that Unit's id in its Swap in Unit
  box and press Apply swaps.
- The picks stay on the page, so posting it again doesn't re-roll them. Reloading
  the page after Ship This first opens it does. Nothing is saved until Ship.
- If the box has too few unsold Units, Ship This says how many are missing and
  can't ship. Restock the box, or pick the rest from another Storage Box with
  Pick from it.

Ship records one Sales Receipt for the order, linked to every picked Unit,
and marks them sold, takes out supplies for all of them and removes each
Unit's own copy from Discogs. The receipt's price is split evenly over the
Units.

Sales > Mystery Boxes, and the listing's page, show how many
more boxes the Storage Box can fill: its unsold Units without a listing of
their own, divided by the Units per sale, rounded down. eBay keeps selling as
many boxes as the listing's quantity says, so lower the quantity on eBay when
it runs out. fbc doesn't set it for you. Listings that have ended aren't on
the Mystery Boxes page.

### Shipment tracking

Each eBay Order shows its carrier and tracking number, linked to the
carrier's own tracking page (USPS, UPS, FedEx and DHL; any other carrier
shows as text). They come from eBay with the orders sync, so an order shows
them after the first Sync (or Sync New) that runs once its label is bought.
The list's Tracking column is blank until then.

fbc doesn't show whether a package has moved or been delivered. eBay only
gives the number and carrier, and the progress is the carrier's to give, which
needs a carrier account and keys. Click the link to see it. fbc can't print a
label again either: eBay has no way to do that for a label bought on eBay, so
open the order's Order Id link and use the label link there.

In Dev Mode nothing is fetched, so orders only show tracking that was already
synced.

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
number of supplies: save the order, then add each supply from the order's
page, under Supplies On Purchase Order, with how many came. That many are
added to the supply's Quantity (count packs, like rolls, not the uses in
each). Changing or removing one takes the difference back out. Orders
from before this have no quantity, since the Supply List already counted
them. Anything else on it, like record crates, is an Expense
on the order: add it from the order's page, under Expenses. Those come out
of the order's price, and the rest is the order's Supply Cost. The
Expenses on an order can't come to more than its price.

Supply Use records each time stock changes, with its date: Ship This, Add
Unit, Purchase Orders (counted below 0, as Purchase Order), edits to Quantity
on the Supply List (a stock-take, which can count below 0) and Reconcile
Supplies. Each supply's page lists its own. It
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
