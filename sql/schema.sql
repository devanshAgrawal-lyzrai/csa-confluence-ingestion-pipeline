CREATE TABLE IF NOT EXISTS pages (
    page_id            VARCHAR(100) PRIMARY KEY,
    space_key          VARCHAR(50),
    title              TEXT,
    page_url           TEXT,
    content_hash       TEXT,
    confluence_version INT,
    last_synced_at     TIMESTAMP,
    metadata           JSONB,
    created_at         TIMESTAMP DEFAULT NOW(),
    updated_at         TIMESTAMP DEFAULT NOW()
);
