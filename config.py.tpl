import os
from flask_appbuilder.security.manager import (
    AUTH_REMOTE_USER,
    AUTH_DB,
    AUTH_LDAP,
    AUTH_OAUTH,
)

basedir = os.path.abspath(os.path.dirname(__file__))

# Any setting here can be overridden by an environment variable named
# FBC_CONFIG_<KEY>. Its value is read as JSON when it can be (true, 3,
# "text"), and a double underscore reaches into a dict. On the live server
# they go in docker/.env, which keeps the secrets out of this file:
#   FBC_CONFIG_SECRET_KEY='...'
#   FBC_CONFIG_EBAY_SETTINGS__APP_ID='...'
# See "Settings from the environment" in docs/production.md.

# Your App secret key
SECRET_KEY = "1234567890"

# The SQLAlchemy connection string.
# SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(basedir, "app.db")
# docker/compose.dev.yml sets SQLALCHEMY_DATABASE_URI in the environment
# to point at its own database; otherwise this one is used.
SQLALCHEMY_DATABASE_URI = os.environ.get(
    "SQLALCHEMY_DATABASE_URI", 'mysql+pymysql://user:password@host/db'
)
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Lets the scripts in dev-scripts/ add fake eBay and Discogs records to the
# database, for testing without the real sites. Only ever set this in a dev
# config.
# ALLOW_FAKE_DATA = True

# Dev mode keeps the app away from the live eBay and Discogs accounts: their
# keys here and the tokens saved in the database go unused, syncs do nothing,
# and picture uploads, publishing and Connect eBay/Discogs are turned off.
# FBC_DEV_MODE=1 in the environment also turns it on (docker/compose.dev.yml
# sets it); either one is enough, and neither turns the other off.
DEV_MODE = False
# SQLALCHEMY_ECHO = True
# SQLALCHEMY_DATABASE_URI = 'postgresql://root:password@localhost/myapp'

# Flask-WTF flag for CSRF
CSRF_ENABLED = True

# ------------------------------
# GLOBALS FOR APP Builder
# ------------------------------
# Uncomment to setup Your App name
APP_NAME = "FBC"

# The timezone times are shown in, as an IANA name (not "CST": a name follows
# daylight saving). fbc stores UTC; this only changes what pages and logs
# show. Typed dates, such as a Sales Receipt's, are not converted. Defaults to
# America/Chicago.
TIMEZONE = "America/Chicago"

# Uncomment to setup Setup an App icon
# APP_ICON = "static/img/logo.jpg"

# ----------------------------------------------------
# AUTHENTICATION CONFIG
# ----------------------------------------------------
# The authentication type
# AUTH_OAUTH : Is for OAuth 2.0 / OpenID Connect
# AUTH_DB : Is for database (username/password()
# AUTH_LDAP : Is for LDAP
# AUTH_REMOTE_USER : Is for using REMOTE_USER from web server
AUTH_TYPE = AUTH_DB

# Uncomment to setup Full admin role name
# AUTH_ROLE_ADMIN = 'Admin'

# Uncomment to setup Public role name, no authentication needed
# AUTH_ROLE_PUBLIC = 'Public'

# Will allow user self registration
AUTH_USER_REGISTRATION = False

# The default user self registration role
# AUTH_USER_REGISTRATION_ROLE = "Public"

# When using LDAP Auth, setup the ldap server
# AUTH_LDAP_SERVER = "ldap://ldapserver.new"

# ---------------------------------------------------
# Babel config for translations
# ---------------------------------------------------
# Setup default language
BABEL_DEFAULT_LOCALE = "en"
# Your application default translation path
BABEL_DEFAULT_FOLDER = "translations"
# The allowed translation for you app
# LANGUAGES = {
#     "en": {"flag": "gb", "name": "English"},
#     "pt": {"flag": "pt", "name": "Portuguese"},
#     "pt_BR": {"flag": "br", "name": "Pt Brazil"},
#     "es": {"flag": "es", "name": "Spanish"},
#     "de": {"flag": "de", "name": "German"},
#     "zh": {"flag": "cn", "name": "Chinese"},
#     "ru": {"flag": "ru", "name": "Russian"},
#     "pl": {"flag": "pl", "name": "Polish"},
# }
# ---------------------------------------------------
# Image and file configuration
# ---------------------------------------------------
# The file upload folder, when using models with files
UPLOAD_FOLDER = basedir + "/app/static/uploads/"

# The image upload folder, when using models with images
IMG_UPLOAD_FOLDER = basedir + "/app/static/uploads/"

# The image upload url, when using models with images
IMG_UPLOAD_URL = "/static/uploads/"
# Setup image size default is (300, 200, True)
# IMG_SIZE = (300, 200, True)

# Where the Discogs and eBay syncs keep their logs while they run in the
# background. Every web server process must see the same folder. The default
# is fbc-sync-jobs in the system temp folder.
# SYNC_JOB_FOLDER = "/tmp/fbc-sync-jobs"

# The scheduler runs in the live container and keeps the syncs fresh (see
# "Scheduled syncs" in docs/production.md). SCHEDULER = False turns it off. It's
# always off in dev mode. The intervals are minutes between one sync finishing
# and the next being due (at least 1); a sync that failed waits the retry delay.
# SCHEDULER = True
# SCHEDULER_EBAY_ORDERS_MINUTES = 15
# SCHEDULER_EBAY_LISTINGS_MINUTES = 30
# SCHEDULER_DISCOGS_MINUTES = 120
# SCHEDULER_DISCOGS_LISTINGS_MINUTES = 30
# SCHEDULER_RETRY_MINUTES = 10

