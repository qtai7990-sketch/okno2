"""v0.2.6: электронная очередь — талоны, роли устройств, окно банка

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('devices', schema=None) as b:
        b.add_column(sa.Column('role', sa.String(length=12), nullable=False, server_default='desk'))
        b.add_column(sa.Column('station', sa.String(length=8), nullable=False, server_default=''))
        b.add_column(sa.Column('series', sa.String(length=1), nullable=False, server_default=''))
    with op.batch_alter_table('banks', schema=None) as b:
        b.add_column(sa.Column('window', sa.String(length=8), nullable=False, server_default=''))
    with op.batch_alter_table('cases', schema=None) as b:
        b.add_column(sa.Column('ticket_id', sa.String(length=36), nullable=True))
        b.create_index(b.f('ix_cases_ticket_id'), ['ticket_id'], unique=False)
    op.create_table(
        'ticket_counters',
        sa.Column('stand_id', sa.String(length=32), nullable=False),
        sa.Column('day', sa.String(length=10), nullable=False),
        sa.Column('series', sa.String(length=1), nullable=False),
        sa.Column('last', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('stand_id', 'day', 'series'),
    )
    op.create_table(
        'tickets',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('stand_id', sa.String(length=32), nullable=False),
        sa.Column('day', sa.String(length=10), nullable=False),
        sa.Column('series', sa.String(length=1), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('number', sa.String(length=8), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('lang', sa.String(length=4), nullable=False),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('issued_device_id', sa.Integer(), nullable=False),
        sa.Column('issued_operator_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('checked_in_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_called_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('called_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('call_count', sa.Integer(), nullable=False),
        sa.Column('station_kind', sa.String(length=8), nullable=False),
        sa.Column('station', sa.String(length=8), nullable=False),
        sa.Column('served_operator_id', sa.Integer(), nullable=True),
        sa.Column('case_id', sa.String(length=36), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['stand_id'], ['stands.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stand_id', 'day', 'number'),
        sa.UniqueConstraint('token_hash'),
    )


def downgrade() -> None:
    op.drop_table('tickets')
    op.drop_table('ticket_counters')
    with op.batch_alter_table('cases', schema=None) as b:
        b.drop_index(b.f('ix_cases_ticket_id'))
        b.drop_column('ticket_id')
    with op.batch_alter_table('banks', schema=None) as b:
        b.drop_column('window')
    with op.batch_alter_table('devices', schema=None) as b:
        b.drop_column('series')
        b.drop_column('station')
        b.drop_column('role')
