"""add the discogs_order and discogs_order_item tables

Revision ID: c3e7a1d94b58
Revises: b5d8f2a6c139
Create Date: 2026-11-15 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c3e7a1d94b58"
down_revision = "b5d8f2a6c139"
branch_labels = None
depends_on = None


def upgrade():
    # New, empty tables: the first Discogs orders sync fills them. Only
    # discogs_order_item.unit_id is a foreign key to an existing table, and
    # it matches unit.id (Integer).
    op.create_table(
        "discogs_order",
        sa.Column(
            "order_id", sa.String(length=30), autoincrement=False, nullable=False
        ),
        sa.Column("created_time", sa.DateTime(), nullable=True),
        sa.Column("last_activity", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=60), nullable=True),
        sa.Column("buyer", sa.String(length=60), nullable=True),
        sa.Column("shipping", sa.Float(), nullable=True),
        sa.Column("shipping_method", sa.String(length=100), nullable=True),
        sa.Column("fee", sa.Float(), nullable=True),
        sa.Column("total", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.PrimaryKeyConstraint("order_id"),
    )
    with op.batch_alter_table("discogs_order", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_discogs_order_created_time"), ["created_time"]
        )
        batch_op.create_index(batch_op.f("ix_discogs_order_status"), ["status"])
    op.create_table(
        "discogs_order_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discogs_order_id", sa.String(length=30), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=True),
        sa.Column("release_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("media_condition", sa.String(length=30), nullable=True),
        sa.Column("sleeve_condition", sa.String(length=30), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["discogs_order_id"], ["discogs_order.order_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["unit.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("discogs_order_item", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_discogs_order_item_discogs_order_id"), ["discogs_order_id"]
        )
        batch_op.create_index(
            batch_op.f("ix_discogs_order_item_listing_id"), ["listing_id"]
        )
        batch_op.create_index(batch_op.f("ix_discogs_order_item_unit_id"), ["unit_id"])


def downgrade():
    op.drop_table("discogs_order_item")
    op.drop_table("discogs_order")
