"""add grade and note fields to unit

Revision ID: a9d4f7c2e601
Revises: c3e8a1d7f5b2
Create Date: 2026-11-19 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a9d4f7c2e601"
down_revision = "c3e8a1d7f5b2"
branch_labels = None
depends_on = None


def split_grading(grading):
    """
    ("record", "sleeve") from the old Unit.grading ("VG+/VG+", "NM or M-/VG",
    "VG+"); either may be None. A copy of the rule the app used, kept here so
    this migration doesn't depend on app code that later changes.
    """
    if not grading:
        return None, None
    parts = [p.strip() for p in grading.split("/", 1)]
    record = parts[0] or None
    sleeve = parts[1] if len(parts) > 1 and parts[1] else None
    return record, sleeve


def upgrade():
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.add_column(sa.Column("record_grade", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("record_note", sa.String(300), nullable=True))
        batch_op.add_column(sa.Column("sleeve_grade", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("sleeve_note", sa.String(300), nullable=True))
        batch_op.add_column(
            sa.Column("dust_jacket_grade", sa.String(20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("dust_jacket_note", sa.String(300), nullable=True)
        )
        batch_op.add_column(sa.Column("extras", sa.String(300), nullable=True))

    # The grades come from the old combined one. Grading and description are
    # left as they are; a later change drops them.
    unit = sa.table(
        "unit",
        sa.column("id", sa.Integer),
        sa.column("grading", sa.String),
        sa.column("record_grade", sa.String),
        sa.column("sleeve_grade", sa.String),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(unit.c.id, unit.c.grading).where(unit.c.grading.isnot(None))
    ).all()
    for unit_id, grading in rows:
        record, sleeve = split_grading(grading)
        if record or sleeve:
            bind.execute(
                unit.update()
                .where(unit.c.id == unit_id)
                .values(record_grade=record, sleeve_grade=sleeve)
            )


def downgrade():
    with op.batch_alter_table("unit", schema=None) as batch_op:
        for column in (
            "extras",
            "dust_jacket_note",
            "dust_jacket_grade",
            "sleeve_note",
            "sleeve_grade",
            "record_note",
            "record_grade",
        ):
            batch_op.drop_column(column)
