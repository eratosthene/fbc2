"""
Where each user's home page cards go (#214). A saved layout is the user's
order of every card plus the ones they hid; none saved means the default
order from the registry (fbc.sales.cards.CARDS). A card added to the registry
later is in no saved layout, so it is put in, visible, after the card that
comes before it in the default order. A key that is no longer a card is
ignored.

The model is imported inside the functions: see fbc/models/sync.py.
"""

from dataclasses import dataclass

from fbc.sales import cards


@dataclass(frozen=True)
class Layout:
    """`order` is every card key as the user has them; `hidden` is a set of keys."""

    order: tuple
    hidden: frozenset

    @property
    def visible(self):
        return [cards.BY_KEY[k] for k in self.order if k not in self.hidden]

    @property
    def hidden_cards(self):
        return [cards.BY_KEY[k] for k in self.order if k in self.hidden]

    def as_json(self):
        return {
            "order": list(self.order),
            "hidden": [k for k in self.order if k in self.hidden],
        }


def default():
    return Layout(tuple(c.key for c in cards.CARDS), frozenset())


def resolve(saved):
    """The Layout for a saved dict (or None, or anything unreadable)."""
    if not isinstance(saved, dict):
        return default()
    order = []
    saved_order = saved.get("order")
    saved_hidden = saved.get("hidden")
    if not isinstance(saved_order, list):
        saved_order = []
    if not isinstance(saved_hidden, list):
        saved_hidden = []
    for key in saved_order:
        if isinstance(key, str) and key in cards.BY_KEY and key not in order:
            order.append(key)
    hidden = {k for k in saved_hidden if isinstance(k, str) and k in cards.BY_KEY}
    known = set(order) | hidden
    for key in hidden - set(order):
        order.append(key)
    # Cards the layout has never seen: after the one before them by default.
    keys = [c.key for c in cards.CARDS]
    for i, key in enumerate(keys):
        if key in known:
            continue
        after = next((k for k in reversed(keys[:i]) if k in order), None)
        order.insert(order.index(after) + 1 if after else 0, key)
        known.add(key)
    return Layout(tuple(order), frozenset(hidden))


def for_user(user_id):
    """The user's Layout: the default for no user or no saved layout."""
    from fbc import db
    from fbc.models.home import HomeLayout

    if user_id is None:
        return default()
    row = db.session.query(HomeLayout).filter_by(user_id=user_id).first()
    return resolve(row.layout if row else None)


def save(user_id, layout):
    from fbc import db
    from fbc.models.home import HomeLayout

    row = db.session.query(HomeLayout).filter_by(user_id=user_id).first()
    if row is None:
        row = HomeLayout(user_id=user_id, layout=layout.as_json())
        db.session.add(row)
    else:
        row.layout = layout.as_json()
    db.session.commit()


def reset(user_id):
    """Delete the saved layout, so the user gets the default again."""
    from fbc import db
    from fbc.models.home import HomeLayout

    db.session.query(HomeLayout).filter_by(user_id=user_id).delete()
    db.session.commit()


class BadKey(ValueError):
    pass


def _check(keys):
    if not isinstance(keys, list) or any(
        not isinstance(k, str) or k not in cards.BY_KEY for k in keys
    ):
        raise BadKey("Not a card.")
    if len(set(keys)) != len(keys):
        raise BadKey("A card is listed twice.")


def reorder(user_id, shown_keys):
    """
    The visible cards were dragged into `shown_keys`' order. They take the
    places the same cards had, so hidden cards, and visible ones the page
    didn't show, keep theirs.
    """
    _check(shown_keys)
    current = for_user(user_id)
    if set(shown_keys) & current.hidden:
        raise BadKey("A hidden card can't be moved.")
    order = list(current.order)
    slots = [i for i, k in enumerate(order) if k in shown_keys]
    for slot, key in zip(slots, shown_keys):
        order[slot] = key
    save(user_id, Layout(tuple(order), current.hidden))


def set_hidden(user_id, key, hidden):
    _check([key])
    current = for_user(user_id)
    now = current.hidden | {key} if hidden else current.hidden - {key}
    save(user_id, Layout(current.order, frozenset(now)))
