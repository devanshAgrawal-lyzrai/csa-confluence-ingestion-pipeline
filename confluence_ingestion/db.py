import json
import logging
import os
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


def get_db_connection():
    url = os.environ["DATABASE_URL"]
    conn = psycopg2.connect(url)
    conn.autocommit = False
    return conn


def get_page(conn, page_id: str) -> Optional[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM pages WHERE page_id = %s", (page_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_page(conn, page: dict) -> None:
    """
    page dict keys:
        page_id, space_key, title, page_url,
        content_hash, confluence_version, metadata (dict)
    """
    sql = """
        INSERT INTO pages
            (page_id, space_key, title, page_url, content_hash,
             confluence_version, last_synced_at, metadata, created_at, updated_at)
        VALUES
            (%(page_id)s, %(space_key)s, %(title)s, %(page_url)s, %(content_hash)s,
             %(confluence_version)s, NOW(), %(metadata)s, NOW(), NOW())
        ON CONFLICT (page_id) DO UPDATE SET
            space_key          = EXCLUDED.space_key,
            title              = EXCLUDED.title,
            page_url           = EXCLUDED.page_url,
            content_hash       = EXCLUDED.content_hash,
            confluence_version = EXCLUDED.confluence_version,
            last_synced_at     = NOW(),
            metadata           = EXCLUDED.metadata,
            updated_at         = NOW()
    """
    params = dict(page)
    # psycopg2 needs JSONB as a string or via Json adapter
    params["metadata"] = psycopg2.extras.Json(page.get("metadata", {}))

    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()
    logger.debug("Upserted page %s (%s)", page["page_id"], page.get("title"))
