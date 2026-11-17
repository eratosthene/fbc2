"""add views_30d and views_checked to ebay_listing

Revision ID: a9e3c7b1d5f4
Revises: c5d1e8a3f726
Create Date: 2026-11-17 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a9e3c7b1d5f4"
down_revision = "c5d1e8a3f726"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable and nothing filled in: a null count means unknown, until the
    # views job has asked eBay.
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.add_column(sa.Column("views_30d", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("views_checked", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.drop_column("views_checked")
        batch_op.drop_column("views_30d")
