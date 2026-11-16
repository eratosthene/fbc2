"""The footer shows the image's deploy id when there is one."""

from auth import signed_in_client


def test_the_footer_shows_the_deploy_id(app, monkeypatch):
    monkeypatch.setenv("FBC_REVISION", "sha-39f2cd0")
    html = signed_in_client(app).get("/").get_data(as_text=True)
    assert "Fire &amp; Bones Collectibles &middot; sha-39f2cd0" in html.replace(
        "&#183;", "&middot;"
    )


def test_the_footer_is_just_the_shop_name_without_one(app, monkeypatch):
    monkeypatch.delenv("FBC_REVISION", raising=False)
    html = signed_in_client(app).get("/").get_data(as_text=True)
    assert "<small>Fire &amp; Bones Collectibles</small>" in html
