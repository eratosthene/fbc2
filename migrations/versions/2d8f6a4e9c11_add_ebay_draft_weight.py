"""add ebay_draft weight_oz

Revision ID: 2d8f6a4e9c11
Revises: 9b3e5d1c2a47
Create Date: 2026-10-30 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2d8f6a4e9c11"
down_revision = "9b3e5d1c2a47"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ebay_draft", schema=None) as batch_op:
        batch_op.add_column(sa.Column("weight_oz", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("ebay_draft", schema=None) as batch_op:
        batch_op.drop_column("weight_oz")
