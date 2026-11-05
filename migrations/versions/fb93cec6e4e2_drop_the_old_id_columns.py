"""Drop the old_id columns

Revision ID: fb93cec6e4e2
Revises: 4c9e2a7d5b18
Create Date: 2026-11-05 09:52:13.011531

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "fb93cec6e4e2"
down_revision = "4c9e2a7d5b18"
branch_labels = None
depends_on = None

# Data written to old_id came from the MongoDB migration and is not kept.
# The downgrade puts the columns back, empty.


def upgrade():
    with op.batch_alter_table("purchase_lot", schema=None) as batch_op:
        batch_op.drop_column("old_id")

    with op.batch_alter_table("purchase_order", schema=None) as batch_op:
        batch_op.drop_column("old_id")

    with op.batch_alter_table("sales_receipt", schema=None) as batch_op:
        batch_op.drop_column("old_id")

    with op.batch_alter_table("storage_box", schema=None) as batch_op:
        batch_op.drop_column("old_id")

    with op.batch_alter_table("supply", schema=None) as batch_op:
        batch_op.drop_column("old_id")

    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.drop_column("old_id")


def downgrade():
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("old_id", mysql.VARCHAR(length=24), nullable=True)
        )

    with op.batch_alter_table("supply", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("old_id", mysql.VARCHAR(length=24), nullable=True)
        )

    with op.batch_alter_table("storage_box", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("old_id", mysql.VARCHAR(length=24), nullable=True)
        )

    with op.batch_alter_table("sales_receipt", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("old_id", mysql.VARCHAR(length=24), nullable=True)
        )

    with op.batch_alter_table("purchase_order", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("old_id", mysql.VARCHAR(length=24), nullable=True)
        )

    with op.batch_alter_table("purchase_lot", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("old_id", mysql.VARCHAR(length=24), nullable=True)
        )
