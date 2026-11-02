import datetime

from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import Unit


def test_ebay_listing_repr_combines_id_title_price():
    listing = EbayListing(item_id="123", title="A Record", price=19.99)
    assert repr(listing) == "123: A Record $19.99"


def test_ebay_listing_fmt_price_formats_currency():
    listing = EbayListing(item_id="123", title="A Record", price=19.99)
    assert listing.fmt_price() == "$19.99"


def test_ebay_listing_fmt_url_renders_link():
    listing = EbayListing(
        item_id="123", title="A Record", price=19.99, url="https://ebay.com/x"
    )
    html = str(listing.fmt_url())
    assert 'href="https://ebay.com/x"' in html
    assert "Listing" in html


def test_ebay_order_repr_includes_title_when_present():
    order = EbayOrder(
        order_id="111",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=25.0,
        title="A" * 50,
    )
    text = repr(order)
    assert "111" in text
    assert order.title[:45] in text
    assert "buyer1" in text
    assert "$25.00" in text


def test_ebay_order_repr_without_title():
    order = EbayOrder(
        order_id="111",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=25.0,
        title=None,
    )
    text = repr(order)
    assert "111" in text
    assert "buyer1" in text
    assert "[" not in text


def test_ebay_order_fmt_url_renders_order_link():
    order = EbayOrder(
        order_id="111",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=25.0,
        title="Item",
    )
    html = str(order.fmt_url())
    assert "orderid=111" in html


def test_add_receipt_url_fills_in_the_order(app):
    order = EbayOrder(
        order_id="111",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=25.0,
        title="Item",
    )
    url = order.add_receipt_url()
    assert "sold_price=25.0" in url
    assert "ebay_orders=111" in url


def test_ebay_order_links_are_just_ship_this(app, db):
    listing = EbayListing(
        item_id="L1", title="Item", price=25.0, url="https://ebay.com/x"
    )
    db.session.add(listing)
    db.session.add(Unit(name="Unit", ebay_listing=listing))
    order = EbayOrder(
        order_id="111",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=25.0,
        title="Item",
        ebay_listing=listing,
    )
    db.session.add(order)
    db.session.commit()

    assert str(order.links()) == (
        '<a href="/ebayordermodelview/ship/111">Ship&nbsp;This</a>'
    )


def test_title_of_an_order_of_several_items_lists_them(app, db):
    from fbc.models.ebay import EbayOrderItem

    order = EbayOrder(
        order_id="111",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=25.0,
        title="First <LP>",
        items=[
            EbayOrderItem(ebay_listing_id="L1", title="First <LP>", quantity=1),
            EbayOrderItem(ebay_listing_id="L2", title="Second", quantity=2),
        ],
    )
    one = EbayOrder(
        order_id="222",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=5.0,
        title="Only",
        items=[EbayOrderItem(ebay_listing_id="L3", title="Only", quantity=1)],
    )

    assert str(order.fmt_title()) == (
        "<b>3 Units:</b><br>First &lt;LP&gt;<br>Second &times;2"
    )
    assert one.fmt_title() == "Only"
