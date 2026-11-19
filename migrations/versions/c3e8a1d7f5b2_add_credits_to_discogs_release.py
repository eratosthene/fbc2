"""add credits to discogs_release

Revision ID: c3e8a1d7f5b2
Revises: b2d6f9a3c1e7
Create Date: 2026-11-19 12:00:00.000000

JSON text of the composer, conductor and orchestra credits of a release, for
the title and item specifics of classical eBay Drafts. Like `labels`, it is
NULL for a release not yet read from Discogs for it: a sync fills it, and an
eBay Draft fetches it once for a classical release that has none. Nothing is
backfilled here, since that takes a call to Discogs per release.
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c3e8a1d7f5b2"
down_revision = "b2d6f9a3c1e7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("discogs_release", schema=None) as batch_op:
        batch_op.add_column(sa.Column("credits", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("discogs_release", schema=None) as batch_op:
        batch_op.drop_column("credits")
