"""Taking a shipped Unit's copy out of the Discogs collection: what Ship This
plans to remove, and the removal itself."""

import logging

from fbc.core import devmode

logger = logging.getLogger()


def discogs_plan(unit):
    """
    The DiscogsInstance shipping `unit` removes from the collection, and
    None; or None and why nothing will be removed. Only the instance the
    Unit is linked to is ever removed, never another copy of the release.
    """
    from fbc import db
    from fbc.models.inventory import Unit

    instance = unit.discogs_instance
    if not instance:
        return None, (
            "This Unit isn't linked to a Discogs instance, so nothing will be"
            " removed from Discogs."
        )
    release = unit.discogs_release
    if not release or instance not in release.discogs_instances:
        return None, (
            "This Unit's Discogs instance "
            + str(instance.instance_id)
            + " isn't one of its release's instances in fbc, so it won't be"
            " removed; remove it from Discogs by hand."
        )
    others = (
        db.session.query(Unit)
        .filter_by(discogs_instance_id=instance.instance_id)
        .filter(Unit.id != unit.id)
        .count()
    )
    if others:
        return None, (
            "Discogs instance "
            + str(instance.instance_id)
            + " is linked to other Units too, so it won't be removed; remove it"
            " from Discogs by hand."
        )
    return instance, None


def remove_from_discogs(unit):
    """
    Delete `unit`'s own instance of its release from the Discogs collection,
    then from fbc. Returns (message, category) to flash.

    The instance is looked up on Discogs first, to find the folder it's in
    and to make sure it's still there; the delete names that one instance,
    so other copies of the same release are left alone. In dev mode it's
    only removed from fbc.
    """
    from fbc.discogs import client as discogs_client
    from fbc.discogs.client import ApiError, DiscogsAuthError
    from fbc.discogs.saving import forget_discogs_instance

    instance, problem = discogs_plan(unit)
    if problem:
        return problem, "warning"
    instance_id = instance.instance_id
    release_id = unit.discogs_release_id
    title = str(unit.discogs_release)
    if devmode.on():
        forget_discogs_instance(instance)
        return (
            devmode.message("Discogs removal")
            + " Removed "
            + title
            + " (instance "
            + str(instance_id)
            + ") from fbc only.",
            "warning",
        )
    try:
        account = discogs_client.account()
        found = [
            i
            for i in account.collection_items(release_id)
            if i["instance_id"] == instance_id
        ]
        if found:
            account.delete_collection_instance(
                found[0]["folder_id"], release_id, instance_id
            )
            message = (
                "Removed "
                + title
                + " (instance "
                + str(instance_id)
                + ") from Discogs."
            )
        else:
            message = (
                title
                + " (instance "
                + str(instance_id)
                + ") was already gone from Discogs; removed it from fbc too."
            )
    # DiscogsAuthError: fbc isn't connected to Discogs, which is only found
    # out once the receipt is saved, so it's reported like any other failure.
    except (ApiError, DiscogsAuthError, OSError) as e:
        logger.error(e)
        return (
            "Couldn't remove "
            + title
            + " (instance "
            + str(instance_id)
            + ") from Discogs: "
            + str(e).rstrip(".")
            + ". Remove it by hand, then Sync Discogs.",
            "danger",
        )
    forget_discogs_instance(instance)
    logger.info(
        "Removed Discogs instance " + str(instance_id) + " of " + str(release_id)
    )
    return message, "success"
