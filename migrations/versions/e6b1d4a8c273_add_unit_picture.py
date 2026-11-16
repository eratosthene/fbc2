"""add unit_picture

Revision ID: e6b1d4a8c273
Revises: d4a8c2e7b915
Create Date: 2026-11-15 12:00:00.000000

Only the table, for a Unit's pictures (#140). It adds, and drops nothing, so
the old image can't start against it but no data is at risk. unit_id is an
Integer, as unit.id is, so MariaDB accepts the foreign key.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e6b1d4a8c273"
down_revision = "d4a8c2e7b915"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "unit_picture",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("file", sa.String(length=60), nullable=False),
        sa.Column("original_file", sa.String(length=60), nullable=False),
        sa.Column("upload_name", sa.String(length=255), nullable=True),
        sa.Column("ebay_url", sa.String(length=500), nullable=True),
        sa.Column("ebay_uploaded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["unit_id"], ["unit.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("unit_picture", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_unit_picture_unit_id"), ["unit_id"], unique=False
        )


def downgrade():
    op.drop_table("unit_picture")
