"""Baseline backend schema.

Revision ID: 20260405_0001
Revises:
Create Date: 2026-04-05 00:00:00
"""

from __future__ import annotations

from alembic import op


revision = "20260405_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS menu (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          brand VARCHAR NOT NULL,
          name VARCHAR NOT NULL,
          size VARCHAR,
          price DECIMAL(8,2),
          description TEXT,
          item_type VARCHAR(24),
          drink_category VARCHAR(32),
          sugar_opts TEXT[],
          ice_opts TEXT[],
          is_active BOOLEAN DEFAULT TRUE,
          created_at TIMESTAMP DEFAULT NOW(),
          updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile (
          user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          username VARCHAR UNIQUE NOT NULL,
          password_hash VARCHAR NOT NULL,
          favorite_brand VARCHAR,
          sugar_preference VARCHAR,
          ice_preference VARCHAR,
          avg_price DECIMAL,
          total_count INTEGER DEFAULT 0,
          nickname VARCHAR(80),
          updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          user_id UUID REFERENCES user_profile(user_id),
          menu_id UUID REFERENCES menu(id),
          brand VARCHAR NOT NULL,
          name VARCHAR NOT NULL,
          sugar VARCHAR,
          ice VARCHAR,
          mood VARCHAR(120),
          price DECIMAL(8,2),
          photo_url TEXT,
          source VARCHAR CHECK (source IN ('manual','photo','screenshot','agent')),
          notes TEXT,
          consumed_at TIMESTAMP NOT NULL,
          created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS record_photos (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          record_id UUID NOT NULL REFERENCES records(id) ON DELETE CASCADE,
          photo_url TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute("ALTER TABLE menu ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE menu ADD COLUMN IF NOT EXISTS item_type VARCHAR(24)")
    op.execute("ALTER TABLE menu ADD COLUMN IF NOT EXISTS drink_category VARCHAR(32)")
    op.execute("ALTER TABLE user_profile ADD COLUMN IF NOT EXISTS nickname VARCHAR(80)")
    op.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS user_id UUID")
    op.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS mood VARCHAR(120)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_threads (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
          thread_key VARCHAR(255) NOT NULL UNIQUE,
          title VARCHAR(120),
          status VARCHAR(24) NOT NULL DEFAULT 'active',
          message_count INT NOT NULL DEFAULT 0,
          last_user_message_at TIMESTAMPTZ,
          last_agent_message_at TIMESTAMPTZ,
          last_summary_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          archived_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_thread_messages (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          thread_id UUID NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
          role VARCHAR(24) NOT NULL,
          content TEXT NOT NULL,
          content_type VARCHAR(24) NOT NULL DEFAULT 'text',
          request_id VARCHAR(64),
          tool_name VARCHAR(80),
          tool_call_id VARCHAR(120),
          source VARCHAR(24) NOT NULL DEFAULT 'agent',
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_thread_summaries (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          thread_id UUID NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
          user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
          summary_type VARCHAR(24) NOT NULL,
          summary_text TEXT NOT NULL,
          open_slots JSONB NOT NULL DEFAULT '[]'::jsonb,
          covered_message_count INT NOT NULL DEFAULT 0,
          token_estimate INT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_memory_profile (
          user_id UUID PRIMARY KEY REFERENCES user_profile(user_id) ON DELETE CASCADE,
          profile_version INT NOT NULL DEFAULT 1,
          display_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
          drink_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
          interaction_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
          budget_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
          health_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
          memory_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_memory_items (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
          memory_type VARCHAR(32) NOT NULL,
          scope VARCHAR(32) NOT NULL,
          content TEXT NOT NULL,
          normalized_fact JSONB,
          source_kind VARCHAR(32) NOT NULL,
          source_ref VARCHAR(255),
          confidence NUMERIC(4,3) NOT NULL DEFAULT 0.500,
          salience NUMERIC(4,3) NOT NULL DEFAULT 0.500,
          status VARCHAR(24) NOT NULL DEFAULT 'active',
          expires_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          last_used_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_write_jobs (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
          thread_id UUID REFERENCES agent_threads(id) ON DELETE SET NULL,
          job_type VARCHAR(32) NOT NULL,
          payload JSONB NOT NULL,
          status VARCHAR(24) NOT NULL DEFAULT 'pending',
          attempt_count INT NOT NULL DEFAULT 0,
          last_error TEXT,
          scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_daily_llm_usage (
          user_id UUID NOT NULL REFERENCES user_profile(user_id) ON DELETE CASCADE,
          usage_date DATE NOT NULL,
          model VARCHAR(120) NOT NULL,
          input_tokens BIGINT NOT NULL DEFAULT 0,
          output_tokens BIGINT NOT NULL DEFAULT 0,
          estimated_cost_cny NUMERIC(12,6) NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (user_id, usage_date, model)
        )
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS idx_records_consumed_at ON records(consumed_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_records_user_consumed_at ON records(user_id, consumed_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_records_date ON records(DATE(consumed_at))")
    op.execute("CREATE INDEX IF NOT EXISTS idx_record_photos_record_sort ON record_photos(record_id, sort_order, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_threads_user_updated_at ON agent_threads(user_id, updated_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_thread_messages_thread_created_at ON agent_thread_messages(thread_id, created_at ASC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_thread_summaries_thread_created_at ON agent_thread_summaries(thread_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_memory_items_user_status ON user_memory_items(user_id, status, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_memory_write_jobs_status_scheduled_at ON memory_write_jobs(status, scheduled_at ASC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_daily_llm_usage_user_date ON user_daily_llm_usage(user_id, usage_date DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_user_daily_llm_usage_user_date")
    op.execute("DROP INDEX IF EXISTS idx_memory_write_jobs_status_scheduled_at")
    op.execute("DROP INDEX IF EXISTS idx_user_memory_items_user_status")
    op.execute("DROP INDEX IF EXISTS idx_agent_thread_summaries_thread_created_at")
    op.execute("DROP INDEX IF EXISTS idx_agent_thread_messages_thread_created_at")
    op.execute("DROP INDEX IF EXISTS idx_agent_threads_user_updated_at")
    op.execute("DROP INDEX IF EXISTS idx_record_photos_record_sort")
    op.execute("DROP INDEX IF EXISTS idx_records_date")
    op.execute("DROP INDEX IF EXISTS idx_records_user_consumed_at")
    op.execute("DROP INDEX IF EXISTS idx_records_consumed_at")

    op.execute("DROP TABLE IF EXISTS user_daily_llm_usage")
    op.execute("DROP TABLE IF EXISTS memory_write_jobs")
    op.execute("DROP TABLE IF EXISTS user_memory_items")
    op.execute("DROP TABLE IF EXISTS user_memory_profile")
    op.execute("DROP TABLE IF EXISTS agent_thread_summaries")
    op.execute("DROP TABLE IF EXISTS agent_thread_messages")
    op.execute("DROP TABLE IF EXISTS agent_threads")
    op.execute("DROP TABLE IF EXISTS record_photos")

    op.execute("ALTER TABLE records DROP COLUMN IF EXISTS mood")
    op.execute("ALTER TABLE records DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE user_profile DROP COLUMN IF EXISTS nickname")
    op.execute("ALTER TABLE menu DROP COLUMN IF EXISTS description")
