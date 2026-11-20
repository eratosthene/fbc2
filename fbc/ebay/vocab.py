"""The lookup tables for eBay listings: grades, genres, countries, sizes,
item specifics defaults and the like. No code and no imports from fbc."""

# Vinyl Records (Music > Vinyl Records) on ebay.com.
DEFAULT_CATEGORY_ID = "176985"


CONDITION_USED = 3000


# Item specifics that fbc can't work out from Discogs and that are the same on
# almost every listing. EBAY_SETTINGS["LISTING"]["DEFAULT_SPECIFICS"] in
# config.py replaces these.
DEFAULT_SPECIFICS = {
    "Format": ["Record"],
    "Material": ["Vinyl"],
    "Case Type": ["Cardboard Sleeve"],
    "Color": ["Black"],
}


# Goldmine grade abbreviations, as a Unit's grades store them, to the wording
# eBay uses for the Record Grading / Sleeve Grading specifics. This is also
# the wording Discogs uses for its Media/Sleeve Condition fields.
GRADES = {
    "M": "Mint (M)",
    "NM": "Near Mint (NM or M-)",
    "M-": "Near Mint (NM or M-)",
    "NM or M-": "Near Mint (NM or M-)",
    "EX": "Excellent (EX)",
    "VG+": "Very Good Plus (VG+)",
    "VG": "Very Good (VG)",
    "G+": "Good Plus (G+)",
    "G": "Good (G)",
    "F": "Fair (F)",
    "P": "Poor (P)",
}


# Discogs genres that eBay splits up.
GENRES = {
    "Funk / Soul": ["Funk", "Soul"],
    "Folk, World, & Country": ["Folk", "World", "Country"],
}


# Country codes (as Discogs and Unit.pressing use them) and adjectives, to
# the names eBay's Country of Origin uses. Anything else passes through.
COUNTRIES = {
    "US": "United States",
    "USA": "United States",
    "American": "United States",
    "UK": "United Kingdom",
    "GB": "United Kingdom",
    "British": "United Kingdom",
    "English": "United Kingdom",
    "CA": "Canada",
    "CAN": "Canada",
    "Canadian": "Canada",
    "JP": "Japan",
    "JPN": "Japan",
    "Japanese": "Japan",
    "DE": "Germany",
    "GER": "Germany",
    "German": "Germany",
    "FR": "France",
    "French": "France",
    "IT": "Italy",
    "Italian": "Italy",
    "NL": "Netherlands",
    "Dutch": "Netherlands",
    "AU": "Australia",
    "AUS": "Australia",
    "Australian": "Australia",
    "NZ": "New Zealand",
    "ES": "Spain",
    "Spanish": "Spain",
    "SE": "Sweden",
    "Swedish": "Sweden",
    "BR": "Brazil",
    "Brazilian": "Brazil",
    "MX": "Mexico",
    "Mexican": "Mexico",
    "EU": "Europe",
    "European": "Europe",
}


# Country names that are more than one word, so "West Germany Original"
# isn't read as a country called "West".
TWO_WORD_COUNTRIES = [
    "United States",
    "United Kingdom",
    "West Germany",
    "East Germany",
    "New Zealand",
    "South Africa",
    "South Korea",
]


# Words a Pressing can start with that aren't countries.
NOT_COUNTRIES = {
    "Original",
    "Reissue",
    "Repress",
    "Remaster",
    "Remastered",
    "First",
    "Second",
    "Early",
    "Late",
    "Later",
    "Promo",
    "Promotional",
    "Limited",
    "Club",
    "Test",
    "White",
    "Mono",
    "Stereo",
    "Unofficial",
    "Bootleg",
    "Deluxe",
    "Import",
}


SIZES = ['12"', '10"', '7"']


TYPES = ["LP", "EP", "Single", "Maxi-Single"]


CHANNELS = ["Stereo", "Mono", "Quadraphonic"]


COLORS = [
    "Red",
    "Blue",
    "Green",
    "Yellow",
    "White",
    "Clear",
    "Orange",
    "Purple",
    "Pink",
    "Gold",
    "Silver",
    "Grey",
    "Brown",
]


TITLE_MAX = 80


# A draft's starting package weight in ounces per disc, by record size,
# packing included: a 12" double LP starts at 32 oz. eBay needs a weight for
# calculated shipping. Replaced by EBAY_SETTINGS["LISTING"]["PACKAGE"] in
# config.py, which can also give LENGTH/WIDTH/DEPTH in inches and a
# ShippingPackage TYPE.
DEFAULT_PACKAGE = {"WEIGHT_OZ": {'12"': 16, '10"': 16, '7"': 8}}


# eBay's Type for an LP that's more than one disc.
MULTI_DISC_TYPES = {2: "Double LP", 3: "Triple LP"}
