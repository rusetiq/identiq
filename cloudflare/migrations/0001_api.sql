CREATE TABLE api_keys (id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL, scope TEXT NOT NULL CHECK(scope IN ('read','write','scanner')), source TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE snapshots (source TEXT NOT NULL, kind TEXT NOT NULL, entity TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(source,kind,entity));
CREATE TABLE commands (id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', result TEXT, created_at TEXT NOT NULL);
CREATE INDEX pending_commands ON commands(source,status,created_at);
CREATE TABLE receipts (id TEXT PRIMARY KEY, source TEXT NOT NULL, created_at TEXT NOT NULL);
