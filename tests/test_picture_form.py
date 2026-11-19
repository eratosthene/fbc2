"""The Add pictures form on the eBay Draft page and the Unit Pictures page.

The form's script (previews, Remove, Clear all, Take photo, turning before
posting) can't be run here: the suite has no browser. These tests check the
markup and the hooks the script looks for, so a change that drops one fails.
"""

import re

import pytest
from auth import signed_in_client
from test_listing import _carnaval

from fbc import db
from fbc.models.ebay import EbayDraft


@pytest.fixture
def client(app):
    return signed_in_client(app)


def _pages(client):
    unit = _carnaval()
    db.session.add(unit)
    db.session.commit()
    draft = EbayDraft(unit=unit, title="T", status="draft")
    db.session.add(draft)
    db.session.commit()
    return [
        (
            "/ebaydraftmodelview/show/%d" % draft.id,
            "/ebaydraftmodelview/pictures/%d" % draft.id,
        ),
        (
            "/unitpictures/show/%d" % unit.id,
            "/unitpictures/add/%d" % unit.id,
        ),
    ]


def _tag(html, pattern):
    (tag,) = re.findall(pattern, html)
    return tag


def test_both_pages_have_the_form_with_both_inputs_and_the_script_hooks(client):
    for page_url, post_url in _pages(client):
        html = client.get(page_url).get_data(as_text=True)

        form = _tag(html, r'<form id="picture-form"[^>]*>')
        assert 'method="post"' in form
        assert 'enctype="multipart/form-data"' in form
        assert 'action="%s"' % post_url in form

        library = _tag(html, r'<input type="file" name="pictures"[^>]*>')
        assert "multiple" in library

        camera = _tag(html, r'<input type="file" id="picture-camera"[^>]*>')
        assert 'capture="environment"' in camera
        assert "name=" not in camera and "multiple" not in camera
        assert 'id="picture-camera-button"' in html and "Take photo" in html

        # The only field that posts pictures is the library input.
        assert html.count('name="pictures"') == 1
        assert ">Add pictures</button>" in html

        # What the script builds and looks for.
        for hook in ('"Remove"', "picture-clear", "Clear all", "new DataTransfer()"):
            assert hook in html
        assert 'id="picture-error"' in html
