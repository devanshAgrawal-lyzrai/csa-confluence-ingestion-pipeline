---
name: project-pipeline-context
description: "Core facts about the pipeline-1 project — what it does, its APIs, and where things live"
metadata: 
  node_type: memory
  type: project
  originSessionId: efcecd6d-c06d-4bb1-a40b-7d3c9f351938
---

RAG ingestion pipeline: Confluence Cloud → Lyzr Agent (chunking/tagging) → Lyzr KB (vector DB) → PostgreSQL.

**Why:** RAG chatbot for VIA Benefits employees answering benefits questions. Source space: CKB on viabenefits.atlassian.net.

**How to apply:** When asked about any new feature, always consider the single `pages` table schema (with `metadata JSONB` for chunks) and the Lyzr agent/KB API pattern.

Key facts:
- Single DB table: `pages` with `metadata JSONB` (stores chunks + vector_db_ids from KB)
- No separate chunks table — chunks live inside `pages.metadata`
- Lyzr agent endpoint: `POST https://agent-prod.studio.lyzr.ai/v3/inference/chat/`
- KB endpoint: `POST {LYZR_KB_BASE_URL}/v3/rag/train/{LYZR_KB_ID}/`
- Entry point: `python run_ingestion.py --space CKB`
- Full context in `PIPELINE_CONTEXT.md`
