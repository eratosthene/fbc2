import os
from flask_appbuilder.security.manager import (
    AUTH_OID,
    AUTH_REMOTE_USER,
    AUTH_DB,
    AUTH_LDAP,
    AUTH_OAUTH,
)

basedir = os.path.abspath(os.path.dirname(__file__))

# Your App secret key
SECRET_KEY = "1234567890"

# The SQLAlchemy connection string.
# SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(basedir, "app.db")
SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://user:password@host/db'
SQLALCHEMY_TRACK_MODIFICATIONS = False
# SQLALCHEMY_ECHO = True
# SQLALCHEMY_DATABASE_URI = 'postgresql://root:password@localhost/myapp'

# Flask-WTF flag for CSRF
CSRF_ENABLED = True

# ------------------------------
# GLOBALS FOR APP Builder
# ------------------------------
# Uncomment to setup Your App name
APP_NAME = "FBC"

# Uncomment to setup Setup an App icon
# APP_ICON = "static/img/logo.jpg"

# ----------------------------------------------------
# AUTHENTICATION CONFIG
# ----------------------------------------------------
# The authentication type
# AUTH_OID : Is for OpenID
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

# Uncomment to setup OpenID providers example for OpenID authentication
# OPENID_PROVIDERS = [
#    { 'name': 'Yahoo', 'url': 'https://me.yahoo.com' },
#    { 'name': 'AOL', 'url': 'http://openid.aol.com/<username>' },
#    { 'name': 'Flickr', 'url': 'http://www.flickr.com/<username>' },
#    { 'name': 'MyOpenID', 'url': 'https://www.myopenid.com' }]
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

# ebay settings
EBAY_SETTINGS = {
    'APP_ID': '',
    'DEV_ID': '',
    'CERT_ID': '',
    'USER_TOKEN': ''
}

# discogs settings
DISCOGS_SETTINGS = {
    'USER_TOKEN': ''
    'USER_AGENT': 'discogs_exporter/1.0'
}

