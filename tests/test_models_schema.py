import pytest


def _foreign_keys(db):
    for table in db.metadata.sorted_tables:
        for column in table.columns:
            for fk in column.foreign_keys:
                yield column, fk.column


def test_foreign_keys_match_the_type_they_reference(db):
    # MySQL refuses to create a foreign key between columns of different
    # types, e.g. an Integer pointing at a BigInteger primary key, so a
    # mismatch here breaks db.create_all() on a fresh database.
    mismatched = [
        f"{column.table.name}.{column.name} ({column.type!r}) -> "
        f"{target.table.name}.{target.name} ({target.type!r})"
        for column, target in _foreign_keys(db)
        if column.type.compile() != target.type.compile()
    ]
    assert mismatched == []


@pytest.mark.parametrize(
    "table, column",
    [
        ("discogs_instance_discogs_release", "instance_id"),
        ("unit", "discogs_instance_id"),
    ],
)
def test_discogs_instance_foreign_keys_cascade_like_the_migration(db, table, column):
    # Migration da564425fbbe created these with ON DELETE CASCADE.
    (fk,) = db.metadata.tables[table].columns[column].foreign_keys
    assert fk.ondelete == "CASCADE"
