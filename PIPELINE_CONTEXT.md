# PIPELINE_CONTEXT.md

## What This System Does

This pipeline ingests content from a Confluence Cloud space into a Lyzr-hosted Knowledge Base (vector DB) so that a RAG chatbot can answer questions with source citations.

Full data flow:

```
Confluence Space (CKB)
    → Fetch all pages (REST API, paginated)
    → Parse XHTML storage format (BeautifulSoup)
    → Internal structured JSON (sections, headings, tables)
    → Lyzr Agent API (semantic chunking + tag generation)
    → Lyzr KB API (store each chunk in vector DB)
    → PostgreSQL pages table (track sync state + chunk metadata)
```

---

## APIs

### 1. Confluence REST API

**Authentication:** HTTP Basic Auth — Atlassian account email + API token.
Generate token at: https://id.atlassian.com/manage-profile/security/api-tokens

**Discover pages in a space:**
```
GET {CONFLUENCE_BASE_URL}/wiki/rest/api/search?cql=space=CKB%20and%20type=page&limit=100&start=0
```
Returns: `page_id`, `title`, `_links.webui` (page URL), `version.number`

Pagination: increment `start` by 100 until `len(results) < limit`.

**Fetch full page content:**
```
GET {CONFLUENCE_BASE_URL}/wiki/rest/api/content/{page_id}?expand=body.storage,version,metadata.labels
```
Key fields:
- `body.storage.value` — XHTML-like Confluence storage format (the raw content)
- `version.number` — integer version for sync tracking
- `metadata.labels.results[].name` — Confluence labels (used as tags if present)

---

### 2. Lyzr Agent API (Chunking + Tagging)

**Endpoint:**
```
POST https://agent-prod.studio.lyzr.ai/v3/inference/chat/
```

**Headers:**
```
Content-Type: application/json
x-api-key: {LYZR_API_KEY}
```

**Request body:**
```json
{
  "user_id": "{LYZR_USER_ID}",
  "agent_id": "{LYZR_AGENT_ID}",
  "session_id": "ingestion-{page_id}-{timestamp}",
  "message": "<see prompt template below>"
}
```

**Message prompt template** (sent in the `message` field):
```
You are a knowledge base ingestion assistant.

You will receive a structured JSON document extracted from Confluence.

Your task:
1. Split the document into semantic chunks. Each chunk corresponds to one section or meaningful sub-section. Do NOT split mid-sentence or mid-table.
2. For each chunk generate 3-7 relevant topic tags (e.g. "eligibility", "healthcare", "claims", "dental", "vision").
3. Format each chunk_text as:
   Title: {page_title}

   Section: {section_heading}

   {content text, tables formatted as markdown}

4. Return ONLY a valid JSON object with NO additional commentary, no markdown fences:

{
  "chunks": [
    {
      "chunk_index": 0,
      "section_heading": "Section Name",
      "chunk_text": "Title: ...\n\nSection: ...\n\n...",
      "tags": ["tag1", "tag2"]
    }
  ]
}

Document JSON:
<structured page JSON here>
```

**Expected agent response:**
```json
{
  "chunks": [
    {
      "chunk_index": 0,
      "section_heading": "Eligibility",
      "chunk_text": "Title: Medical Health Care Expenses\n\nSection: Eligibility\n\nEmployees qualify after 90 days.",
      "tags": ["eligibility", "healthcare"]
    }
  ]
}
```

**Lyzr Studio — Agent System Prompt to configure:**
```
You are a document chunking and tagging assistant for a knowledge base.
Always respond with valid JSON only.
Never include markdown fences, preamble, or explanation.
Your entire response must be parseable by json.loads().
```

---

### 3. Lyzr KB API (Vector DB)

**Endpoint:**
```
POST {LYZR_KB_BASE_URL}/v3/rag/train/{LYZR_KB_ID}/
```

**Headers:**
```
Content-Type: application/json
x-api-key: {LYZR_API_KEY}
```

**Request body:**
```json
{
  "text": "<chunk_text>",
  "metadata": {
    "chunk_id": "uuid",
    "page_id": "...",
    "page_title": "...",
    "space_key": "CKB",
    "section_heading": "...",
    "page_url": "...",
    "tags": ["tag1", "tag2"]
  }
}
```

**Response:** Contains a `vector_db_id` (the ID assigned by the vector DB, used to update/delete the record later). The exact response field name (`id`, `vector_id`, etc.) should be confirmed on first test run.

