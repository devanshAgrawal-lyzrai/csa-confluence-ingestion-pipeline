# PIPELINE_CONTEXT_v2.md

> Supersedes PIPELINE_CONTEXT.md.
> Key change: chunking is now handled by the Lyzr KB Parse API (size-based). The Lyzr Agent is retained for page-level intelligence (summary, tags, relevancy score).

---

## What This System Does

Ingests Confluence Cloud content into a Lyzr Knowledge Base (vector DB) so a RAG chatbot can answer questions with source citations.

```
Confluence Space (CKB)
    → Fetch all pages (REST API, paginated)
    → Parse XHTML storage format (BeautifulSoup → plain text)
    → Lyzr Agent API  (plain text → summary + tags + relevancy score)
    → Lyzr Parse API  (plain text → size-based chunks)
    → Assign unique chunk IDs + metadata (incl. tags) to each chunk
    → Lyzr KB Train API  (chunks → embeddings + vector storage)
    → PostgreSQL pages table  (sync state + chunk metadata + intelligence)
```

---

## APIs

### 1. Confluence REST API

**Auth:** HTTP Basic Auth — Atlassian account email + API token.
Generate token: https://id.atlassian.com/manage-profile/security/api-tokens

**Discover pages in a space:**
```
GET {CONFLUENCE_BASE_URL}/wiki/rest/api/search
    ?cql=space=CKB%20and%20type=page&limit=100&start=0
```
Returns: `page_id`, `title`, `_links.webui`, `version.number`

Pagination: increment `start` by 100 until `len(results) < limit`.

**Fetch full page content:**
```
GET {CONFLUENCE_BASE_URL}/wiki/rest/api/content/{page_id}
    ?expand=body.storage,version,metadata.labels
```
Key fields:
- `body.storage.value` — XHTML Confluence storage format
- `version.number` — integer version for sync tracking
- `metadata.labels.results[].name` — Confluence labels

---

### 2. Lyzr Agent API (Page Intelligence)

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
  "message": "<plain text of the page + instructions — see prompt template in lyzr_client.py>"
}
```

**Expected response** (the agent's `response` field):
```json
{
  "summary": "This page covers medical FSA eligibility and covered expense types for VIA Benefits employees.",
  "tags": ["eligibility", "healthcare", "fsa", "dental", "vision"],
  "relevancy_score": 0.97
}
```

**Agent system prompt to configure in Lyzr Studio:**
```
You are a knowledge base intelligence assistant for a benefits HR platform.
Always respond with valid JSON only.
Never include markdown fences, preamble, or explanation.
Your entire response must be parseable by json.loads().
```

Called **once per page**. The `tags` from this response are also injected into every chunk's `extra_info` so they are available for metadata filtering at retrieval time.

---

### 3. Lyzr Parse API (Text → Chunks)

**Endpoint:**
```
POST {LYZR_KB_BASE_URL}/v3/parse/text/
```

**Headers:**
```
Content-Type: application/json
x-api-key: {LYZR_API_KEY}
```

**Request body:**
```json
{
  "data": [
    {
      "text": "<full plain-text content of the page>",
      "source": "<page_id>",
      "extra_info": {}
    }
  ],
  "chunk_size": 1000,
  "chunk_overlap": 100
}
```

**Response:** Array of chunk objects — same structure as input `data`, but each item is one chunk. All chunks share the same `source` value as the input (the page_id we passed in).

```json
[
  {"text": "chunk 1 text...", "source": "<page_id>", "extra_info": {}},
  {"text": "chunk 2 text...", "source": "<page_id>", "extra_info": {}},
  ...
]
```

**After receiving the response, the pipeline:**
1. Replaces each chunk's `source` with a fresh UUID (this becomes the `chunk_id`)
2. Fills each chunk's `extra_info` with page metadata:

```json
{
  "page_id": "149881427",
  "page_title": "Medical Health Care Expenses",
  "space_key": "CKB",
  "page_url": "https://...",
  "labels": ["healthcare", "benefits"],
  "tags": ["eligibility", "healthcare", "fsa", "dental"],
  "chunk_index": 0
}
```

`tags` comes from the Lyzr Agent call (step 4 in the data flow). The same tags are applied to every chunk of the page.

---

### 4. Lyzr KB Train API (Chunks → Vector DB)

**Endpoint:**
```
POST {LYZR_KB_BASE_URL}/v3/rag/train/{LYZR_KB_ID}/
```

**Headers:**
```
Content-Type: application/json
x-api-key: {LYZR_API_KEY}
```

**Request body:** The prepared chunks array (with unique sources + extra_info filled):
```json
[
  {
    "text": "chunk 1 text...",
    "source": "550e8400-e29b-41d4-a716-446655440000",
    "extra_info": {
      "page_id": "149881427",
      "page_title": "Medical Health Care Expenses",
      "space_key": "CKB",
      "page_url": "https://...",
      "labels": ["healthcare", "benefits"],
      "chunk_index": 0
    }
  },
  ...
]
```

Lyzr handles: embedding generation + vector storage. The `source` UUID becomes the retrievable ID in the vector DB. The `extra_info` is returned alongside chunks at retrieval time (used for citations).

---

## Database

Single PostgreSQL table.

```sql
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
```

`metadata` JSONB stored per page after a successful sync:
```json
{
  "labels": ["healthcare", "benefits"],
  "summary": "This page covers medical FSA eligibility and covered expense types...",
  "tags": ["eligibility", "healthcare", "fsa", "dental"],
  "relevancy_score": 0.97,
  "chunk_count": 7,
  "chunks": [
    {
      "chunk_id": "550e8400-e29b-41d4-a716-446655440000",
      "chunk_index": 0,
      "chunk_text_preview": "Title: Medical Health Care Expenses\n\n## Eligibility\n\nEmployees qualify after..."
    }
  ]
}
```

The full chunk text is not stored in SQL — it lives in the vector DB. The SQL row is for sync tracking and auditability only.

---

## Internal Plain-Text Format (parser output → KB input)

The XHTML parser converts Confluence storage format to this readable plain text before sending to the Parse API:

```
Title: Medical Health Care Expenses

