"""add the insertion_calibration table

Revision ID: e3a7c1d9b250
Revises: d9e2b6f4a173
Create Date: 2026-11-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e3a7c1d9b250"
down_revision = "d9e2b6f4a173"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "insertion_calibration",
        sa.Column("month_start", sa.DateTime(), nullable=False),
        sa.Column("adjustment", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("entered", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("month_start"),
    )


def downgrade():
    op.drop_table("insertion_calibration")
