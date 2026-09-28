"""v0.2.7: повторная выдача талона (Idempotency-Key) и отметка печати

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28 21:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('tickets', schema=None) as b:
        b.add_column(sa.Column('idem_key', sa.String(length=64), nullable=True))
        b.add_column(sa.Column('printed_at', sa.DateTime(timezone=True), nullable=True))
        b.create_unique_constraint('uq_tickets_device_idem', ['issued_device_id', 'idem_key'])


def downgrade() -> None:
    with op.batch_alter_table('tickets', schema=None) as b:
        b.drop_constraint('uq_tickets_device_idem', type_='unique')
        b.drop_column('printed_at')
        b.drop_column('idem_key')
