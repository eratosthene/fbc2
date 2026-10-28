import sys
sys.path.insert(0, '..')
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import Unit
from fbc.models.sales import SalesReceipt
from fbc import db, app
from ebaysdk.exception import ConnectionError
from ebaysdk.trading import Connection as Trading
import logging
logging.basicConfig(format="%(asctime)s:%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.getLogger("ebaysdk").setLevel(logging.CRITICAL)

ended_listings = []

listings = db.session.query(EbayListing)
for l in listings:
    logger.info(l)
    lid = l.item_id
    o = db.session.query(EbayOrder).filter_by(ebay_listing_id=lid).first()
    if not o:
        logger.info("No order found!")
        u = db.session.query(Unit).filter_by(ebay_listing_id=lid).first()
        if not u:
            logger.info("No unit found, listing was ended, should be deleted.")
            ended_listings.append(l)
        else:
            logger.info(f"Found unit: {u}")
            srid = u.sales_receipt_id
            sr = db.session.query(SalesReceipt).filter_by(id=srid).first()
            if not sr:
                logger.info("No sales receipt found, need to check this listing!")
                ebayconfig = app.config["EBAY_SETTINGS"]
                try:
                    api = Trading(
                        debug=False,
                        config_file=None,
                        appid=ebayconfig["APP_ID"],
                        domain="api.ebay.com",
                        certid=ebayconfig["CERT_ID"],
                        devid=ebayconfig["DEV_ID"],
                        token=ebayconfig["USER_TOKEN"],
                    )

                    response = api.execute("GetItem",{"ItemID": lid})
                    logger.info("eBay response: " + api.response_status())
                    status = response.reply.Item.SellingStatus.ListingStatus
                    if status == 'Active':
                        logger.info("Listing is active, skipping.")
                except ConnectionError as e:
                    d = e.response.dict()
                    #logger.info(d)
                    #input()
                    ec = d['Errors']['ErrorCode']
                    if ec == '17':
                        logger.info("Listing was ended, should be deleted.")
                        ended_listings.append(l)
                    else:
                        logger.error(e)
                        input()

            else:
                logger.info(f"Found SR: {sr}")
                eoid = sr.ebay_order_id
                eo = db.session.query(EbayOrder).filter_by(order_id=eoid).first()
                if not eo:
                    if srid == 319:
                        logger.info("(KNOWN) Listing was ended, should be deleted.")
                        ended_listings.append(l)
                    else:
                        logger.error("No order found via SR, THIS SHOULD NOT HAPPEN!")
                        input()
                else:
                    logger.info(f"Order found via SR, skipping: {eo}")
    else:
        logger.info(f"Direct order found, skipping: {o}")

logger.info(f"Total listings to remove: {len(ended_listings)}")
for l in ended_listings:
    logger.info(l)
    u = db.session.query(Unit).filter_by(ebay_listing_id=l.item_id).update({'ebay_listing_id': None})
    db.session.flush()
    db.session.delete(l)
    db.session.commit()
