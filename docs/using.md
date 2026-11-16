# Using fbc

fbc keeps track of a record shop's stock and sales, and keeps them in step
with Discogs and eBay. This is what each part of the app does. To set it up,
see [Running fbc in production](production.md).

Before syncing, sign in to each site once: Connect > Connect eBay and
Connect > Connect Discogs. The app keeps the tokens in the database.

## Searching by id

Every list can be searched by its id (for eBay Listings and Orders, the item
or order id; for Discogs Releases, the release id). Where the id is a plain
number, "Matches id" is the first choice, and it takes a single id (`42`), a
range (`100-200`) or a mix (`5, 100-200`).

## The home page

It has the alerts first (supplies to reorder, a To do banner, and free
insertions close to or over the limit), then these sections, in order:

- **Listings**: the active listings and this month's free insertions (see
  below).
- **Stock**: the Units in stock.
- **Sales by marketplace**: Sales Receipts, Sold Price and Net Sold for eBay,
  Discogs and sales not linked to an order.
- **Net profit** and the **breakeven price per Unit**, then **Profit Details**
  to open: a table by Purchase Lot and one by Storage Box, each with its
  Units, Units Sold and Stock.
- **Other items**: shown once an Other Item has sold (see below): how many
  sold and their sales, and profit for those that have a cost. It's apart
  from everything else here.
- **Money**: supply cost and other expenses, with Expense Details to open.

Every page has the shop's name in small type at the bottom.

### Dates and times

Times show in your timezone (the `TIMEZONE` setting, Central by default), as
`2026-11-13 5:45 PM`; a date taken from a time is `2026-11-13`. That covers
the eBay Listings and Orders lists, Discogs Listings, Supply Use, the Ship This
page, the Connect eBay and Connect Discogs pages, the home page's last sync
and the log lines. The Start Time and Created Time on the eBay Listing and
eBay Order forms are in your timezone too. Dates you type, such as a Sales
Receipt's, an Expense's or a Purchase Lot's, show as typed. Searching a time
column still compares against UTC, which is how the times are stored.

Every list of synced data (Releases, Folders, eBay Listings, eBay Orders, the
Tasks lists and the like) has a small line above it saying when each sync
behind it last finished, or "Never synced". Click the name to run that sync.

A Sales Receipt made by Ship This takes its date from the order's UTC date, so
an order placed at 8 PM Central on the 13th gets the 14th. It is left that way
because the monthly reports count by that date.

### Tasks

The shop keeps the Discogs and eBay syncs up to date by itself, every few
minutes to a couple of hours (see "Scheduled syncs" in
[Running fbc in production](production.md)). What they bring in becomes a task.
The To do banner on the home page counts each kind and links to its list, and
the Tasks menu has the same lists. A task leaves its list as soon as the
work is done, so there is nothing to tick off:

- **Orders to Ship**: eBay Orders that have no Sales Receipt yet. Use Ship This
  on each. Archived orders are left out.
- **Discogs Orders to Ship**: Discogs Orders that aren't cancelled and have no
  Sales Receipt yet. Use Ship This on each.
- **Listings to End by Hand**: a listing fbc tried to end and couldn't, after its
  Unit sold on the other marketplace (see "Selling on both eBay and Discogs").
  The home page shows these in a red banner of their own. The task clears
  when the listing is gone.
- **Releases to Make Units**: Discogs Releases with more copies in your Discogs
  collection than unsold Units. Add a Unit for the new copy and price it. A
  copy that sold leaves the collection, so its sold Unit doesn't count.
- **Units to Draft (eBay)**: unsold Units priced over $2, with no eBay Listing
  and no eBay Draft. (Cheaper Units go in the discount boxes.) Make an eBay
  Draft from the Unit.
- **Drafts to List (eBay)**: eBay Drafts that aren't published yet. Take the
  pictures and publish.
- **Units to Draft (Discogs)**: unsold Units priced over $2 that are in your
  Discogs collection and have no Discogs Draft or For Sale listing. Press List
  on Discogs on the Unit. A Unit that is on eBay shows here too, since it can
  be sold in both places; List on Discogs warns you about it.
- **Drafts to List (Discogs)**: Discogs listings that are still a Draft. Add
  the pictures on Discogs, then press Make it For Sale. It leaves the list
  once Sync Discogs Listings sees it is For Sale.

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

