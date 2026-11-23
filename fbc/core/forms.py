import copy
import logging

from flask import redirect, request, session, url_for
from flask_wtf import FlaskForm
from flask_appbuilder import ModelView
from flask_appbuilder.models.filters import BaseFilter
from flask_appbuilder.models.sqla.filters import get_field_setup_query
from sqlalchemy import case, or_

logger = logging.getLogger()


class CustomForm(FlaskForm):
    """
    A custom FlaskForm which reads data from request params
    """

    @classmethod
    def refresh(cls, obj=None):
        kw = dict(obj=obj)
        if request.method == "GET":
            kw["formdata"] = request.args
            logger.info(request.args)
        form = cls(**kw)
        return form


class NullsLastMixin:
    """
    Sorts rows with nothing in the sort column last, in both directions.
    MariaDB and SQLite put them first when sorting up and last when sorting
    down, so a list sorted by Retail Price would start with the Units that
    have none. The flag is a CASE rather than NULLS LAST, which MariaDB
    doesn't have.
    """

    def apply_order_by(self, query, order_column, order_direction, *args, **kwargs):
        query = super().apply_order_by(
            query, order_column, order_direction, *args, **kwargs
        )
        clauses = tuple(getattr(query, "_order_by_clauses", ()))
        element = getattr(clauses[0], "element", None) if clauses else None
        if element is None:
            return query
        empty = case((element.is_(None), 1), else_=0)
        return query.order_by(None).order_by(empty, *clauses)


def nulls_last(datamodel):
    """
    A copy of a view's datamodel that sorts empty values last. A copy, since
    the Units views share one datamodel and sort differently.
    """
    if isinstance(datamodel, NullsLastMixin):
        return datamodel
    sorted_model = copy.copy(datamodel)
    sorted_model.__class__ = type(
        "NullsLast" + type(datamodel).__name__,
        (NullsLastMixin, type(datamodel)),
        {},
    )
    return sorted_model


class CustomModelView(ModelView):
    """
    A custom ModelView which overrides the various CRUD templates
    """

    # List columns Flask-AppBuilder can't sort, mapped to what to sort them by:
    # a column of a related model ("purchase_lot.date"), or a column of this
    # model worked out in SQL. The list column keeps its name; only the sort
    # link and the ORDER BY change. A column that is a method over a real
    # column is marked @renders on the model instead.
    sort_by = {}

    def _get_list_widget(self, *args, **kwargs):
        # Flask-AppBuilder passes the sort column on by name.
        column = kwargs.get("order_column")
        if column in self.sort_by:
            kwargs["order_column"] = self.sort_by[column]
        return super()._get_list_widget(*args, **kwargs)

    list_template = "crud/list.html"
    edit_template = "crud/edit.html"
    add_template = "crud/add.html"
    show_template = "crud/show.html"

    def list_url_came_from(self):
        """
        The list this view was last opened at in this session, with its
        filters and page, as a relative URL; the plain list if there is none.

        Flask-AppBuilder pushes every list and Show page it renders onto a
        short per-session history. get_redirect() isn't used because it
        returns the entry below the newest, which is the list only for one
        Show page: with Show open in two tabs it is the other Show. So this
        looks back through the history for this view's own list. Only the
        path and query are kept, so it can never leave the site.
        """
        from urllib.parse import urlsplit

        plain = url_for(self.endpoint + ".list")
        for url in reversed(session.get("page_history") or []):
            if not isinstance(url, str):
                continue
            parts = urlsplit(url)
            if parts.path == plain:
                return plain + ("?" + parts.query if parts.query else "")
        return plain

    def back_to_list(self):
        """Redirect to the list the owner came from: for the last step on an item."""
        return redirect(self.list_url_came_from())

    def _init_properties(self):
        """
        Makes every view searchable by its primary key. Flask-AppBuilder
        leaves primary keys out of the default search, and views that list
        their own search_columns mostly forgot the id. An integer id also
        gets FilterIdMatches, so a range like 100-200 works.
        """
        self.datamodel = nulls_last(self.datamodel)
        super()._init_properties()
        self.order_columns = list(self.order_columns) + [
            c for c in self.sort_by if c in self.list_columns
        ]
        pk = self.datamodel.get_pk_name()
        if not isinstance(pk, str) or pk not in self.datamodel.list_columns:
            return
        if pk not in self.search_columns:
            self.search_columns = [pk] + list(self.search_columns)
            self._filters = self.datamodel.get_filters(self.search_columns)
        if self.datamodel.is_integer(pk):
            # The tables hold filter instances, not classes.
            mine = FilterIdMatches(pk, self.datamodel)
            for table in (self._filters._search_filters, self._filters._all_filters):
                table[pk] = [mine] + [
                    f for f in table[pk] if not isinstance(f, FilterIdMatches)
                ]


class FilterIdMatches(BaseFilter):
    """
    Matches an integer id typed as "42", a range "100-200", or a
    comma-separated mix of both ("5, 100-200"). Anything that doesn't parse
    matches nothing, the same as Flask-AppBuilder's own integer filters.
    """

    name = "Matches id"
    arg_name = "idm"

    @staticmethod
    def parse(text):
        """Returns [(low, high), ...], or None if the text isn't valid."""
        spans = []
        for part in str(text).split(","):
            bounds = [b.strip() for b in part.split("-")]
            if len(bounds) > 2 or not all(b.isdigit() for b in bounds):
                return None
            low, high = int(bounds[0]), int(bounds[-1])
            spans.append((min(low, high), max(low, high)))
        return spans

    def apply(self, query, value):
        query, field = get_field_setup_query(query, self.model, self.column_name)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        spans = self.parse(value)
        if not spans:
            return query.filter(False)
        return query.filter(
            or_(
                *[
                    field == low if low == high else field.between(low, high)
                    for low, high in spans
                ]
            )
        )
