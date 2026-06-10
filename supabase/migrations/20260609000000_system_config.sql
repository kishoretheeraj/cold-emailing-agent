CREATE TABLE IF NOT EXISTS system_config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO system_config (key, value)
VALUES ('pause_scope', 'none')
ON CONFLICT (key) DO NOTHING;