The Units, eBay Listings, eBay Orders, Discogs Orders and Sales Receipts lists
hide archived
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
- **Discogs Orders** whose status is Shipped, Refund Sent or Cancelled, from
  when the order was placed.
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

eBay listings and orders, and Discogs listings and orders, synced in. eBay Drafts turn a Unit into a listing,
and Ship This turns an order into a Sales Receipt.

### Discogs Listings

Discogs keeps what you have **for sale** (the Marketplace inventory) apart from
your collection. The Discogs Listings list (Sales menu) shows that
inventory as fbc last read it, with each listing's release, price,
condition, sleeve condition, status, location and when it was posted. Press
Sync on that list, run `flask sync discogs-listings`, or let the scheduler do
it every 30 minutes.

The sync only reads from Discogs. It never lists, changes or deletes
anything there, so a listing is changed on Discogs and the next sync shows it.
(Listing a Unit, below, is the only thing fbc writes to Discogs.)
It reads every status, not just For Sale, so Drafts and Expired items show
too. A listing made by hand on the Discogs site shows up as well.

- A listing links to its Unit when its External ID (the Private Comments on
  Discogs) is `fbc-<Unit ID>`, as eBay's SKU is. Listings made by hand
  usually have none, so they have no Unit. That is fine; nothing is lost.
- A listing is marked Ended when it is Sold (or Deleted, Violation or
  Suspended, which Discogs won't let you edit again), or is no longer in the
  Discogs inventory because you deleted it there. A listing that comes back
  stops being Ended. Draft and Expired listings aren't Ended, since you can
  still put them up for sale. Only a listing with status For Sale that isn't
  Ended is on the Marketplace.
- To clear a listing out of fbc, for test data say, delete it on Discogs, run
  Sync Discogs Listings so it is marked Ended, then press the delete button on
  its row (or its page). Only an Ended listing has one, and it removes fbc's
  row only; nothing changes on Discogs. If Discogs still reports the listing
  with the status Deleted, the next sync brings the row back, so check the
  list after a sync.
- If a page of the inventory doesn't come back, or the listings don't add up
  to the count Discogs reports, the sync fails and changes nothing, so a bad
  answer can't mark listings ended. Run it again.
- Pictures can't be sent to Discogs from fbc. Add them on the Discogs site
  (Add Photos on the listing). See "The Discogs Marketplace" in
  `docs/development.md`.

### Discogs Orders

The Discogs Orders list (Sales menu) shows what has been bought from you on
the Discogs Marketplace, as fbc last read it: when, the order number (a link
to the order on Discogs), the first item's title, the buyer, Discogs's status
for it (New Order, Invoice Sent, Payment Received, Shipped and so on), the total
and Discogs's selling fee. Press Sync on that list, run
`flask sync discogs-orders`, or let the scheduler do it every 15 minutes.

- The sync only reads from Discogs. You invoice the buyer, mark the order
  shipped and message them on the Discogs site; the next sync shows the new
  status.
- A plain sync reads the last 30 days of orders and brings each up to date,
  since Discogs changes the status as you work through an order. **Deep Sync**
  (or `flask sync discogs-orders --deep`) reads every order Discogs has.
- Each item links to its Unit when its listing is one fbc has linked to a
  Unit (see Discogs Listings), so run the listings sync first for a new
  listing. An order of something you listed by hand has no Unit.
- Discogs gives the total (items, shipping and any tax) and its selling fee,
  but not what payment processing took or what the label cost, so fbc can't
  know what you earned. Wherever a Discogs sale's earnings show (Ship This,
  the Sales Receipt and the home page) they are the total less Discogs's fee,
  and say so. Correct Net Sold to what you really kept.
- The list has a Ship This link on each order not shipped yet, and a link to
  its Sales Receipt after (see "Ship This for a Discogs order").
- Old finished orders can be archived (see Archiving).

### List on Discogs

A Unit that is in your Discogs collection and not sold has a **List on
Discogs** link in its Links column (Units list and Unit page), next to List on
eBay. Nothing is posted until you press the button, and posting makes a
**Draft**, which buyers can't see. In order:

