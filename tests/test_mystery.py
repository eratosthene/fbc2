"""Mystery box listings: picking Units at Ship This, and shipping them (#74)."""

import random

from fbc import db, mystery, shipping
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import StorageBox, Unit
from fbc.models.sales import SalesReceipt
from fbc.util import relist_ebay_listing
from test_combined_shipping import (  # noqa: F401
    WHEN,
    _label,
    _order,
    _sale,
    finances,
)
from test_shipping import _shipping_supplies, client  # noqa: F401


def _box(name="Discount Bin", units=12):
    box = StorageBox(name=name)
    db.session.add(box)
    db.session.flush()
    for n in range(units):
        db.session.add(
            Unit(
                name=name + " record " + str(n),
                unit_type='12" LP',
                pressing="",
                grading="NM",
                storage_box=box,
                discogs_master="m" + name[:2] + str(n),
            )
        )
    db.session.commit()
    return box


def _mystery(box, item_id="900", count=5, price=25.0):
    listing = EbayListing(
        item_id=item_id,
        title="Mystery Box " + item_id,
        price=price,
        url="https://ebay.com/itm/" + item_id,
        pick_from_box_id=box.id,
        pick_count=count,
    )
    db.session.add(listing)
    db.session.commit()
    return listing


def _picked(page):
    import re

    return re.findall(r'name="pick-900" value="(\d+)"', page)


def test_boxes_available_counts_unsold_units_without_their_own_listing(app):
    box = _box(units=12)
    listing = _mystery(box)
    own = EbayListing(item_id="1", title="x", price=5.0, url="u")
    ended = EbayListing(item_id="2", title="y", price=5.0, url="u", ended=True)
    db.session.add_all([own, ended])
    units = Unit.query.order_by(Unit.id).all()
    units[0].sold = True
    units[1].ebay_listing_id = "1"
    units[2].ebay_listing_id = "2"
    db.session.commit()

    # 12, less one sold and one on an active listing; the ended one counts.
    assert len(mystery.available_units(box.id)) == 10
    assert listing.fmt_boxes_available() == 2
    assert [x.item_id for x in mystery.active_listings()] == ["900"]
    listing.ended = True
    db.session.commit()
    assert mystery.active_listings() == []


def test_choose_leaves_out_what_the_buyer_already_got(app):
    _box(units=6)
    units = Unit.query.all()
    seen = (set(), {units[0].discogs_master, units[1].discogs_master})

    for seed in range(20):
        picked = mystery.choose(units, 4, seen, rng=random.Random(seed))
        assert len(picked) == 4
        assert not {units[0], units[1]} & set(picked)
    # Repeats are only used when there's nothing else.
    assert len(mystery.choose(units, 6, seen)) == 6


def test_ship_this_picks_units_for_every_box_bought(client):  # noqa: F811
    box = _box(units=12)
    _mystery(box)
    _order("O-1", [("900", 2, 25.0)])

    page = client.get("/ebayordermodelview/ship/O-1").get_data(as_text=True)

    ids = _picked(page)
    assert len(ids) == 10 and len(set(ids)) == 10
    assert "Pull list: Mystery Box 900" in page
    assert "disabled>Ship</button>" not in page


def test_reroll_replaces_only_that_pick(client):  # noqa: F811
    box = _box(units=12)
    _mystery(box)
    _order("O-1", [("900", 1, 25.0)])
    first = _picked(client.get("/ebayordermodelview/ship/O-1").get_data(as_text=True))

    page = client.post(
        "/ebayordermodelview/ship/O-1",
        data={"pick-900": first, "action": "reroll:" + first[2]},
    ).get_data(as_text=True)

    after = _picked(page)
    assert first[2] not in after and len(after) == 5
    assert set(first) - {first[2]} <= set(after)
    assert db.session.query(SalesReceipt).count() == 0


def test_swap_in_a_unit_you_choose(client):  # noqa: F811
    box = _box(units=12)
    _mystery(box)
    _order("O-1", [("900", 1, 25.0)])
    first = _picked(client.get("/ebayordermodelview/ship/O-1").get_data(as_text=True))
    other = next(
        str(u.id) for u in Unit.query.order_by(Unit.id) if str(u.id) not in first
    )

    page = client.post(
        "/ebayordermodelview/ship/O-1",
        data={"pick-900": first, "swap-" + first[0]: other, "action": "update"},
    ).get_data(as_text=True)

    after = _picked(page)
    assert after[0] == other and after[1:] == first[1:]