## Eligibility

Employees qualify after 90 days.

## Covered Expenses

Expense | Covered
--- | ---
Dental | Yes
Vision | No

## Notes

- Claim forms must be submitted within 30 days.
```

Tables become markdown tables. Lists become markdown lists. Confluence macros (code blocks, info panels) are included as plain text.

---

## Data Flow — Step by Step

For each page in the Confluence space:

```
1. confluence_client.get_page_content(page_id)
      → xhtml, version, labels

2. sha256(xhtml) == pages.content_hash?
      → YES: skip (log + continue)
      → NO:  proceed

3. parser.parse_storage_format(xhtml) → internal doc JSON
   parser.to_plain_text(doc_json)    → plain_text string

4. lyzr_client.get_page_intelligence(plain_text)
      POST /v3/inference/chat/  (called ONCE per page)
      → {summary, tags, relevancy_score}
      Agent system prompt: "respond with valid JSON only"

5. kb_client.parse_text(plain_text, source=page_id)
      POST /v3/parse/text/
      → raw_chunks: [{text, source: page_id, extra_info: {}}]
        (all chunks share the same source — the page_id we sent)

6. for each raw_chunk:
      chunk_id = sha256(f"{page_id}:{chunk_text}")   ← deterministic, content-based
      raw_chunk["source"]    = chunk_id
      raw_chunk["extra_info"] = {
          page_id, page_title, space_key, page_url,
          labels, tags (from step 4), chunk_index
      }

7. kb_client.train(prepared_chunks)
      POST /v3/rag/train/{kb_id}/
      → Lyzr embeds + stores everything; extra_info is returned at retrieval time

8. db.upsert_page(conn, page_record)
      page_record.metadata = {
          labels, summary, tags, relevancy_score,
          chunk_count,
          chunks: [{chunk_id, chunk_index, chunk_text_preview}]
      }
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `CONFLUENCE_BASE_URL` | e.g. `https://viabenefits.atlassian.net` |
| `CONFLUENCE_EMAIL` | Your Atlassian account email |
| `CONFLUENCE_API_TOKEN` | From https://id.atlassian.com/manage-profile/security/api-tokens |
| `LYZR_API_KEY` | Lyzr Studio API key (used for Agent, Parse, and Train APIs) |
| `LYZR_AGENT_ID` | Agent ID from Lyzr Studio (page intelligence agent) |
| `LYZR_USER_ID` | Lyzr user ID |
| `LYZR_KB_BASE_URL` | e.g. `https://agent-prod.studio.lyzr.ai` |
| `LYZR_KB_ID` | Knowledge base ID in Lyzr Studio |
| `DATABASE_URL` | e.g. `postgresql://postgres:postgres@localhost:5432/kb_ingestion` |
| `CHUNK_SIZE` | Token/char chunk size for Parse API (default: 1000) |
| `CHUNK_OVERLAP` | Overlap between chunks (default: 100) |
| `LOG_LEVEL` | `INFO` (default) or `DEBUG` |

---

## Running the Pipeline

```bash
# One-time: start Postgres and create table
docker run --name kb-postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=kb_ingestion -p 5432:5432 -d postgres:16

psql postgresql://postgres:postgres@localhost:5432/kb_ingestion -f sql/schema.sql

# Install deps
pip install -r requirements.txt

# Configure
cp .env.example .env   # fill in your values

# Dry run (no KB or DB writes)
python run_ingestion.py --space CKB --dry-run --verbose

# Full run
python run_ingestion.py --space CKB
```

---

## File Structure

```
pipeline-1/
├── confluence_ingestion/
│   ├── __init__.py
│   ├── confluence_client.py    # Confluence REST API (pagination, retry)
│   ├── parser.py               # XHTML → internal JSON + to_plain_text()
│   ├── lyzr_client.py          # Lyzr Agent API (summary, tags, relevancy_score)
│   ├── kb_client.py            # Lyzr Parse API + Train API
│   ├── db.py                   # PostgreSQL upsert_page
│   └── pipeline.py             # Orchestration loop
├── sql/
│   └── schema.sql
├── .env.example
├── requirements.txt
└── run_ingestion.py
```

---

## Incremental Sync

Re-running is safe. For each page:
1. `sha256(xhtml)` is compared against `pages.content_hash`
2. If hash matches → skip (no API calls made)
3. If hash differs → full parse → parse API → train API → DB upsert

Chunk IDs are **content-based**: `sha256(page_id + ":" + chunk_text)`. This means:
- If a chunk's text is unchanged across versions → same ID → Lyzr KB upserts by `source`, overwriting the old vector cleanly with no orphan.
- If a chunk's text changes → new ID → new vector created. The old vector at the previous ID is no longer referenced but is not actively deleted (Lyzr KB cleanup is a future concern).
- Inserting a new section in the middle of a page does not shift other chunks' IDs (unlike index-based hashing), because each ID is derived from the actual content.
