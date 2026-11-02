"""add expense table

Revision ID: 3e7b2f9a6c15
Revises: 512014df862a
Create Date: 2026-11-01 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3e7b2f9a6c15"
down_revision = "512014df862a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "expense",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("vendor", sa.String(length=100), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("link", sa.String(length=255), nullable=True),
        sa.Column("miles", sa.Float(), nullable=True),
        sa.Column("mileage_rate", sa.Float(), nullable=True),
        sa.Column("purchase_order_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"], ["purchase_order.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("expense", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_expense_date"), ["date"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_expense_purchase_order_id"),
            ["purchase_order_id"],
            unique=False,
        )


def downgrade():
    op.drop_table("expense")
