"""add renews_at to ebay_listing

Revision ID: f4b8d2e6a391
Revises: e3a7c1d9b250
Create Date: 2026-11-12 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "f4b8d2e6a391"
down_revision = "e3a7c1d9b250"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable, and nothing is filled in: the next listings sync sets it from
    # eBay's Time Left. Until then a listing renews monthly on its start's day.
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.add_column(sa.Column("renews_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.drop_column("renews_at")
