"""add parked_at to ebay_listing

Revision ID: c5d1e8a3f726
Revises: f7c2a9d1e485
Create Date: 2026-11-16 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c5d1e8a3f726"
down_revision = "f7c2a9d1e485"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable and nothing filled in: no listing has been parked yet.
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.add_column(sa.Column("parked_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.drop_column("parked_at")
