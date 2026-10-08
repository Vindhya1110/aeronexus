"""add derived_measurements.aqi_detail

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("derived_measurements") as b:
        b.add_column(sa.Column("aqi_detail", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("derived_measurements") as b:
        b.drop_column("aqi_detail")
