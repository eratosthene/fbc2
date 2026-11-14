"""A signed-in test client, for tests that open pages without a fixture for it."""

import fbc


def signed_in_client(app=None):
    """A test client signed in as an Admin (made if there isn't one yet)."""
    app = app or fbc.app
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    if not sm.find_user("admin"):
        sm.add_user(
            "admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw"
        )
    client = app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client
