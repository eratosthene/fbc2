"""add ebay_oauth_token table

Revision ID: 9b3e5d1c2a47
Revises: 4c1f9a2b7d30
Create Date: 2026-10-29 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9b3e5d1c2a47"
down_revision = "4c1f9a2b7d30"
branch_labels = None
depends_on = None


def upgrade():
    # The app used to run db.create_all() on import, before `flask db upgrade`
    # got here, so on an older database the table may already exist.
    if sa.inspect(op.get_bind()).has_table("ebay_oauth_token"):
        return
    op.create_table(
        "ebay_oauth_token",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("expires", sa.DateTime(), nullable=True),
        sa.Column("created", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("ebay_oauth_token")
