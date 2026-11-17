"""A signed-in user's home page layout (#214)."""

from flask_appbuilder import Model
from sqlalchemy import JSON, Column, ForeignKey, Integer


class HomeLayout(Model):
    """
    How one user has arranged the home page's cards: `layout` is
    {"order": [card keys, every card, in their order], "hidden": [card keys]}.
    There is no row until the user changes something, and none after Reset to
    default. fbc/sales/layout.py reads and writes it. `user_id` is an
    Integer like ab_user.id, which MariaDB needs for the foreign key, and
    there is no relationship to the user, which is Flask-AppBuilder's model.
    """

    __tablename__ = "home_layout"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("ab_user.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    layout = Column(JSON, nullable=False)
