CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS menu (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  brand        VARCHAR NOT NULL,
  name         VARCHAR NOT NULL,
  size         VARCHAR,
  price        DECIMAL(8,2),
  description  TEXT,
  sugar_opts   TEXT[],
  ice_opts     TEXT[],
  is_active    BOOLEAN DEFAULT TRUE,
  created_at   TIMESTAMP DEFAULT NOW(),
  updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_profile (
  user_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  username          VARCHAR UNIQUE NOT NULL,
  password_hash     VARCHAR NOT NULL,
  favorite_brand    VARCHAR,
  sugar_preference  VARCHAR,
  ice_preference    VARCHAR,
  avg_price         DECIMAL,
  total_count       INTEGER DEFAULT 0,
  updated_at        TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS records (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id      UUID NOT NULL REFERENCES user_profile(user_id),
  menu_id      UUID REFERENCES menu(id),
  brand        VARCHAR NOT NULL,
  name         VARCHAR NOT NULL,
  sugar        VARCHAR,
  ice          VARCHAR,
  mood         VARCHAR(120),
  price        DECIMAL(8,2),
  photo_url    TEXT,
  source       VARCHAR CHECK (source IN ('manual','photo','screenshot','agent')),
  notes        TEXT,
  consumed_at  TIMESTAMP NOT NULL,
  created_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS record_photos (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  record_id    UUID NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  photo_url    TEXT NOT NULL,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_records_consumed_at ON records(consumed_at);
CREATE INDEX IF NOT EXISTS idx_records_user_consumed_at ON records(user_id, consumed_at);
CREATE INDEX IF NOT EXISTS idx_records_date ON records(DATE(consumed_at));
CREATE INDEX IF NOT EXISTS idx_record_photos_record_sort ON record_photos(record_id, sort_order, created_at);
