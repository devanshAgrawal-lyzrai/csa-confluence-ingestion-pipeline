import hashlib
import logging
import os
import time

from confluence_ingestion import confluence_client as cc_mod
from confluence_ingestion import db
from confluence_ingestion import kb_client as kb_mod
from confluence_ingestion import lyzr_client as lyzr_mod
from confluence_ingestion import parser

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))
DEFAULT_CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))


def orchestrate_space(
    space_key: str,
    confluence: cc_mod.ConfluenceClient,
    lyzr: lyzr_mod.LyzrClient,
    kb: kb_mod.KBClient,
    conn,
    dry_run: bool = False,
) -> None:
    failed_pages = []
    total = 0
    skipped = 0

    for page_meta in confluence.get_all_pages(space_key):
        total += 1
        page_id = page_meta["page_id"]
        if not page_id:
            logger.warning("Skipping page with no ID: %s", page_meta)
            continue

        try:
            was_skipped = process_page(
                page_meta=page_meta,
                space_key=space_key,
                confluence=confluence,
                lyzr=lyzr,
                kb=kb,
                conn=conn,
                dry_run=dry_run,
            )
            if was_skipped:
                skipped += 1
        except Exception as exc:
            logger.error("[%s] Failed: %s", page_id, exc, exc_info=True)
            failed_pages.append({
                "page_id": page_id,
                "title": page_meta.get("title"),
                "error": str(exc),
            })

    logger.info(
        "Space %s done. total=%d  skipped=%d  failed=%d",
        space_key, total, skipped, len(failed_pages),
    )
    if failed_pages:
        logger.warning("Failed pages:")
        for f in failed_pages:
            logger.warning("  [%s] %s — %s", f["page_id"], f["title"], f["error"])


def process_page(
    page_meta: dict,
    space_key: str,
    confluence: cc_mod.ConfluenceClient,
    lyzr: lyzr_mod.LyzrClient,
    kb: kb_mod.KBClient,
    conn,
    dry_run: bool = False,
) -> bool:
    """Returns True if the page was skipped (hash unchanged)."""
    page_id = page_meta["page_id"]
    title = page_meta.get("title", "")
    page_url = page_meta.get("url", "")
    logger.info("[%s] Starting: %s", page_id, title)

    # --- 1. Fetch raw content ---
    content = confluence.get_page_content(page_id)
    xhtml = content["xhtml"]
    version = content["version"]
    labels = content["labels"]

    # --- 2. Hash check ---
    content_hash = hashlib.sha256(xhtml.encode("utf-8")).hexdigest()
    existing = db.get_page(conn, page_id) if not dry_run else None
    if existing and existing.get("content_hash") == content_hash:
        logger.info("[%s] Unchanged, skipping.", page_id)
        return True

    # --- 3. Parse XHTML → structured JSON → plain text ---
    document = parser.parse_storage_format(
        xhtml=xhtml, page_id=page_id, title=title, space_key=space_key
    )
    plain_text = parser.to_plain_text(document)

    if not plain_text.strip():
        logger.warning("[%s] Page produced empty plain text, skipping.", page_id)
        return False

    # --- 4. Lyzr Agent: summary + tags + relevancy score ---
    session_id = f"ingestion-{page_id}-{int(time.time())}"
    intelligence = {"summary": "", "tags": [], "relevancy_score": 0.0}
    if not dry_run:
        try:
            intelligence = lyzr.get_page_intelligence(plain_text, session_id)
            logger.info(
                "[%s] Agent: relevancy=%.2f  tags=%s",
                page_id, intelligence["relevancy_score"], intelligence["tags"],
            )
        except Exception as exc:
            logger.warning("[%s] Agent call failed (continuing): %s", page_id, exc)
    else:
        logger.debug("[%s] dry-run: skipping agent call", page_id)

    # --- 5. KB Parse: plain text → size-based chunks ---
    raw_chunks = []
    if not dry_run:
        raw_chunks = kb.parse_text(
            text=plain_text,
            source=page_id,
            chunk_size=DEFAULT_CHUNK_SIZE,
            chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        )
    else:
        logger.debug("[%s] dry-run: skipping KB parse_text call", page_id)
        raw_chunks = [{"text": plain_text[:500], "source": page_id, "extra_info": {}}]

    if not raw_chunks:
        logger.warning("[%s] KB parse returned zero chunks, skipping train + DB write.", page_id)
        return False

    logger.info("[%s] KB parse returned %d chunks.", page_id, len(raw_chunks))

    # --- 6. Assign unique source UUIDs + extra_info to each chunk ---
    prepared_chunks = []
    for i, raw in enumerate(raw_chunks):
        chunk_id = hashlib.sha256(f"{page_id}:{chunk_text}".encode("utf-8")).hexdigest()
        prepared_chunks.append({
            "text": raw.get("text", ""),
            "source": chunk_id,
            "extra_info": {
                "page_id": page_id,
                "page_title": title,
                "space_key": space_key,
                "page_url": page_url,
                "labels": labels,
                "tags": intelligence["tags"],
                "chunk_index": i,
            },
        })

    # --- 7. KB Train: store chunks in vector DB ---
    if not dry_run:
        kb.train(prepared_chunks)
        logger.info("[%s] KB train complete (%d chunks).", page_id, len(prepared_chunks))
    else:
        logger.debug("[%s] dry-run: skipping KB train call", page_id)

    # --- 8. PostgreSQL upsert ---
    page_record = {
        "page_id": page_id,
        "space_key": space_key,
        "title": title,
        "page_url": page_url,
        "content_hash": content_hash,
        "confluence_version": version,
        "metadata": {
            "labels": labels,
            "summary": intelligence["summary"],
            "tags": intelligence["tags"],
            "relevancy_score": intelligence["relevancy_score"],
            "chunk_count": len(prepared_chunks),
            "chunks": [
                {
                    "chunk_id": c["source"],
                    "chunk_index": c["extra_info"]["chunk_index"],
                    "chunk_text_preview": c["text"][:200],
                }
                for c in prepared_chunks
            ],
        },
    }

    if not dry_run:
        db.upsert_page(conn, page_record)
        logger.info("[%s] Done. %d chunks ingested.", page_id, len(prepared_chunks))
    else:
        logger.info(
            "[%s] dry-run complete. Would ingest %d chunks. summary=%r",
            page_id, len(prepared_chunks), intelligence["summary"][:80] if intelligence["summary"] else "",
        )

    return False
