"""add quantity to purchase_order_supply

Revision ID: 5a9d2e7c4b18
Revises: a7d3b9e14c60
Create Date: 2026-11-08 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5a9d2e7c4b18"
down_revision = "a7d3b9e14c60"
branch_labels = None
depends_on = None


def upgrade():
    # The table only linked orders to supplies. What's there already gets 0:
    # the Supply List counted those orders when they were placed.
    with op.batch_alter_table("purchase_order_supply", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.alter_column(
            "purchase_order_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.alter_column("supply_id", existing_type=sa.Integer(), nullable=False)


def downgrade():
    with op.batch_alter_table("purchase_order_supply", schema=None) as batch_op:
        batch_op.alter_column("supply_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column(
            "purchase_order_id", existing_type=sa.Integer(), nullable=True
        )
        batch_op.drop_column("quantity")