---

## Database

Single PostgreSQL table. No ORM — plain `psycopg2`.

```sql
CREATE TABLE IF NOT EXISTS pages (
    page_id         VARCHAR(100) PRIMARY KEY,
    space_key       VARCHAR(50),
    title           TEXT,
    page_url        TEXT,
    content_hash    TEXT,
    confluence_version INT,
    last_synced_at  TIMESTAMP,
    metadata        JSONB,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);
```

`metadata` JSONB structure stored per page:
```json
{
  "labels": ["benefits", "healthcare"],
  "chunks": [
    {
      "chunk_id": "uuid",
      "chunk_index": 0,
      "section_heading": "Eligibility",
      "chunk_text": "...",
      "chunk_hash": "sha256hex",
      "vector_db_id": "kb-assigned-id",
      "tags": ["eligibility", "healthcare"]
    }
  ]
}
```

---

## Internal Document Model (parser output)

The XML parser converts Confluence storage format to this structure before sending to the Lyzr agent:

```json
{
  "page_id": "149881427",
  "title": "Medical Health Care Expenses",
  "space_key": "CKB",
  "sections": [
    {
      "heading": "Eligibility",
      "level": 1,
      "content": [
        { "type": "paragraph", "text": "Employees qualify after 90 days." },
        {
          "type": "table",
          "rows": [
            ["Expense", "Covered"],
            ["Dental", "Yes"],
            ["Vision", "No"]
          ]
        }
      ]
    }
  ]
}
```

Tables are converted to markdown in `chunk_text`. Confluence macros (`ac:structured-macro`) are captured as `{"type": "macro", "name": "...", "text": "..."}`.

---

## Environment Variables

| Variable | Description |
|---|---|
| `CONFLUENCE_BASE_URL` | e.g. `https://viabenefits.atlassian.net` |
| `CONFLUENCE_EMAIL` | Your Atlassian account email |
| `CONFLUENCE_API_TOKEN` | From https://id.atlassian.com/manage-profile/security/api-tokens |
| `LYZR_API_KEY` | Lyzr Studio API key |
| `LYZR_AGENT_ID` | ID of the chunking/tagging agent |
| `LYZR_USER_ID` | Lyzr user ID |
| `LYZR_KB_BASE_URL` | e.g. `https://agent-prod.studio.lyzr.ai` |
| `LYZR_KB_ID` | Knowledge base ID in Lyzr Studio |
| `DATABASE_URL` | e.g. `postgresql://postgres:postgres@localhost:5432/kb_ingestion` |
| `LOG_LEVEL` | `INFO` (default) or `DEBUG` |

---

## Running the Pipeline

```bash
# One-time: create the DB table
psql $DATABASE_URL -f sql/schema.sql

# Install deps
pip install -r requirements.txt

# Copy and fill env vars
cp .env.example .env

# Dry run (no DB or KB writes — just logs what would happen)
python run_ingestion.py --space CKB --dry-run

# Full run
python run_ingestion.py --space CKB

# Verbose debug output
python run_ingestion.py --space CKB --verbose
```

---

## Incremental Sync

Re-running the pipeline is safe. For each page:

1. Fetch `body.storage.value` from Confluence
2. Compute `sha256(xhtml)` and compare to `pages.content_hash`
3. If hash matches → log `skipping unchanged page` and move on
4. If hash differs → re-parse, re-chunk, re-upsert to KB, overwrite `pages` row

Old KB vectors from a previous version of a page are replaced (upserted by `chunk_id`) if `chunk_id` generation is deterministic — currently UUIDs are random per run, so re-sync creates new KB records. A future improvement is to derive `chunk_id` from a hash of `page_id + chunk_index` for stable IDs.

---

## File Structure

```
pipeline-1/
├── confluence_ingestion/
│   ├── __init__.py
│   ├── confluence_client.py    # Confluence REST API (pagination, retry)
│   ├── parser.py               # XHTML → internal doc JSON
│   ├── lyzr_client.py          # Lyzr agent API (chunking + tagging)
│   ├── kb_client.py            # Lyzr KB API (vector DB upsert)
│   ├── db.py                   # PostgreSQL upsert_page
│   └── pipeline.py             # Orchestration loop
├── sql/
│   └── schema.sql
├── .env.example
├── requirements.txt
└── run_ingestion.py
```
