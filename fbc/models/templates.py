from flask_appbuilder import Model
from sqlalchemy import Column, String, Text

# Like fbc.models.sync: don't import this at the top of a module that
# fbc/__init__.py imports before Flask-AppBuilder starts. Import it inside the
# function that needs it.


class DescriptionTemplate(Model):
    """
    A description template the owner edited, by name ("ebay"). A name with no
    row uses the built-in template (fbc/ebay/template.py).
    """

    __tablename__ = "description_template"
    name = Column(String(40), primary_key=True)
    body = Column(Text, nullable=False)
