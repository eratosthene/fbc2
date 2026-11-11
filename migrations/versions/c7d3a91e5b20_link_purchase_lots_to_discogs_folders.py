"""link purchase lots to discogs folders

Revision ID: c7d3a91e5b20
Revises: b4c8e2d1f7a3
Create Date: 2026-11-10 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c7d3a91e5b20"
down_revision = "b4c8e2d1f7a3"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable, and nothing is filled in: existing lots keep no folder until
    # the owner sets one.
    with op.batch_alter_table("purchase_lot", schema=None) as batch_op:
        batch_op.add_column(sa.Column("folder_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_purchase_lot_folder_id", "folder", ["folder_id"], ["folder_id"]
        )


def downgrade():
    with op.batch_alter_table("purchase_lot", schema=None) as batch_op:
        batch_op.drop_constraint("fk_purchase_lot_folder_id", type_="foreignkey")
        batch_op.drop_column("folder_id")
