"""add ebay_listing.description_hash

Revision ID: c3e8a1d95f42
Revises: b5c1e9a37d20
Create Date: 2026-11-20 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c3e8a1d95f42"
down_revision = "b5c1e9a37d20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ebay_listing", sa.Column("description_hash", sa.String(40), nullable=True)
    )


def downgrade():
    op.drop_column("ebay_listing", "description_hash")
