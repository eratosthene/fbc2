from fbc.util import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.discogs import (
    Artist,
    DiscogsRelease,
    Folder,
    Genre,
    Style,
)
from fbc.widgets import (
    DiscogsReleaseListWidget,
)
from fbc.views.inventory import UnitModelView


class DiscogsReleaseModelView(CustomModelView):
    datamodel = SQLAInterface(DiscogsRelease)
    base_order = ('date_added', 'desc')
    list_widget = DiscogsReleaseListWidget
    related_views = [UnitModelView]
    list_columns = [
        "date_added",
        "title",
        "artists",
        "year",
        "master_year",
        "genres",
        "styles",
        "purchase_lot",
        "link_column",
    ]
    label_columns = {"link_column": "Links"}
    edit_columns = [
        "title",
        "artists",
        "year",
        "master_id",
        "master_year",
        "genres",
        "styles",
        "discogs_instances",
        "released",
        "folder",
    ]


class ArtistModelView(CustomModelView):
    datamodel = SQLAInterface(Artist)
    related_views = [DiscogsReleaseModelView]
    list_columns = [
        "name",
        "sort_name",
        "artist_id",
    ]
    related_views = [DiscogsReleaseModelView]


class GenreModelView(CustomModelView):
    datamodel = SQLAInterface(Genre)
    base_order = ("name", "desc")
    related_views = [DiscogsReleaseModelView]


class StyleModelView(CustomModelView):
    datamodel = SQLAInterface(Style)
    base_order = ("name", "desc")
    related_views = [DiscogsReleaseModelView]


class FolderModelView(CustomModelView):
    datamodel = SQLAInterface(Folder)
    list_columns = ["name", "folder_id"]
    base_order = ("name", "desc")
    related_views = [DiscogsReleaseModelView]
