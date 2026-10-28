"""add indexes to unit foreign keys

Revision ID: e5d062bfb93e
Revises: da564425fbbe
Create Date: 2026-10-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5d062bfb93e'
down_revision = 'da564425fbbe'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('unit', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_unit_discogs_release_id'), ['discogs_release_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_unit_discogs_instance_id'), ['discogs_instance_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_unit_discogs_master'), ['discogs_master'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_unit_ebay_listing_id'), ['ebay_listing_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_unit_purchase_lot_id'), ['purchase_lot_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_unit_storage_box_id'), ['storage_box_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_unit_sales_receipt_id'), ['sales_receipt_id'], unique=False
        )


def downgrade():
    with op.batch_alter_table('unit', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_unit_sales_receipt_id'))
        batch_op.drop_index(batch_op.f('ix_unit_storage_box_id'))
        batch_op.drop_index(batch_op.f('ix_unit_purchase_lot_id'))
        batch_op.drop_index(batch_op.f('ix_unit_ebay_listing_id'))
        batch_op.drop_index(batch_op.f('ix_unit_discogs_master'))
        batch_op.drop_index(batch_op.f('ix_unit_discogs_instance_id'))
        batch_op.drop_index(batch_op.f('ix_unit_discogs_release_id'))