# Supplies > Expenses. The categories an expense can have, as
# {key: label}; the key is what's stored, so keep it once expenses use it.
# Left out, the list in fbc/models/expenses.py is used, named after
# Schedule C's lines. Check it with whoever does the taxes.
# EXPENSE_CATEGORIES = {
#     "advertising": "Advertising (line 8)",
#     "car": "Car and truck (line 9)",
#     "other": "Other (line 27a)",
# }

# The IRS standard mileage rate, in dollars per mile, for an expense entered
# as Miles. It changes every year (irs.gov, "standard mileage rates"); each
# expense keeps the rate it was entered with.
MILEAGE_RATE = 0.725

# Theme configuration
# these are located on static/appbuilder/css/themes
# you can create your own and easily use them placing them on the same dir structure to override
# APP_THEME = "bootstrap-theme.css"  # default bootstrap
# APP_THEME = "amelia.css" # ridiculous
# APP_THEME = "cerulean.css" # good but needs some color fixing
# APP_THEME = "cosmo.css" # eh
APP_THEME = "cyborg.css" # not too bad
# APP_THEME = "darkly.css" # eh
# APP_THEME = "flatly.css" # eh
# APP_THEME = "journal.css" # eh
# APP_THEME = "lumen.css" # drop shadows are nice, allcaps buttons
# APP_THEME = "paper.css" # better drop shadows, allcaps buttons
# APP_THEME = "readable.css" # very simple but readable
# APP_THEME = "sandstone.css" # gross colors, allcaps buttons
# APP_THEME = "simplex.css" # gross colors
# APP_THEME = "slate.css" # not too bad
# APP_THEME = "solar.css" # gross colors
# APP_THEME = "spacelab.css" # good but needs some color fixing
# APP_THEME = "superhero.css" # gross colors
# APP_THEME = "united.css" # gross colors
# APP_THEME = "yeti.css" # weird font sizes

# Archiving (`flask archive`) hides old, finished records from the lists, and
# only the lists: the home page and every money figure still count them. Each
# is how many days old a record must be: Units that are sold, eBay Listings
# whose Units are all sold, shipped eBay Orders with no return or refund
# open, and Sales Receipts. A missing key means 365; 0 or None turns that one
# off. See "Archiving" in docs/using.md.
ARCHIVE_AFTER_DAYS = {
    'UNITS': 365,
    'EBAY_LISTINGS': 365,
    'EBAY_ORDERS': 365,
    'SALES_RECEIPTS': 365,
}

# ebay settings
EBAY_SETTINGS = {
    'APP_ID': '',
    'DEV_ID': '',
    'CERT_ID': '',
    # fbc signs in to eBay with OAuth. Set RUNAME to the app's eBay Redirect
    # URL name (developer.ebay.com > User Tokens), then use Sales > Connect
    # eBay; the token it gets is kept in the database. OAUTH_REFRESH_TOKEN, if
    # set, is used only until then.
    'RUNAME': '',
    'OAUTH_REFRESH_TOKEN': '',
    # Used when publishing a Unit as a listing (eBay Drafts). See
    # Sales > eBay Drafts > Shipping & Returns for the values to use.
    'LISTING': {
        'CATEGORY_ID': '176985',  # Music > Vinyl Records
        # Without business policies: an existing listing whose shipping,
        # returns and handling time new listings copy.
        'TEMPLATE_ITEM_ID': '',
        # With business policies: the three policy IDs (these win over
        # TEMPLATE_ITEM_ID when all are set).
        'SHIPPING_PROFILE_ID': '',
        'RETURN_PROFILE_ID': '',
        'PAYMENT_PROFILE_ID': '',
        'POSTAL_CODE': '',
        'LOCATION': '',
        'BEST_OFFER': True,
        # Free insertions eBay gives you a month (250 without a Store
        # subscription; a Store raises it). The home page counts this
        # month's against it, and publishing warns before going over.
        'FREE_INSERTIONS': 250,
        # Starting package weight for a draft, in ounces packed per disc, by
        # record size: a 12" double LP starts at 2 x 16 = 32 oz (eBay needs a
        # weight for calculated shipping). Optionally also 'LENGTH', 'WIDTH',
        # 'DEPTH' in inches and a 'TYPE' such as 'PackageThickEnvelope'.
        'PACKAGE': {
            'WEIGHT_OZ': {'12"': 16, '10"': 16, '7"': 8},
        },
        # Item specifics added to every listing when fbc can't work them out.
        'DEFAULT_SPECIFICS': {
            'Format': ['Record'],
            'Material': ['Vinyl'],
            'Case Type': ['Cardboard Sleeve'],
            'Color': ['Black'],
        },
    },
}

# discogs settings
DISCOGS_SETTINGS = {
    # Your Discogs app's keys (discogs.com > Settings > Developers), then use
    # Discogs > Connect Discogs; the token it gets is kept in the database.
    'CONSUMER_KEY': '',
    'CONSUMER_SECRET': '',
    # A personal access token, used only until Connect Discogs is done.
    'USER_TOKEN': '',
    'USER_AGENT': 'discogs_exporter/1.0',
    # Whether List on Discogs starts with Allow offers ticked (it can be
    # changed on each listing). On if left out, as eBay's BEST_OFFER is.
    'ALLOW_OFFERS': True,
}

