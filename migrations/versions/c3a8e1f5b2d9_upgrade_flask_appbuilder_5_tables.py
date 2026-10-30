"""upgrade Flask-AppBuilder's ab_* tables to 5.x

Revision ID: c3a8e1f5b2d9
Revises: 7e2a4c9d1f58
Create Date: 2026-10-30 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c3a8e1f5b2d9"
down_revision = "7e2a4c9d1f58"
branch_labels = None
depends_on = None


def _has_unique(inspector, table, column):
    return any(
        uc["column_names"] == [column] for uc in inspector.get_unique_constraints(table)
    )


def upgrade():
    inspector = sa.inspect(op.get_bind())

    # Flask-AppBuilder creates its own missing tables when the app starts,
    # which is before `flask db upgrade` gets here, so this one may already
    # exist.
    if not inspector.has_table("ab_api_key"):
        op.create_table(
            "ab_api_key",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("uuid", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=256), nullable=False),
            sa.Column("key_hash", sa.String(length=256), nullable=False),
            sa.Column("lookup_hash", sa.String(length=256), nullable=True),
            sa.Column("key_prefix", sa.String(length=16), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("scopes", sa.Text(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_on", sa.DateTime(), nullable=True),
            sa.Column("expires_on", sa.DateTime(), nullable=True),
            sa.Column("revoked_on", sa.DateTime(), nullable=True),
            sa.Column("last_used_on", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["ab_user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("uuid"),
            sa.UniqueConstraint("lookup_hash"),
        )
        op.create_index("idx_api_key_prefix", "ab_api_key", ["key_prefix"])
        op.create_index("idx_api_key_user_id", "ab_api_key", ["user_id"])
        op.create_index(
            "idx_api_key_lookup_hash", "ab_api_key", ["lookup_hash"], unique=True
        )

    # A permission with no permission or no view grants nothing, and can't be
    # kept once both are required.
    op.execute(
        "DELETE FROM ab_permission_view_role WHERE permission_view_id IN ("
        "SELECT id FROM ab_permission_view"
        " WHERE permission_id IS NULL OR view_menu_id IS NULL)"
    )
    op.execute(
        "DELETE FROM ab_permission_view"
        " WHERE permission_id IS NULL OR view_menu_id IS NULL"
    )
    for column in ("permission_id", "view_menu_id"):
        op.alter_column(
            "ab_permission_view", column, existing_type=sa.Integer(), nullable=False
        )

    for table in ("ab_user", "ab_register_user"):
        op.alter_column(
            table,
            "username",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
        op.alter_column(
            table,
            "email",
            existing_type=sa.String(length=64),
            type_=sa.String(length=320),
            existing_nullable=False,
        )
        # MySQL names an unnamed unique constraint after its column, as
        # Flask-AppBuilder's own create_all() would have.
        if not _has_unique(inspector, table, "email"):
            op.create_unique_constraint("email", table, ["email"])


def downgrade():
    # Back to Flask-AppBuilder 4.8.1's tables, where ab_user's email was
    # already 320 characters and unique.
    op.drop_constraint("email", "ab_register_user", type_="unique")
    op.alter_column(
        "ab_register_user",
        "email",
        existing_type=sa.String(length=320),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    for table in ("ab_user", "ab_register_user"):
        op.alter_column(
            table,
            "username",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    for column in ("permission_id", "view_menu_id"):
        op.alter_column(
            "ab_permission_view", column, existing_type=sa.Integer(), nullable=True
        )

    op.drop_table("ab_api_key")
