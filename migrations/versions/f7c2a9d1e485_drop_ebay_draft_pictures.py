"""drop ebay_draft.pictures

Revision ID: f7c2a9d1e485
Revises: e6b1d4a8c273
Create Date: 2026-11-15 22:00:00.000000

A draft's pictures are its Unit's (unit_picture, #140), each with its own eBay
address, and nothing reads this column any more. It held a JSON list of eBay
addresses that fbc stopped writing when drafts began using stored pictures,
and there were no drafts with any when this went in. Dropping it loses only
that list; downgrading puts the empty column back.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7c2a9d1e485"
down_revision = "e6b1d4a8c273"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ebay_draft", schema=None) as batch_op:
        batch_op.drop_column("pictures")


def downgrade():
    with op.batch_alter_table("ebay_draft", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pictures", sa.Text(), nullable=True))
