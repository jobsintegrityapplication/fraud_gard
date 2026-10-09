"""continuous_learning

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'training_label_queue',
        sa.Column('queue_id', sa.String(length=32), nullable=False),
        sa.Column('case_id', sa.String(length=32), nullable=False),
        sa.Column('transaction_id', sa.String(length=32), nullable=True),
        sa.Column('decision', sa.String(length=32), nullable=False),
        sa.Column('label', sa.Integer(), nullable=True),
        sa.Column('reviewer', sa.String(length=64), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('evidence_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint('label IN (0, 1) OR label IS NULL', name='ck_tlq_label'),
        sa.CheckConstraint(
            "status IN ('QUEUED','CLAIMED','QUALITY_FAILED','READY','USED_IN_TRAINING','REJECTED')",
            name='ck_tlq_status',
        ),
        sa.PrimaryKeyConstraint('queue_id'),
    )
    with op.batch_alter_table('training_label_queue', schema=None) as batch_op:
        batch_op.create_index('ix_tlq_status', ['status'], unique=False)
        batch_op.create_index('ix_tlq_case', ['case_id'], unique=False)

    op.create_table(
        'learning_jobs',
        sa.Column('job_id', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('stages_json', sa.JSON(), nullable=True),
        sa.Column('result_json', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('job_id'),
    )
    with op.batch_alter_table('learning_jobs', schema=None) as batch_op:
        batch_op.create_index('ix_learning_jobs_status', ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('learning_jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_learning_jobs_status')
    op.drop_table('learning_jobs')
    with op.batch_alter_table('training_label_queue', schema=None) as batch_op:
        batch_op.drop_index('ix_tlq_case')
        batch_op.drop_index('ix_tlq_status')
    op.drop_table('training_label_queue')
