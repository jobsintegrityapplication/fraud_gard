"""pattern_registry

Revision ID: a8e4f1b90c2d
Revises: 7b7a7c4c0b7c
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8e4f1b90c2d'
down_revision: Union[str, Sequence[str], None] = '7b7a7c4c0b7c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('fraud_patterns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('supporting_cases_json', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('supporting_evidence_json', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('threshold', sa.Float(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('policy_action', sa.String(length=16), server_default='ALERT', nullable=False))
        batch_op.add_column(sa.Column('owner', sa.String(length=64), nullable=True))
        batch_op.alter_column('state', existing_type=sa.String(length=16), type_=sa.String(length=24), existing_nullable=False)
        batch_op.drop_constraint('ck_fraud_patterns_state', type_='check')
        batch_op.create_check_constraint(
            'ck_fraud_patterns_state',
            "state IN ('DISCOVERED','UNDER_REVIEW','CONFIRMED','ACTIVE','MONITORED','DEPRECATED')",
        )
        batch_op.create_check_constraint(
            'ck_fraud_patterns_policy_action',
            "policy_action IN ('ALERT','BLOCK','STEP_UP','REVIEW')",
        )


def downgrade() -> None:
    with op.batch_alter_table('fraud_patterns', schema=None) as batch_op:
        batch_op.drop_constraint('ck_fraud_patterns_policy_action', type_='check')
        batch_op.drop_constraint('ck_fraud_patterns_state', type_='check')
        batch_op.create_check_constraint(
            'ck_fraud_patterns_state',
            "state IN ('DISCOVERED','CANDIDATE','CONFIRMED','RETIRED')",
        )
        batch_op.drop_column('owner')
        batch_op.drop_column('policy_action')
        batch_op.drop_column('threshold')
        batch_op.drop_column('supporting_evidence_json')
        batch_op.drop_column('supporting_cases_json')
        batch_op.alter_column('state', existing_type=sa.String(length=24), type_=sa.String(length=16), existing_nullable=False)
