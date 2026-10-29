"""add ebay_draft table and discogs_release labels/country

Revision ID: 4c1f9a2b7d30
Revises: e5d062bfb93e
Create Date: 2026-10-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4c1f9a2b7d30'
down_revision = 'e5d062bfb93e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('discogs_release', schema=None) as batch_op:
        batch_op.add_column(sa.Column('labels', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('country', sa.String(length=50), nullable=True))

    # fbc/__init__.py runs db.create_all() on import, which `flask db upgrade`
    # does before getting here, so the table (and its indexes) may already
    # exist.
    if sa.inspect(op.get_bind()).has_table('ebay_draft'):
        return
    op.create_table(
        'ebay_draft',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('unit_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=80), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('best_offer', sa.Boolean(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('item_specifics', sa.Text(), nullable=True),
        sa.Column('pictures', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('messages', sa.Text(), nullable=True),
        sa.Column('ebay_listing_id', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['ebay_listing_id'], ['ebay_listing.item_id']),
        sa.ForeignKeyConstraint(['unit_id'], ['unit.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('ebay_draft', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ebay_draft_unit_id'), ['unit_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ebay_draft_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('ebay_draft', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ebay_draft_status'))
        batch_op.drop_index(batch_op.f('ix_ebay_draft_unit_id'))
    op.drop_table('ebay_draft')

    with op.batch_alter_table('discogs_release', schema=None) as batch_op:
        batch_op.drop_column('country')
        batch_op.drop_column('labels')
