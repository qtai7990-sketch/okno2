"""v0.3.0: агент 1-го этажа и изолированное pre-case
Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31 22:25:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.add_column(sa.Column("floor1_device_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_cases_floor1_device_id"), ["floor1_device_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cases_floor1_device_id"))
        batch_op.drop_column("floor1_device_id")
