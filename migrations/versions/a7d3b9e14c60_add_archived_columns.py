"""add archived to unit, ebay_listing, ebay_order and sales_receipt

Revision ID: a7d3b9e14c60
Revises: fb93cec6e4e2
Create Date: 2026-11-06 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a7d3b9e14c60"
down_revision = "fb93cec6e4e2"
branch_labels = None
depends_on = None

TABLES = ("unit", "ebay_listing", "ebay_order", "sales_receipt")


def upgrade():
    # Every existing row starts unarchived; `flask archive` sets the old ones.
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "archived",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
            batch_op.create_index(
                batch_op.f("ix_" + table + "_archived"), ["archived"], unique=False
            )


def downgrade():
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f("ix_" + table + "_archived"))
            batch_op.drop_column("archived")
