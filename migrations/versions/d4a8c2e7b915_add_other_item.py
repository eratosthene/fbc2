"""add other_item and ebay_listing.category_id

Revision ID: d4a8c2e7b915
Revises: b8e3a1d5c7f2
Create Date: 2026-11-15 00:00:00.000000

Only the tables and columns. Sorting the listings already in the database
needs eBay's category for each, so it's `flask sort-other-items`.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4a8c2e7b915"
down_revision = "b8e3a1d5c7f2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("category_id", sa.String(length=20), nullable=True)
        )
    op.create_table(
        "other_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("ebay_listing_id", sa.String(length=20), nullable=True),
        sa.Column("sales_receipt_id", sa.Integer(), nullable=True),
        sa.Column("sold", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["ebay_listing_id"], ["ebay_listing.item_id"]),
        sa.ForeignKeyConstraint(
            ["sales_receipt_id"], ["sales_receipt.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ebay_listing_id"),
    )
    with op.batch_alter_table("other_item", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_other_item_sales_receipt_id"),
            ["sales_receipt_id"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_other_item_sold"), ["sold"], unique=False)


def downgrade():
    op.drop_table("other_item")
    with op.batch_alter_table("ebay_listing", schema=None) as batch_op:
        batch_op.drop_column("category_id")
