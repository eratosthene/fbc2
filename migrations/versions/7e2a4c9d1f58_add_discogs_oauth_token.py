"""add discogs_oauth_token table

Revision ID: 7e2a4c9d1f58
Revises: 2d8f6a4e9c11
Create Date: 2026-10-30 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7e2a4c9d1f58"
down_revision = "2d8f6a4e9c11"
branch_labels = None
depends_on = None


def upgrade():
    # fbc/__init__.py runs db.create_all() on import, which `flask db upgrade`
    # does before getting here, so the table may already exist.
    if sa.inspect(op.get_bind()).has_table("discogs_oauth_token"):
        return
    op.create_table(
        "discogs_oauth_token",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("created", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("discogs_oauth_token")