def test_a_short_box_blocks_shipping_until_filled_from_another(client):  # noqa: F811
    box = _box(units=3)
    _mystery(box)
    spare = _box("Spare", units=4)
    _order("O-1", [("900", 1, 25.0)])

    page = client.get("/ebayordermodelview/ship/O-1").get_data(as_text=True)
    assert "2 Units" in page and "short" in page
    assert "disabled>Ship</button>" in page
    posted = {"pick-900": _picked(page), "net_sold": "10", "action": "ship"}
    client.post("/ebayordermodelview/ship/O-1", data=posted)
    assert db.session.query(SalesReceipt).count() == 0

    posted["fill-900"] = str(spare.id)
    page = client.post("/ebayordermodelview/ship/O-1", data=posted).get_data(
        as_text=True
    )
    assert len(_picked(page)) == 5
    assert "disabled>Ship</button>" not in page


def test_shipping_a_mystery_box_order(client, finances):  # noqa: F811
    box = _box(units=12)
    _mystery(box)
    _order("O-1", [("900", 2, 25.0)])
    supplies = _shipping_supplies()
    finances.orders["O-1"] = [_sale("O-1", 50.0, items=1), _label("O-1", 6.0)]
    page = client.get("/ebayordermodelview/ship/O-1").get_data(as_text=True)
    ids = _picked(page)

    shipped = client.post(
        "/ebayordermodelview/ship/O-1",
        data={
            "pick-900": ids,
            "net_sold": "44.00",
            "supply-" + str(supplies["mailers"].id): "2",
            "action": "ship",
        },
        follow_redirects=True,
    )

    assert "marked 10 Units sold" in shipped.get_data(as_text=True)
    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert (receipt.order_ids(), receipt.sold_price, receipt.net_sold) == (
        ["O-1"],
        50.0,
        44.0,
    )
    sold = Unit.query.filter(Unit.sales_receipt_id == receipt.id).all()
    assert sorted(str(u.id) for u in sold) == sorted(ids)
    assert all(u.sold and u.sold_price == 5.0 for u in sold)
    assert all(u.sold_for() == 5.0 for u in sold)
    assert mystery.boxes_available(db.session.get(EbayListing, "900")) == 0


def test_a_buyers_earlier_records_are_not_picked_again(client):  # noqa: F811
    box = _box(units=7)
    _mystery(box)
    earlier = Unit.query.order_by(Unit.id).limit(2).all()
    first = _order("O-0", [("900", 1, 25.0)])
    shipping.ship_order(first, earlier, 20.0)
    _order("O-1", [("900", 1, 25.0)])

    page = client.get("/ebayordermodelview/ship/O-1").get_data(as_text=True)

    assert not {str(u.id) for u in earlier} & set(_picked(page))


def test_relisting_keeps_a_mystery_box_and_its_orders(app):
    box = _box(units=6)
    old = _mystery(box)
    _order("O-1", [("900", 1, 25.0)])

    relist_ebay_listing(
        old,
        {
            "ItemID": "901",
            "Title": old.title,
            "BuyItNowPrice": {"value": "25.00"},
            "ListingDetails": {
                "ViewItemURL": "https://ebay.com/itm/901",
                "StartTime": "2026-11-01T10:00:00.000Z",
            },
        },
    )

    new = db.session.get(EbayListing, "901")
    assert (new.pick_from_box_id, new.pick_count) == (box.id, 5)
    assert db.session.get(EbayOrder, "O-1").listing_ids() == {"901": 1}


def test_the_listing_needs_both_box_and_count(client):  # noqa: F811
    box = _box(units=1)
    _mystery(box)

    page = client.post(
        "/ebaylistingmodelview/edit/900",
        data={"title": "t", "price": "5", "url": "u", "pick_count": "5"},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Set both the Storage Box and Units Per Sale" in page


def test_the_mystery_boxes_page_shows_boxes_available(client):  # noqa: F811
    box = _box(units=11)
    _mystery(box)

    page = client.get("/mysteryboxes/").get_data(as_text=True)

    assert "Mystery Box 900" in page
    assert "<td>2</td>" in page
    # It is in the Sales menu, and no longer on the home page.
    assert 'href="/mysteryboxes/"' in client.get("/").get_data(as_text=True)
    assert "Mystery boxes available" not in client.get("/").get_data(as_text=True)


def test_the_mystery_boxes_page_says_when_there_are_none(client):  # noqa: F811
    page = client.get("/mysteryboxes/").get_data(as_text=True)

    assert "No active mystery box listings" in page
