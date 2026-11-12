"""add the mystery box columns to ebay_listing

Revision ID: a7c3e9d15b42
Revises: f4b8d2e6a391
Create Date: 2026-11-13 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a7c3e9d15b42"
down_revision = "f4b8d2e6a391"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable, and nothing is filled in: a listing becomes a mystery box when
    # its Storage Box and Units per sale are set on its edit page. Both are
    # Integer, like storage_box.id, so the foreign key is allowed on MariaDB.
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pick_from_box_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("pick_count", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_ebay_listing_pick_from_box_id"),
            ["pick_from_box_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_ebay_listing_pick_from_box_id_storage_box"),
            "storage_box",
            ["pick_from_box_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_ebay_listing_pick_from_box_id_storage_box"),
            type_="foreignkey",
        )
        batch_op.drop_index(batch_op.f("ix_ebay_listing_pick_from_box_id"))
        batch_op.drop_column("pick_count")
        batch_op.drop_column("pick_from_box_id")