1. Press List on Discogs. A form opens, filled in from the Unit. Check it:
   - **Media and Sleeve Condition** come from the Unit's grading, which is
     "media/sleeve", like `NM/VG+`. M is Mint (M); NM, M- and NM or M- are
     Near Mint (NM or M-); VG+, VG, G+, G, F and P are the same words in full
     (Very Good Plus (VG+) and so on). A grading Discogs has no word for, such
     as EX, or a Unit with no sleeve grade, leaves that box on "(choose)".
   - **Price** is the Unit's retail price.
   - **Comments** are the Unit's Description, shown to buyers.
   - **Allow offers** starts ticked. Set `'ALLOW_OFFERS': False` in
     `DISCOGS_SETTINGS` in `config.py` to start it unticked.
   - **Weight (grams)** is eBay Drafts' package weight (by record size and
     number of discs), which is in ounces, times 28.35. Discogs works out
     shipping from it.
   - **External ID** is `fbc-<Unit ID>` and **Location** is the Unit's Storage
     Box. fbc sets External ID so the sync can find the Unit.
2. Press **Post to Discogs as a Draft**. fbc saves the listing at once, so it
   shows in Discogs Listings without a sync, and opens it.
3. On the listing page, press **Open on Discogs** and **add pictures there**.
   Discogs's API can't take pictures, so this has to be done by hand.
4. Back on the listing page, press **Make it For Sale (goes live)**. That is
   the only step that makes it live, and fbc never does it for you. Only a
   Draft that is still a Draft on Discogs is changed.

If a Unit has an eBay listing that is active, or an eBay Draft that hasn't
been published, the form and listing page say so with a link. You can go on:
fbc ends the other listing when the Unit sells (see "Selling on both eBay and
Discogs").

fbc won't list a Unit that:

- already has a Draft or For Sale listing on Discogs (it opens that listing
  instead);
- is sold; or
- isn't in your Discogs collection. A Unit made with "Just add the Unit" has no
  collection copy, so it has nothing to list. The collection stays a record
  of what you have. Add it to the collection on Discogs first and run Sync
  Discogs, or use "Add to Discogs" in Identify Record.

If Discogs refuses the Draft, its message is shown and nothing is saved in
fbc. If Discogs doesn't answer, the Draft may or may not have been made. Press
the button again: fbc first reads the newest 100 items in your Discogs
inventory for one with this Unit's External ID, and saves that one instead of
posting a second. A later sync of the same listing updates fbc's row rather
than adding another.

In Dev Mode the button shows "Dev Mode is turned on, listing on Discogs
disabled." and nothing is posted.

### Active listings and free insertions

The eBay listings sync reads eBay's whole active list. Any listing fbc has
that isn't in it is marked Ended (the eBay Listings list shows a column, and
can filter on it), and fbc remembers when the sync last finished. If a page
doesn't come back, or the listings don't add up to the count eBay reports, the
sync fails and changes nothing, so a bad answer can't mark listings ended.
Run it again.

