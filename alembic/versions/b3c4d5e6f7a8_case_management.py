"""case_management

Revision ID: b3c4d5e6f7a8
Revises: a8e4f1b90c2d
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'a8e4f1b90c2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('fraud_cases', schema=None) as batch_op:
        batch_op.add_column(sa.Column('evidence_version', sa.Integer(), server_default='1', nullable=False))
        batch_op.alter_column('status', existing_type=sa.String(length=16), type_=sa.String(length=24), existing_nullable=False)
        batch_op.drop_constraint('ck_fraud_cases_status', type_='check')
        batch_op.create_check_constraint(
            'ck_fraud_cases_status',
            "status IN ('OPEN','INVESTIGATING','PENDING_REVIEW','CONFIRMED_FRAUD','NOT_FRAUD','NEED_MORE_EVIDENCE','CLOSED')",
        )

    with op.batch_alter_table('evidence', schema=None) as batch_op:
        batch_op.add_column(sa.Column('evidence_version', sa.Integer(), server_default='1', nullable=False))
        batch_op.drop_constraint('ck_evidence_type', type_='check')
        batch_op.create_check_constraint(
            'ck_evidence_type',
            "evidence_type IN ('GRAPH','MODEL','PATTERN','DOCUMENT','RULE','ALERT')",
        )

    with op.batch_alter_table('analyst_decisions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reviewer', sa.String(length=64), server_default='system', nullable=False))
        batch_op.add_column(sa.Column('evidence_version', sa.Integer(), server_default='1', nullable=False))
        batch_op.alter_column('reason', existing_type=sa.Text(), nullable=False, server_default='')
        batch_op.drop_constraint('ck_analyst_decision', type_='check')
        batch_op.create_check_constraint(
            'ck_analyst_decision',
            "decision IN ('CONFIRMED_FRAUD','NOT_FRAUD','NEED_MORE_EVIDENCE','INVESTIGATING','PENDING_REVIEW','CLOSED')",
        )

    op.create_table(
        'case_audit_events',
        sa.Column('audit_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('case_id', sa.String(length=32), nullable=False),
        sa.Column('actor', sa.String(length=64), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('from_status', sa.String(length=24), nullable=True),
        sa.Column('to_status', sa.String(length=24), nullable=True),
        sa.Column('evidence_version', sa.Integer(), nullable=True),
        sa.Column('decision_id', sa.String(length=32), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['fraud_cases.case_id']),
        sa.PrimaryKeyConstraint('audit_id'),
    )
    with op.batch_alter_table('case_audit_events', schema=None) as batch_op:
        batch_op.create_index('ix_case_audit_case', ['case_id'], unique=False)
        batch_op.create_index('ix_case_audit_ts', ['ts'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('case_audit_events', schema=None) as batch_op:
        batch_op.drop_index('ix_case_audit_ts')
        batch_op.drop_index('ix_case_audit_case')
    op.drop_table('case_audit_events')
    with op.batch_alter_table('analyst_decisions', schema=None) as batch_op:
        batch_op.drop_constraint('ck_analyst_decision', type_='check')
        batch_op.create_check_constraint(
            'ck_analyst_decision',
            "decision IN ('CONFIRMED_FRAUD','NOT_FRAUD','NEED_MORE_EVIDENCE','UNCERTAIN')",
        )
        batch_op.drop_column('evidence_version')
        batch_op.drop_column('reviewer')
    with op.batch_alter_table('evidence', schema=None) as batch_op:
        batch_op.drop_constraint('ck_evidence_type', type_='check')
        batch_op.create_check_constraint(
            'ck_evidence_type',
            "evidence_type IN ('GRAPH','MODEL','PATTERN','DOCUMENT','RULE')",
        )
        batch_op.drop_column('evidence_version')
    with op.batch_alter_table('fraud_cases', schema=None) as batch_op:
        batch_op.drop_constraint('ck_fraud_cases_status', type_='check')
        batch_op.create_check_constraint(
            'ck_fraud_cases_status',
            "status IN ('OPEN','REVIEW','CLOSED','ESCALATED')",
        )
        batch_op.drop_column('evidence_version')
