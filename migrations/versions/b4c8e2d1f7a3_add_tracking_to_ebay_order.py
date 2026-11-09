"""add shipping carrier and tracking number to ebay_order

Revision ID: b4c8e2d1f7a3
Revises: 5a9d2e7c4b18
Create Date: 2026-11-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b4c8e2d1f7a3"
down_revision = "5a9d2e7c4b18"
branch_labels = None
depends_on = None


def upgrade():
    # Both are NULL until the next orders sync that sees the order shipped.
    with op.batch_alter_table("ebay_order", schema=None) as batch_op:
        batch_op.add_column(sa.Column("shipping_carrier", sa.String(length=40)))
        batch_op.add_column(sa.Column("tracking_number", sa.String(length=40)))


def downgrade():
    with op.batch_alter_table("ebay_order", schema=None) as batch_op:
        batch_op.drop_column("tracking_number")
        batch_op.drop_column("shipping_carrier")
