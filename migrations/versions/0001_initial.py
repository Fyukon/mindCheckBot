"""
Initial tables
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tg_user_id', sa.BigInteger(), nullable=False, unique=True, index=True),
        sa.Column('language_code', sa.String(length=8), nullable=True),
        sa.Column('timezone', sa.String(length=64), nullable=False, server_default='Europe/Moscow'),
        sa.Column('checkin_time', sa.String(length=5), nullable=False, server_default='18:00'),
        sa.Column('consent_given', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    op.create_table(
        'checkins',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), index=True, nullable=False),
        sa.Column('date', sa.DateTime(), index=True, nullable=False),
        sa.Column('mood_score', sa.Integer(), nullable=True),
        sa.Column('stress_score', sa.Integer(), nullable=True),
        sa.Column('energy_score', sa.Integer(), nullable=True),
        sa.Column('emotions', sa.Text(), nullable=True),
        sa.Column('sleep_hours', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('analysis_summary', sa.Text(), nullable=True),
        sa.Column('recommendations', sa.Text(), nullable=True),
    )

    op.create_unique_constraint('uq_checkin_user_date', 'checkins', ['user_id', 'date'])

    op.create_table(
        'reminders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), index=True, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('times', sa.String(length=64), nullable=False, server_default='18:00'),
    )


def downgrade():
    op.drop_table('reminders')
    op.drop_constraint('uq_checkin_user_date', 'checkins', type_='unique')
    op.drop_table('checkins')
    op.drop_table('users')
