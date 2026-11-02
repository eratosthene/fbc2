"""add supply_use table and supply cost_per_use

Revision ID: 512014df862a
Revises: a7c3e9f1d2b4
Create Date: 2026-11-01 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "512014df862a"
down_revision = "a7c3e9f1d2b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("supply", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cost_per_use", sa.Float(), nullable=True))
    # Starts empty: nothing recorded what was used before now.
    op.create_table(
        "supply_use",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.DateTime(), nullable=False),
        sa.Column("supply_id", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("sales_receipt_id", sa.Integer(), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["supply_id"], ["supply.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["sales_receipt_id"], ["sales_receipt.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["unit.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("supply_use", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_supply_use_supply_id"), ["supply_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_supply_use_sales_receipt_id"),
            ["sales_receipt_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_supply_use_unit_id"), ["unit_id"], unique=False
        )


def downgrade():
    op.drop_table("supply_use")
    with op.batch_alter_table("supply", schema=None) as batch_op:
        batch_op.drop_column("cost_per_use")
