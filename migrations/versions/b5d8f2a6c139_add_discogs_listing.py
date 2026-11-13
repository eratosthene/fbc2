"""add the discogs_listing table

Revision ID: b5d8f2a6c139
Revises: a7c3e9d15b42
Create Date: 2026-11-12 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b5d8f2a6c139"
down_revision = "a7c3e9d15b42"
branch_labels = None
depends_on = None


def upgrade():
    # A new, empty table: the first Discogs listings sync fills it.
    op.create_table(
        "discogs_listing",
        sa.Column("listing_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("release_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("condition", sa.String(length=30), nullable=True),
        sa.Column("sleeve_condition", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("posted", sa.DateTime(), nullable=True),
        sa.Column("url", sa.String(length=255), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("ended", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["unit_id"], ["unit.id"]),
        sa.PrimaryKeyConstraint("listing_id"),
    )
    with op.batch_alter_table("discogs_listing", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_discogs_listing_release_id"), ["release_id"]
        )
        batch_op.create_index(batch_op.f("ix_discogs_listing_unit_id"), ["unit_id"])
        batch_op.create_index(batch_op.f("ix_discogs_listing_status"), ["status"])
        batch_op.create_index(batch_op.f("ix_discogs_listing_ended"), ["ended"])


def downgrade():
    op.drop_table("discogs_listing")
