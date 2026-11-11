"""add ended and end_time to ebay_listing, and the sync_record table

Revision ID: d9e2b6f4a173
Revises: c7d3a91e5b20
Create Date: 2026-11-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "d9e2b6f4a173"
down_revision = "c7d3a91e5b20"
branch_labels = None
depends_on = None


def upgrade():
    # Every listing already there starts as not ended, with no end_time:
    # the first eBay listings sync after this marks the ones that aren't in
    # eBay's active list. Nothing is guessed here.
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("ended", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("end_time", sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_ebay_listing_ended"), ["ended"])
    op.create_table(
        "sync_record",
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("finished", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade():
    op.drop_table("sync_record")
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ebay_listing_ended"))
        batch_op.drop_column("end_time")
        batch_op.drop_column("ended")