The home page shows one line: the active listings as of that sync, and this
month's free insertions used so far, how many of the limit are left, the
renewals still due before the month ends, the projected total, and when the
sync last finished (in your timezone, not Pacific like the month's count). The limit is 250
unless `EBAY_SETTINGS['LISTING']['FREE_INSERTIONS']` in `config.py` says
otherwise; a Store subscription raises it. The page warns when the projected total
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

### Pictures on a Unit

Every Unit has a **Pictures** page (#140): the Pictures link in the Units list's
Links column, the Pictures button on Edit Unit, or the link on an eBay Draft. It
is where to add pictures before there is a draft, and where to take them to
Discogs. They are the same pictures an eBay Draft uses, so what you add here
shows on the draft and what you add there shows here. The page has the same
add, Make main, Remove and Rotate left and right as the draft page; see
[Pictures on an eBay Draft](#pictures-on-an-ebay-draft) for what each does.

**Taking them to Discogs.** fbc can't send pictures to Discogs, because its API
has no photo upload, so they go in by hand, on the Discogs listing: Edit Details,
then Add Photos. The Pictures page helps two ways:

- Each picture is shown at its real size (not a thumbnail, which would upload
  a thumbnail), the right way up, and under a real name such as
  `unit-123-1.jpg`. The number is its place, so `-1` is the main picture. Drag
  one onto Add Photos, or press its Download button.
- **Download all (zip)** gives `unit-123-pictures.zip` with every picture under
  those names, in order. Unzip it and drag the files onto Add Photos from a
  folder, which every browser accepts.

Dragging a picture straight from the page onto Add Photos was tried on a real
Discogs listing: it works in Chrome and not in Firefox, and Safari hasn't been
tried. As far as I can tell this is a limit of Firefox, not something the page
can change. Chrome lets a page offer a dragged picture as a file (a
non-standard feature Firefox hasn't built; Mozilla's bug 570164 asks for it),
and Firefox doesn't let a page attach a file to a drag. I haven't tested a
workaround in Firefox. There, use Download or the zip and drag the files from a
folder, or press Add Photos and choose them, which every browser accepts. The
zip and the page use the shrunk 2048 pixel copies, not the originals.

### Pictures on an eBay Draft

A Unit's pictures are kept by fbc, on the server, not only on eBay (#140).
They belong to the Unit, so a Discogs listing can use the same ones later;
an eBay Draft shows and uses its Unit's pictures. For each picture fbc
keeps the file you added and a copy shrunk to 2048 pixels on the long side,
which is what goes to eBay. The folder they're in is `PICTURES_DIR`; without
it the draft page says pictures can't be kept (see
[Running fbc in production](production.md)).

Add pictures with the file chooser on the draft page and Add pictures. Files
you choose show as small previews with Rotate left and Rotate right buttons, so
a sideways or upside down photo can be turned first. The turning happens in
your browser when you press Add pictures. A file the browser can't show has no
buttons and is added as it is. A photo that is only upright through its phone's
orientation tag is turned upright when it's kept, so it doesn't come out
sideways. A file that isn't a picture is refused with a message and the others
are still added. There can be up to 24.

Nothing goes to eBay when you add a picture. **Check with eBay** and **Publish
to eBay** first send eBay any picture it doesn't have, and then list the
draft. A picture that fails to send stops the check or publish, with eBay's
reason, and the ones that did send aren't sent again next time. fbc remembers
the address eBay gives each picture, but only for 30 days, because it isn't known
how long eBay keeps a picture no listing uses; an older one is sent
again, which is harmless.

Each picture has Make main, Remove and Rotate left and Rotate right buttons.
Rotate turns the stored files 90 degrees (each turn is saved again at high
quality, so a picture turned many times loses a little), and the turned
picture is sent to eBay next time. Remove deletes the picture from the Unit,
for every marketplace, and its files from the folder. These change drafts
only, not listings that are already published. Adding, turning and removing
pictures all work in Dev Mode, since they stay on this machine; only sending
to eBay is turned off.

#### Copying pictures down from eBay listings

Units listed before fbc kept pictures have none stored. `flask pictures-import`
copies each eBay listing's pictures down to the Units linked to it, active
or ended, so they can be used again, such as for a Discogs listing (see
[Running fbc in production](production.md#pictures) for running it on the live
server). It reads each listing's pictures from eBay, fetches the files, and
keeps them as the Unit's own, in the listing's order:

```bash
flask --app fbc pictures-import
flask --app fbc pictures-import --apply
```

The first only reads the listings and says what it would do. Things to know:

- A Unit that already has pictures is left alone, so it is safe to run again
  and a second run only does what the first couldn't. Every Unit of a listing
  with several gets the same pictures.
- Sold Units are left out, since they can't be listed again; `--include-sold`
  adds them. Mystery boxes have no Units linked, so they have nothing to copy
  to.
- A listing is all or nothing. If eBay can't be read for one, or one of its
  pictures can't be fetched or isn't a picture, that listing is skipped with
  the reason and nothing of it is kept, so the next run tries it again.
  eBay only tells about an ended listing for a while after it ends, so an old
  ended one may be unreadable.
- `--limit 20` reads at most that many listings, and `--item 123456789012` does
  one. It is one eBay call for each listing, and one for each picture.
- The copies have no eBay address saved, so publishing a draft for the Unit
  sends eBay the stored file, not the old listing's address.
- The pictures come from the address eBay gives, which may not be the largest
  size the seller originally uploaded.
- Dev Mode refuses, as it does for every eBay call.

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

### Ship This for a Discogs order

The Ship This link on a Discogs Order (the Discogs Orders list, or Discogs
Orders to Ship) does for it what Ship This does for an eBay order, in one
Sales Receipt for the order:

- It finds the Units from the order's items. Every item must link to a Unit
  that isn't sold yet; if not, it says which and links to adding the Sales
  Receipt by hand. A cancelled order can't be shipped.
- **Net Sold** starts as Discogs's total (items, shipping and any tax) less its
  selling fee. Discogs's API has no payment processing fee or label cost, so
  change it to what you really kept. The Sales Receipt shows the sale and
  Discogs's fee as lines, and any difference as a manual adjustment.
- Sold Price is the items' prices, without shipping, as for eBay.
- It takes the supplies you used out of stock and can remove each Unit's own
  copy from the Discogs collection, as for eBay.
- It marks the Units sold. It does **not** change the order on Discogs:
  invoice it and mark it shipped there, and the next sync shows the status.
- Discogs orders aren't combined: Discogs already puts everything one buyer
  bought in one order.
- If a Unit on the order is still listed on the other marketplace (or fbc
  couldn't end it), the page warns you at the top. The eBay Ship This page
  does the same for a Discogs listing.

Sales Receipts, the Sales Receipts list, a Unit's page and the home page's
Sales by marketplace table all show Discogs sales next to eBay's. A Sales
Receipt of a Discogs order has no Refresh Earnings: there is nothing more to
ask Discogs.

### Sales in person, and Notes on a Sales Receipt

A sale in person has no eBay or Discogs order, so add its Sales Receipt by hand
(Sales Receipts, then Add) and link its Units to it. A Sales Receipt has a free
text Notes field on its forms, its page and in the search, for what the receipt
can't say itself, such as "sold in person to a friend".

The home page's Sales by marketplace table always has eBay and Discogs rows,
even with no sales. Its "Other" row counts the Sales Receipts with no
order of either kind, and shows only when there are some. Each row links to the
Sales Receipts list filtered to it, archived ones included, as the table counts
them. In the search those filters are "Has an order" under eBay Orders, "Has an
order" under Discogs Orders and "Has no order" under eBay Orders. A Sales
Receipt is for one marketplace: the form refuses one with both an eBay order
and a Discogs order.

### Selling on both eBay and Discogs

A Unit can be listed on eBay and Discogs at once. When it sells on one, fbc
ends the other listing by itself, right after an orders sync saves the sale
(the scheduler runs them every 10 minutes for eBay and 15 for Discogs):

- **Sold on eBay**: the Unit's Discogs listing is deleted, whether it is For
  Sale or a Draft.
- **Sold on Discogs**: the Unit's eBay listing is ended, and an eBay Draft that
  hasn't been published is deleted.
- Every try is kept in the Ended Listings list (Sales menu): the Unit, the
  order that sold it, what was ended and what happened. Ended Listings is
  also where to look to see what fbc did.
- It only counts a sale made while the other listing was up, so an old order
  never ends a listing you made after a return. An order cancelled on Discogs
  ends nothing.
- A listing with several Units on it (an eBay listing with a quantity) is left
  alone until every one of its Units has sold, since the order doesn't say
  which Unit went. Mystery boxes pick their Units at Ship This, so they
  aren't ended either.
- If Discogs or eBay refuses, or fbc isn't connected, the sale is still
  recorded as usual. The failure shows as a row in Ended Listings with
  status failed, a red "Sold twice?" banner on the home page, and Listings to
  End by Hand under Tasks. fbc tries again after every orders sync, and the
  task clears once the listing is gone (ended by fbc, by you, or on its own).
- A listing that is already gone counts as ended.
- In Dev Mode nothing is ended, and the log says so.

This can't be airtight. Both sites can sell the Unit between two syncs, so it
shrinks the chance of selling a Unit twice and doesn't close it. If an eBay
order comes in, run Sync on eBay Orders by hand, as before, to end the
Discogs listing sooner. Discogs allows far more requests than the orders sync uses, so
its interval can be cut with the scheduler settings in [Running fbc in production](production.md) if the
window matters.

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

### Other Items

Not everything sold on eBay is a record: your own things and estate-sale finds
sell there too. The eBay listings sync reads each listing's eBay category.
Music > Vinyl Records is a record's, which gets a Unit as always. Any other
category makes an **Other Item** (Sales > Other Items), with the listing's
title as its name. Other Items are kept apart from records:

- Their listings and orders leave the eBay Listings and eBay Orders lists. An
  order for one still shows on Orders to Ship, until it's shipped.
- They are never in record stock, cost or profit, or in the Sales by
  marketplace table on the home page. The Other items line has what they sold:
  how many, the Sold Price and Net Sold of their Sales Receipts, and a profit.
- Cost is blank, and stays blank unless you enter it on the Item. With no cost
  an Item has no profit. Enter a cost later and its sale joins the profit.
  A Sales Receipt for several Items has a profit once every Item on it has a
  cost.
- Shipping supplies used on an Other Item's sale come out of the home page's
  Supply cost and are taken off that sale's profit, so they don't lower record
  profit. This only works for supplies with a Cost Per Use on the Supply List:
  one without stays in the shop-wide supply cost.
- Lot costs aren't split between records and Other Items. An Other Item can
  also be added by hand.

A listing that has a Unit, an eBay Draft or is a mystery box stays a record's
whatever its category, so a record listed in the wrong category doesn't vanish.
The sync only needs eBay's category for a new listing. If it can't be read, the
listing stays a record's until `flask --app fbc sort-other-items` reads it.

**Ship This** on an Other Item's order records its Sales Receipt, marks the
Item sold and takes the supplies out of stock, with no Units and no Discogs
step. It offers every supply set as Used For shipping (the label roll, packing
tape), with none filled in: enter how many were used, since a record's size
doesn't apply. Other packaging can be added as ordinary Supplies. An order that
mixes records and Other Items can't be shipped this way: Ship This says so and
links to adding the Sales Receipt by hand. Orders of Other Items aren't
combined into one package.

An order whose listing has no Unit, no eBay Draft and isn't a mystery box, and
isn't an Other Item yet (an old order that was left alone, even if eBay's listing is long gone), gets a page on Ship
This that offers to make an Other Item of it, with no cost (and adds the listing back, ended and archived, if fbc no longer has it; an order from before fbc kept items, which knows no listing, gets one made for it from the order), and then ship it.
If it is a record, it links to adding the Sales Receipt by hand instead.

#### Sorting the listings already there

Run this once after updating, and again if a listing couldn't be read:

```bash
flask --app fbc sort-other-items
flask --app fbc sort-other-items --apply
```

It reads the category of each listing that has none (one eBay call each, so
use `--limit` to do some at a time), and with `--apply` makes an Other Item
for each one outside Vinyl Records. Orders for them are already in fbc, and
now show on Other Items. An order that already has a Sales Receipt, made by
hand, marks its Item sold with that receipt, unless a Unit is on the receipt
too. Without `--apply` it only says what it would do. It is safe to run again:
a listing that is sorted is skipped, and no listing gets two Items. It does
nothing in Dev Mode, since it reaches eBay.

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

## Reports

The Reports menu shows money for a chosen period, where the home page shows
all time. It's being built in slices (issue #69); so far there is Profit &
Loss.

### The period

Pick a preset (this month, last month, quarter to date, year to date, last
year, last 12 months or all time) or a From and To date, both included, and
group the columns by month or quarter. The period is in the page's address
(`?period=ytd`, or `?from=2026-01-01&to=2026-06-30&group=quarter`), so a
report can be bookmarked. The year is the calendar year. A date that isn't
understood falls back to year to date, with a note.

### Profit & Loss

- **Sales** for the period, split into eBay, Discogs and Other (Sales Receipts
  with no order): how many, Sold For, Fees, Fee % and Net Sold, and the cost
  of the Units sold.
- **Profit**, worked out two ways side by side. **Cost of goods** is Net Sold
  less the cost of the Units sold in the period (each Unit's share of its
  Purchase Lot's price, shared evenly), supplies and Expenses. It leads,
  because it asks whether what sold, sold at a profit, so buying a big Lot in
  December doesn't make the year look like a loss. **Cash** is Net Sold less
  Purchase Lots bought, supplies bought and Expenses, each on its own date.
  Over all time, cash profit is the home page's net profit.
- **Expenses** by category, and the figures **by month or quarter**.
- **Other Items** are never in the record figures. Their sales are shown apart,
  with a profit only for those that have a cost.

A Sales Receipt with no Units still counts in the sales, as in the home
page's Sales by marketplace, so the report can differ from the home page's
Net profit (which adds up Units) by that receipt's Net Sold.

A Unit's cost is recomputed from its Lot as it is now, so adding a Unit to an
old Lot changes the past periods' cost of goods. Archived records count.
