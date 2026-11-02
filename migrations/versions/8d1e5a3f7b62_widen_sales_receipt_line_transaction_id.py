"""widen sales_receipt_line.transaction_id

Revision ID: 8d1e5a3f7b62
Revises: 3e7b2f9a6c15
Create Date: 2026-11-02 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8d1e5a3f7b62"
down_revision = "3e7b2f9a6c15"
branch_labels = None
depends_on = None


def upgrade():
    # eBay's transaction IDs run past 40 characters, which MariaDB refused.
    with op.batch_alter_table("sales_receipt_line", schema=None) as batch_op:
        batch_op.alter_column(
            "transaction_id",
            existing_type=sa.String(length=40),
            type_=sa.String(length=100),
            existing_nullable=True,
        )


def downgrade():
    with op.batch_alter_table("sales_receipt_line", schema=None) as batch_op:
        batch_op.alter_column(
            "transaction_id",
            existing_type=sa.String(length=100),
            type_=sa.String(length=40),
            existing_nullable=True,
        )
