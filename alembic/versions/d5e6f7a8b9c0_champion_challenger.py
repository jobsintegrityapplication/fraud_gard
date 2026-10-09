"""champion_challenger

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'model_registry',
        sa.Column('model_id', sa.String(length=32), nullable=False),
        sa.Column('version', sa.String(length=64), nullable=False),
        sa.Column('algorithm', sa.String(length=64), nullable=False),
        sa.Column('dataset_version', sa.String(length=64), nullable=True),
        sa.Column('feature_version', sa.String(length=64), nullable=True),
        sa.Column('training_timestamp', sa.String(length=64), nullable=True),
        sa.Column('metrics_json', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('artifact_uri', sa.String(length=512), nullable=True),
        sa.Column('gate_results_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint(
            "status IN ('TRAINING','VALIDATING','FAILED','SHADOW','CANARY','PRODUCTION','ROLLED_BACK')",
            name='ck_model_registry_status',
        ),
        sa.PrimaryKeyConstraint('model_id'),
    )
    with op.batch_alter_table('model_versions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dataset_version', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('feature_version', sa.String(length=64), nullable=True))
        batch_op.alter_column('status', existing_type=sa.String(length=16), type_=sa.String(length=24), existing_nullable=False)
        batch_op.drop_constraint('ck_model_status', type_='check')
        batch_op.create_check_constraint(
            'ck_model_status',
            "status IN ('TRAINING','VALIDATING','FAILED','SHADOW','CANARY','PRODUCTION','ROLLED_BACK','CHALLENGER','CHAMPION','RETIRED')",
        )


def downgrade() -> None:
    with op.batch_alter_table('model_versions', schema=None) as batch_op:
        batch_op.drop_constraint('ck_model_status', type_='check')
        batch_op.create_check_constraint(
            'ck_model_status',
            "status IN ('CHALLENGER','CHAMPION','SHADOW','RETIRED')",
        )
        batch_op.drop_column('feature_version')
        batch_op.drop_column('dataset_version')
    op.drop_table('model_registry')
