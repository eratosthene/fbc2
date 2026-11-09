"""add shipping carrier, tracking number and tracking status to ebay_order

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
    # All blank until an orders sync sees the order shipped, and a USPS lookup
    # (if USPS_SETTINGS is set) finds its status.
    with op.batch_alter_table("ebay_order", schema=None) as batch_op:
        batch_op.add_column(sa.Column("shipping_carrier", sa.String(length=40)))
        batch_op.add_column(sa.Column("tracking_number", sa.String(length=40)))
        batch_op.add_column(sa.Column("tracking_status", sa.String(length=100)))
        batch_op.add_column(
            sa.Column(
                "tracking_delivered",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("tracking_last_event", sa.DateTime()))
        batch_op.add_column(sa.Column("tracking_checked", sa.DateTime()))


def downgrade():
    with op.batch_alter_table("ebay_order", schema=None) as batch_op:
        batch_op.drop_column("tracking_checked")
        batch_op.drop_column("tracking_last_event")
        batch_op.drop_column("tracking_delivered")
        batch_op.drop_column("tracking_status")
        batch_op.drop_column("tracking_number")
        batch_op.drop_column("shipping_carrier")
