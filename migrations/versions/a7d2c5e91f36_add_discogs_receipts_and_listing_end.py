"""add sales_receipt_discogs_order and listing_end

Revision ID: a7d2c5e91f36
Revises: c3e7a1d94b58
Create Date: 2026-11-15 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a7d2c5e91f36"
down_revision = "c3e7a1d94b58"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sales_receipt_discogs_order",
        sa.Column("sales_receipt_id", sa.Integer(), nullable=False),
        sa.Column("discogs_order_id", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(
            ["discogs_order_id"], ["discogs_order.order_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sales_receipt_id"], ["sales_receipt.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("sales_receipt_id", "discogs_order_id"),
    )
    with op.batch_alter_table("sales_receipt_discogs_order", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_sales_receipt_discogs_order_discogs_order_id"),
            ["discogs_order_id"],
            unique=False,
        )
    op.create_table(
        "listing_end",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("sold_on", sa.String(length=10), nullable=False),
        sa.Column("ended_on", sa.String(length=10), nullable=False),
        sa.Column("listing_ref", sa.String(length=30), nullable=False),
        sa.Column("order_id", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("message", sa.String(length=255), nullable=True),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("updated", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["unit_id"], ["unit.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("listing_end", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_listing_end_unit_id"), ["unit_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_listing_end_listing_ref"), ["listing_ref"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_listing_end_status"), ["status"], unique=False
        )


def downgrade():
    op.drop_table("listing_end")
    op.drop_table("sales_receipt_discogs_order")
