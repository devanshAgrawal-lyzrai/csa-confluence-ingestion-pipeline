# PROJECT_CONTEXT.md

## Project Overview

We are building a RAG-based Knowledge Base chatbot that ingests content from Confluence Cloud and answers user questions using retrieval-augmented generation.

Primary source system:

* Confluence Cloud
* Multiple spaces
* Hundreds/thousands of pages possible

Goals:

1. Initial full ingestion of relevant Confluence spaces.
2. Incremental synchronization of changes.
3. Source citations in chatbot responses.
4. Metadata-based filtering during retrieval.
5. Auditability of ingestion operations.
6. Enterprise-grade maintainability without overengineering.

---

# Confluence Architecture

## Content Discovery

We will first discover pages at the space level.

Example flow:

Space
→ Page List
→ Page Content
→ Chunking
→ Embeddings

Typical API usage:

### Get pages in a space

Using CQL:

GET /wiki/rest/api/search?cql=space=CKB and type=page

Returns:

* page ids
* titles
* urls
* metadata

This is used for page discovery.

---

## Fetch page content

For each page:

GET /wiki/rest/api/content/{pageId}?expand=body.storage,version,metadata.labels

Important fields:

body.storage.value
version
metadata.labels

---

# Confluence Storage Format

body.storage.value is NOT plain text.

It is:

XHTML-like Confluence Storage Format

Contains:

Standard tags:

* h1-h6
* p
* ul
* ol
* table

Confluence tags:

* ac:structured-macro
* ac:image
* ri:attachment

Example:

<h1>Eligibility</h1>
<p>Employees qualify after 90 days.</p>

This content should be parsed programmatically.

---

# Parsing Strategy

Use BeautifulSoup or lxml.

Example:

```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(content, "xml")
```

or

```python
soup = BeautifulSoup(content, "lxml")
```

We should NOT send raw Confluence XML directly to an LLM for structuring.

Instead:

Confluence XML
→ Deterministic Parser
→ Structured Internal JSON

---

# Internal Document Model

Create a normalized document structure.

Example:

```json
{
  "page_id": "149881427",
  "title": "Medical Health Care Expenses",
  "space_key": "CKB",
  "sections": [
    {
      "heading": "Eligibility",
      "content": [
        {
          "type": "paragraph",
          "text": "Employees qualify after 90 days."
        }
      ]
    }
  ]
}
```

Purpose:

* Preserve hierarchy
* Preserve tables
* Preserve headings
* Preserve citations
* Enable intelligent chunking

---

# Chunking Strategy

Chunk AFTER parsing.

DO NOT chunk:

* raw XML
* flattened text

Preferred flow:

Confluence XML
→ Structured JSON
→ Semantic Chunking
→ Embeddings

Chunk boundaries should follow:

Page
→ Section
→ Subsection
→ Content blocks

Instead of arbitrary token splitting.

Example chunk:

Title: Medical Health Care Expenses

Section: Eligibility

Employees qualify after 90 days.

---

# Tables

Tables must be preserved structurally.

Bad:

Dental Yes Vision No

Good:

| Expense | Covered |
| ------- | ------- |
| Dental  | Yes     |

Tables may later be chunked:

* table-level
* row-level

depending on retrieval quality.

---

# Embedding Strategy

Do NOT embed JSON directly.

Generate chunk text from structured JSON.

Example:

Title: Medical Health Care Expenses

Section: Eligibility

Employees qualify after 90 days.

Embed:

* chunk text

Store separately:

* metadata

---

# Citations

Source citations are a core requirement.

Each chunk must retain metadata.

Example metadata:

```json
{
  "page_id": "149881427",
  "page_title": "Medical Health Care Expenses",
  "space_key": "CKB",
  "section_heading": "Eligibility",
  "url": "...",
  "chunk_id": "..."
}
```

Chatbot responses should cite:

* page title
* section heading
* Confluence URL

Goal:

Medical procedures are covered...

Source:
Medical Health Care Expenses → Covered Expenses

---

# Metadata Filtering

Metadata filtering will be supported.

Examples:

* healthcare
* claims
* denied_claim
* eligibility

Primary storage location:

Vector DB payload metadata

Example:

```json
{
  "page_id": "...",
  "space_key": "CKB",
  "tags": [
    "claims",
    "medical"
  ]
}
```

Filtering should happen inside the vector database whenever possible.

Example:

Retrieve top N chunks
WHERE tags contains "claims"

---

# Confluence Labels

Before implementing custom tagging, inspect Confluence labels.

Useful API:

GET /wiki/rest/api/content/{id}?expand=metadata.labels

or

GET /wiki/rest/api/content/{id}/label

If labels are:

* present
* meaningful
* consistently maintained

then they may be reused as retrieval tags.

Otherwise tags may be generated later.

---

# Synchronization Strategy

Real-time sync is not required.

Near-real-time is acceptable.

Preferred approach:

Confluence Change
→ Webhook or Scheduled Sync
→ Re-fetch Page
→ Re-chunk
→ Compare Hashes
→ Update Changed Chunks Only

---

# Hash-Based Change Detection

Store hashes for efficient updates.

Page hash:

* detect page-level changes

Chunk hash:

* detect chunk-level changes

Workflow:

New page version
→ Parse
→ Generate chunks
→ Compare chunk hashes
→ Update only changed chunks

---

# Database Design (Current Preferred Version)

Keep the schema intentionally simple.

## pages

```sql
CREATE TABLE pages (
    page_id VARCHAR(100) PRIMARY KEY,
    space_key VARCHAR(50),
    title TEXT,
    page_url TEXT,
    content_hash TEXT,
    confluence_version INT,
    last_synced_at TIMESTAMP,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

Purpose:

* page tracking
* synchronization
* ingestion status

---

## chunks

```sql
CREATE TABLE chunks (
    chunk_id UUID PRIMARY KEY,
    page_id VARCHAR(100),
    chunk_index INT,
    section_heading TEXT,
    chunk_text TEXT,
    chunk_hash TEXT,
    vector_db_id TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

Purpose:

* retrieval mapping
* citations
* chunk-level updates

---

# Why Chunk IDs Exist

Chunk IDs are required.

Reasons:

* vector DB updates
* vector DB deletions
* citations
* auditability
* retrieval tracking

Chunk is the fundamental retrieval unit.

---

# Why Pages Table Exists

Although technically possible to operate only from chunks, a lightweight pages table is recommended.

Reasons:

* page-level sync tracking
* page metadata
* content hash tracking
* Confluence version tracking

No additional ingestion/audit tables are currently planned.

---

# Vector DB Expectations

Vector DB stores:

* embedding vector
* retrieval metadata

Example:

```json
{
  "id": "chunk_123",
  "vector": [...],
  "payload": {
    "page_id": "...",
    "page_title": "...",
    "space_key": "...",
    "section_heading": "...",
    "tags": ["claims", "medical"]
    }
}
```

The vector database should be the primary mechanism for:

* semantic retrieval
* metadata filtering

---

# Design Principles

1. Preserve structure as long as possible.
2. Do not flatten content early.
3. Parse deterministically before using LLMs.
4. Chunk semantically, not by token count alone.
5. Preserve metadata for citations.
6. Keep ingestion schema simple.
7. Avoid overengineering until proven necessary.
8. Store retrieval metadata with vectors.
9. Support incremental updates via hashes.
10. Source traceability is mandatory.
