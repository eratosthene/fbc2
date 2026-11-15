"""add notes to sales_receipt

Revision ID: b8e3a1d5c7f2
Revises: a7d2c5e91f36
Create Date: 2026-11-15 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b8e3a1d5c7f2"
down_revision = "a7d2c5e91f36"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable and no default: MariaDB can't give a TEXT column a default, and
    # an empty note is the same as none.
    with op.batch_alter_table("sales_receipt", schema=None) as batch_op:
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("sales_receipt", schema=None) as batch_op:
        batch_op.drop_column("notes")
