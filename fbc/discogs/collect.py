"""Adding a release to the Discogs collection from Identify Record, and setting
the copy's condition, notes and other custom fields."""

import logging

from fbc.discogs import client as discogs_client
from fbc.core import devmode
from fbc.discogs import identify

logger = logging.getLogger()


# The collection fields the form offers, in order. They're matched by name
# against the account's custom fields (Discogs numbers them per account).
COLLECTION_FIELDS = [
    "Media Condition",
    "Sleeve Condition",
    "Notes",
    "Dust Jacket Condition",
    "Extras Included",
]


def _grade(value):
    """ "Near Mint (NM or M-)" -> "NM or M-", as Sync Discogs reads it."""
    import re

    m = re.search(r".*\((.*)\)", value or "")
    return m.group(1) if m else ""


def grades_from(values):
    """
    The Unit's grades from the conditions chosen, as Add Unit's starting
    values: {"record_grade": "M-", "sleeve_grade": "VG+"}. A grade that
    isn't chosen is left out.
    """
    grades = {
        "record_grade": _grade(values.get("Media Condition")),
        "sleeve_grade": _grade(values.get("Sleeve Condition")),
    }
    return {name: grade for name, grade in grades.items() if grade}


def collection_form():
    """
    What the collect form offers, from the account: {"ok": True, "folders":
    [(id, name)], "fields": [{"id", "name", "type", "options"}], "missing":
    [names not set up on the account]} or {"ok": False, "error", "dev_mode"}.
    """
    if devmode.on():
        return identify._failure("Not available in dev mode.", dev_mode=True)

    def fetch():
        try:
            account = discogs_client.account()
            folders = account.collection_folders()
            fields = account.collection_fields()
        except discogs_client.DiscogsAuthError as e:
            return identify._failure(str(e))
        except discogs_client.ApiError as e:
            return identify._failure("Discogs said no: " + str(e))
        except Exception as e:
            logger.exception("Couldn't read the collection's folders and fields")
            return identify._failure("Couldn't reach Discogs: " + str(e))
        by_name = {f["name"].lower(): f for f in fields}
        chosen = [by_name[n.lower()] for n in COLLECTION_FIELDS if n.lower() in by_name]
        return {
            "ok": True,
            # Folder 0 is "All", which can't be added to.
            "folders": [(f["id"], f["name"]) for f in folders if f["id"] != 0],
            "fields": [
                {
                    "id": f["id"],
                    "name": f["name"],
                    "type": f.get("type"),
                    "options": f.get("options") or [],
                }
                for f in chosen
            ],
            "missing": [n for n in COLLECTION_FIELDS if n.lower() not in by_name],
        }

    found = identify._cached(("collection-form",), fetch)
    if not found["ok"]:
        with identify._lock:
            identify._cache.pop(("collection-form",), None)
    return found


def collect(release_id, folder_id, values):
    """
    Add a copy of the release to the Discogs collection folder `folder_id`,
    set its custom fields from `values` ({field name: text}, blanks and
    anything a dropdown doesn't offer left out), and save it in fbc like a
    synced one. WRITES TO THE DISCOGS ACCOUNT. Returns (DiscogsRelease,
    instance_id, warnings, None) or (None, None, [], why not). If the copy
    is made but something after fails, the answer says so: the release is
    then in Discogs and Sync Discogs will bring it in.
    """
    from fbc import db
    from fbc.models.discogs import DiscogsRelease
    from fbc.discogs.saving import add_discogs_release

    if devmode.on():
        return None, None, [], devmode.message("Discogs")
    form = collection_form()
    if not form["ok"]:
        return None, None, [], form["error"]
    if int(folder_id) not in {f[0] for f in form["folders"]}:
        return None, None, [], "That isn't one of your Discogs folders."
    release_id = int(release_id)
    try:
        account = discogs_client.account()
        instance_id = account.add_to_collection(folder_id, release_id)
    except discogs_client.DiscogsAuthError as e:
        return None, None, [], str(e)
    except discogs_client.ApiError as e:
        if e.rate_limited:
            return None, None, [], "Discogs says too many requests; try in a minute."
        return None, None, [], "Discogs said no: " + str(e)
    except Exception as e:
        logger.exception("Couldn't add release %s to the collection", release_id)
        return None, None, [], "Couldn't reach Discogs: " + str(e)
    logger.info("Added release %s to Discogs as instance %s", release_id, instance_id)

    warnings = []
    for field in form["fields"]:
        value = (values.get(field["name"]) or "").strip()
        if not value:
            continue
        if field["type"] == "dropdown" and value not in field["options"]:
            warnings.append(field["name"] + ": Discogs has no choice " + repr(value))
            continue
        try:
            account.set_collection_field(
                folder_id, release_id, instance_id, field["id"], value
            )
        except Exception as e:
            logger.warning("Couldn't set %s: %s", field["name"], e)
            warnings.append(field["name"] + " wasn't set: " + str(e))
    try:
        item = next(
            i
            for i in account.collection_items(release_id)
            if int(i["instance_id"]) == int(instance_id)
        )
        add_discogs_release(item)
    except Exception as e:
        logger.exception("Added to Discogs but couldn't save in fbc")
        db.session.rollback()
        return (
            None,
            instance_id,
            warnings,
            "It was added to your Discogs collection (instance %s), but fbc"
            " couldn't save it (%s). Run Sync Discogs to bring it in."
            % (instance_id, e),
        )
    return db.session.get(DiscogsRelease, release_id), instance_id, warnings, None
