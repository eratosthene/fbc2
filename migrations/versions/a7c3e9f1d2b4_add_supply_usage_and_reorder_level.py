"""add supply used_for, record_size, per_units, pack_size, pack_used and min_quantity

Revision ID: a7c3e9f1d2b4
Revises: f1b7c4e2a9d3
Create Date: 2026-10-31 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a7c3e9f1d2b4"
down_revision = "f1b7c4e2a9d3"
branch_labels = None
depends_on = None

# How the supplies fbc already has are used: (name, used_for, record_size,
# per_units, pack_size). Packing tape is offered on Ship This but left at
# none, since how much a shipment takes varies.
SUPPLIES = [
    ("LP Mailers", "shipping", None, 5, 1),
    ('Vinyl Outer Sleeves, 12"', "shipping", '12"', 1, 1),
    ('Vinyl Outer Sleeves, 10"', "shipping", '10"', 1, 1),
    ('Vinyl Outer Sleeves, 7"', "shipping", '7"', 1, 1),
    ("Shipping Label Roll", "shipping", None, 0, 220),
    ("Packing Tape Rolls", "shipping", None, None, 1),
    ('Vinyl Inner Sleeves, 12"', "unit", None, None, 1),
    ('Vinyl Inner Sleeves, 10"', "unit", None, None, 1),
    ('Vinyl Inner Sleeves, 7"', "unit", None, None, 1),
]


def upgrade():
    with op.batch_alter_table("supply", schema=None) as batch_op:
        batch_op.add_column(sa.Column("used_for", sa.String(length=10), nullable=True))
        batch_op.add_column(
            sa.Column("record_size", sa.String(length=5), nullable=True)
        )
        batch_op.add_column(sa.Column("per_units", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("pack_size", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("pack_used", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("min_quantity", sa.Integer(), nullable=False, server_default="0")
        )
    update = sa.text(
        "UPDATE supply SET used_for = :used_for, record_size = :record_size,"
        " per_units = :per_units, pack_size = :pack_size WHERE name = :name"
    )
    for name, used_for, record_size, per_units, pack_size in SUPPLIES:
        op.get_bind().execute(
            update,
            dict(
                name=name,
                used_for=used_for,
                record_size=record_size,
                per_units=per_units,
                pack_size=pack_size,
            ),
        )


def downgrade():
    with op.batch_alter_table("supply", schema=None) as batch_op:
        batch_op.drop_column("min_quantity")
        batch_op.drop_column("pack_used")
        batch_op.drop_column("pack_size")
        batch_op.drop_column("per_units")
        batch_op.drop_column("record_size")
        batch_op.drop_column("used_for")
