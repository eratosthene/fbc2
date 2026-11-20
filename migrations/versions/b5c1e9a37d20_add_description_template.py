"""add the description_template table

Revision ID: b5c1e9a37d20
Revises: a9d4f7c2e601
Create Date: 2026-11-20 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b5c1e9a37d20"
down_revision = "a9d4f7c2e601"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "description_template",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("body", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("description_template")
