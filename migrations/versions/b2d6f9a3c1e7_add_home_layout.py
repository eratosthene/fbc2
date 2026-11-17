"""add home_layout

Revision ID: b2d6f9a3c1e7
Revises: a9e3c7b1d5f4
Create Date: 2026-11-17 00:00:00.000000

One row per user who has arranged the home page's cards. No rows to start
with: a user with none gets the default order from code.
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b2d6f9a3c1e7"
down_revision = "a9e3c7b1d5f4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "home_layout",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("layout", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["ab_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade():
    op.drop_table("home_layout")
