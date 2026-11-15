"""The supplies Ship This takes out of the Supply List: which are used, the
counts typed in the form, and what is short."""


def supplies_plan(units):
    """
    [(supply, count)] for every supply used for shipping: how many uses
    shipping `units` together normally takes (see Supply.shipping_count).
    """
    from fbc.ebay.describe import record_size
    from fbc.models.supplies import SHIPPING, Supply

    sizes = [record_size(u) for u in units]
    return [(s, s.shipping_count(sizes)) for s in Supply.used_for_list(SHIPPING)]


def supply_counts(form, supplies, prefix="supply-"):
    """
    {supply: count} from the `prefix`<id> fields of `form` for each of
    `supplies`, leaving out blanks and 0. Raises ValueError, with what to
    fix, for anything but a whole number of 0 or more.
    """
    counts = {}
    for supply in supplies:
        value = (form.get(prefix + str(supply.id)) or "").strip()
        if not value:
            continue
        try:
            count = int(value)
        except ValueError:
            count = -1
        if count < 0:
            raise ValueError(
                "Enter how many " + supply.name + " were used as a whole number."
            )
        if count:
            counts[supply] = count
    return counts


def stock_problems(counts):
    """
    What to fix for each of `counts` ({supply: count}) that uses more than
    the supply has left (see Supply.available), as messages linking to
    Reconcile Supplies; [] if none does. Stock can't really run below 0, so
    a shortfall means the Supply List is out of step with what's on hand.
    """
    from flask import url_for
    from markupsafe import Markup

    problems = []
    for supply, count in counts.items():
        left = supply.available()
        if count > left:
            link = url_for(
                "ReconcileSuppliesView.reconcile",
                supply=supply.id,
                _anchor="supply-" + str(supply.id),
            )
            problems.append(
                Markup(
                    "There aren't enough {0} in stock for this: it uses {1}, and"
                    " the Supply List has {2} left. Count what's really on hand"
                    ' and correct it on <a href="{3}">Reconcile Supplies</a>, or'
                    " change how many were used, then try again."
                ).format(supply.name, count, left, link)
            )
    return problems


def fmt_supplies(counts):
    """{supply: count} as "1 LP Mailers, 1 Shipping Label Roll"."""
    return ", ".join(str(n) + " " + s.name for s, n in counts.items())
