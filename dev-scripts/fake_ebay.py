"""
Add fake eBay records to a dev database, in place of a sync from eBay.

  orders N    N orders, each with its own listing and an unsold Unit on it,
              so every one offers Ship This. The Unit is of a fake Discogs
              release and instance, so Ship This also offers to remove it
              from Discogs, unless --no-discogs.
  listings N  N listings, each with an unsold Unit on it, and no order.

--no-unit leaves the Unit out, for orders Ship This can't handle (it says
to ship them by hand). --items puts several items on each order, each
with its listing and Unit. --buyer gives every order the same buyer, so Ship
This offers to ship them combined. --size picks the record size, which decides the
outer sleeve Ship This suggests. Remove everything fake with clear_fake.py.

Run from the repo root, on this machine or in docker/compose.dev.yml's app:
  pipenv run python dev-scripts/fake_ebay.py orders 5
  docker compose -f docker/compose.dev.yml exec app python dev-scripts/fake_ebay.py orders 5
"""

import argparse

import _fake


def add(kind, count, size, unit=True, discogs=True, items=1, buyer=None):
    added = []
    for _ in range(count):
        listings = []
        units = []
        for _ in range(items if kind == "orders" else 1):
            item = None
            if unit:
                release = _fake.fake_release(size) if discogs else None
                item = _fake.fake_unit(release, size)
                units.append(item)
            listings.append(_fake.fake_listing(item))
        if kind == "orders":
            added.append((_fake.fake_order(listings, buyer), units))
        else:
            added.extend((listing, units) for listing in listings)
    _fake.db.session.commit()
    for record, units in added:
        print(
            "Added "
            + repr(record)
            + (
                " (Unit"
                + ("s " if len(units) > 1 else " ")
                + ", ".join(str(u.id) for u in units)
                + ")"
                if units
                else ""
            )
        )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("kind", choices=["orders", "listings"])
    parser.add_argument("count", type=int)
    parser.add_argument(
        "--size",
        choices=list(_fake.SIZES) + ["mix"],
        default="mix",
        help='record size of the Units (default: a mix, mostly 12")',
    )
    parser.add_argument(
        "--no-unit", action="store_true", help="don't put a Unit on the listings"
    )
    parser.add_argument(
        "--no-discogs",
        action="store_true",
        help="make the Units without a Discogs release or instance",
    )
    parser.add_argument(
        "--items", type=int, default=1, help="items on each order (default: 1)"
    )
    parser.add_argument("--buyer", help="give every order this buyer")
    args = parser.parse_args()
    _fake.setup()
    add(
        args.kind,
        args.count,
        args.size,
        not args.no_unit,
        not args.no_discogs,
        args.items,
        args.buyer,
    )


if __name__ == "__main__":
    main()
