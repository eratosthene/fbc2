"""add sales_receipt_line table

Revision ID: 6b2f8d4a1c73
Revises: 512014df862a
Create Date: 2026-11-01 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6b2f8d4a1c73"
down_revision = "512014df862a"
branch_labels = None
depends_on = None


def upgrade():
    # Starts empty: `flask backfill-earnings` fills in past receipts from eBay.
    op.create_table(
        "sales_receipt_line",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sales_receipt_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("fee_type", sa.String(length=50), nullable=True),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("ebay_order_id", sa.String(length=20), nullable=True),
        sa.Column("transaction_id", sa.String(length=40), nullable=True),
        sa.Column("transaction_date", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sales_receipt_id"], ["sales_receipt.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sales_receipt_line", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sales_receipt_line_sales_receipt_id"),
            ["sales_receipt_id"],
            unique=False,
        )


def downgrade():
    op.drop_table("sales_receipt_line")
