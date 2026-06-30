import logging
from fbc.util import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface
from flask_appbuilder.models.sqla.filters import (
    get_field_setup_query,
)
from flask_appbuilder.models.filters import BaseFilter

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


logger = logging.getLogger()


class FilterNotInFunction(BaseFilter):
    name = "Filter view where field is not in a list returned by a function"
    arg_name = "not_inf"

    def apply(self, query, func):
        query, field = get_field_setup_query(query, self.model, self.column_name)
        return query.filter(~field.in_(func()))


def get_unit_releases():
    from fbc.models.inventory import Unit
    from fbc import db

    releases = [
        r.discogs_release_id
        for r in db.session.query(Unit)
        .filter(Unit.discogs_release_id.isnot(None))
        .distinct()
    ]
    return releases


class DiscogsReleaseModelView(CustomModelView):
    datamodel = SQLAInterface(DiscogsRelease)
    base_order = ("date_added", "desc")
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


class DiscogsReleaseModelNoUnitView(CustomModelView):
    datamodel = SQLAInterface(DiscogsRelease)
    base_filters = [["release_id", FilterNotInFunction, get_unit_releases]]
    base_order = ("date_added", "desc")
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
