"""make unit discogs_master and sold NOT NULL, storing blanks as '' and false

Revision ID: f1b7c4e2a9d3
Revises: c3a8e1f5b2d9
Create Date: 2026-10-30 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f1b7c4e2a9d3"
down_revision = "c3a8e1f5b2d9"
branch_labels = None
depends_on = None


def upgrade():
    # Units saved from the form already store a blank master as '' and an
    # unticked Sold as false; older ones may have NULL. Keep only the former so
    # the "Equal to ''" and "Not Equal to" Units filters find them all.
    op.execute("UPDATE unit SET discogs_master = '' WHERE discogs_master IS NULL")
    op.execute("UPDATE unit SET sold = false WHERE sold IS NULL")
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.alter_column(
            "discogs_master",
            existing_type=sa.String(length=21),
            existing_nullable=True,
            nullable=False,
            server_default="",
        )
        batch_op.alter_column(
            "sold",
            existing_type=sa.Boolean(),
            existing_nullable=True,
            nullable=False,
            server_default=sa.false(),
        )


def downgrade():
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.alter_column(
            "sold",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            nullable=True,
            server_default=None,
        )
        batch_op.alter_column(
            "discogs_master",
            existing_type=sa.String(length=21),
            existing_nullable=False,
            nullable=True,
            server_default=None,
        )
