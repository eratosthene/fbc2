"""link sales receipts to several eBay orders, keep every order item and
what each unit sold for

Revision ID: 4c9e2a7d5b18
Revises: 8d1e5a3f7b62
Create Date: 2026-11-02 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4c9e2a7d5b18"
down_revision = "8d1e5a3f7b62"
branch_labels = None
depends_on = None


def drop_order_foreign_key():
    inspector = sa.inspect(op.get_bind())
    for fk in inspector.get_foreign_keys("sales_receipt"):
        if fk["constrained_columns"] == ["ebay_order_id"]:
            op.drop_constraint(fk["name"], "sales_receipt", type_="foreignkey")


def upgrade():
    op.create_table(
        "sales_receipt_ebay_order",
        sa.Column("sales_receipt_id", sa.Integer(), nullable=False),
        sa.Column("ebay_order_id", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["ebay_order_id"], ["ebay_order.order_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sales_receipt_id"], ["sales_receipt.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("sales_receipt_id", "ebay_order_id"),
    )
    with op.batch_alter_table("sales_receipt_ebay_order", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sales_receipt_ebay_order_ebay_order_id"),
            ["ebay_order_id"],
            unique=False,
        )
    # Every receipt keeps the order it had. Receipts that split one order
    # stay split until `flask combine-receipts` puts them together.
    op.execute(
        "INSERT INTO sales_receipt_ebay_order (sales_receipt_id, ebay_order_id)"
        " SELECT id, ebay_order_id FROM sales_receipt"
        " WHERE ebay_order_id IS NOT NULL"
    )
    drop_order_foreign_key()
    with op.batch_alter_table("sales_receipt", schema=None) as batch_op:
        batch_op.drop_column("ebay_order_id")

    # Starts empty: order syncs fill in the orders eBay still returns.
    op.create_table(
        "ebay_order_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ebay_order_id", sa.String(length=20), nullable=False),
        sa.Column("ebay_listing_id", sa.String(length=20), nullable=True),
        sa.Column("title", sa.String(length=100), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ebay_order_id"], ["ebay_order.order_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ebay_order_item", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ebay_order_item_ebay_listing_id"),
            ["ebay_listing_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ebay_order_item_ebay_order_id"),
            ["ebay_order_id"],
            unique=False,
        )
    with op.batch_alter_table("ebay_order", schema=None) as batch_op:
        batch_op.add_column(sa.Column("shipping", sa.Float(), nullable=True))
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sold_price", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.drop_column("sold_price")
    with op.batch_alter_table("ebay_order", schema=None) as batch_op:
        batch_op.drop_column("shipping")
    op.drop_table("ebay_order_item")
    with op.batch_alter_table("sales_receipt", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("ebay_order_id", sa.String(length=20), nullable=True)
        )
    # A receipt of several orders keeps only its first.
    op.execute(
        "UPDATE sales_receipt SET ebay_order_id = ("
        " SELECT MIN(l.ebay_order_id) FROM sales_receipt_ebay_order l"
        " WHERE l.sales_receipt_id = sales_receipt.id)"
    )
    with op.batch_alter_table("sales_receipt", schema=None) as batch_op:
        batch_op.create_foreign_key(None, "ebay_order", ["ebay_order_id"], ["order_id"])
    op.drop_table("sales_receipt_ebay_order")
