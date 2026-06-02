# PROJECT_ARCHITECTURE_CONTEXT.md

## Project

Claims Support Advisor (CSA)

Enterprise RAG Knowledge Base built on top of Confluence Cloud.

Primary goal:

* Ingest Confluence knowledge articles
* Build a searchable RAG knowledge base
* Support citations
* Support metadata filtering
* Support incremental synchronization
* Maintain auditability and traceability

---

# Current Scope

This is a V1 implementation.

Priorities:

1. Reliable ingestion
2. Good retrieval quality
3. Source citations
4. Incremental updates
5. Simplicity

Avoid overengineering.

---

# Source System

Source:

Confluence Cloud

Example URL:

```text
https://viabenefits.atlassian.net/wiki/spaces/CKB/pages/149881427/Medical+Health+Care+Expenses
```

---

# Confluence Ingestion Strategy

## Page Discovery

Use CQL search:

```http
GET /wiki/rest/api/search?cql=space=CKB and type=page
```

Purpose:

* Discover pages
* Retrieve page IDs
* Retrieve page metadata

---

## Page Retrieval

For each page:

```http
GET /wiki/rest/api/content/{pageId}?expand=body.storage,version,metadata.labels
```

Important fields:

```text
body.storage.value
version
metadata.labels
```

---

# Confluence Content Format

body.storage.value is Confluence Storage Format.

It is XHTML/XML-like content.

Contains:

* headings
* paragraphs
* lists
* tables
* macros
* images
* attachments

Example:

```xml
<h1>Eligibility</h1>
<p>Employees qualify after 90 days.</p>
```

---

# Current Parsing Strategy

Use:

```python
BeautifulSoup(content, "xml")
```

or

```python
BeautifulSoup(content, "lxml")
```

---

# Current V1 Content Flow

Current approved architecture:

```text
Confluence XHTML
    ↓
BeautifulSoup Parser
    ↓
Plain Text
    ↓
Lyzr Agent
        - Summary
        - Tags
        - Relevancy Score
    ↓
Lyzr Parse API
        - Chunking
    ↓
Lyzr Train API
        - Embeddings
        - Vector Storage
```

---

# Important Clarification

The Lyzr Agent is NOT responsible for chunking.

The Agent is currently used only for:

```text
Summary Generation
Tag Generation
Relevancy Scoring
```

The Parse API performs chunking.

The Train API performs embedding generation and vector storage.

---

# Current Database Design

Keep schema intentionally simple.

Only one SQL table is currently planned.

---

## pages

Suggested structure:

```sql
CREATE TABLE pages (
    page_id VARCHAR(100) PRIMARY KEY,

    space_key VARCHAR(50),

    title TEXT,

    page_url TEXT,

    content_hash TEXT,

    confluence_version INT,

    summary TEXT,

    relevancy_score FLOAT,

    metadata JSONB,

    created_at TIMESTAMP,

    updated_at TIMESTAMP,

    last_synced_at TIMESTAMP
);
```

---

# Metadata Structure

Suggested metadata JSON:

```json
{
  "labels": [
    "claims",
    "medical"
  ],

  "generated_tags": [
    "denied_claim",
    "eligibility"
  ],

  "chunks": [
    {
      "chunk_id": "...",
      "chunk_index": 1,
      "vector_db_id": "..."
    }
  ]
}
```

---

# Tags Strategy

Preferred order:

1. Confluence Labels
2. Agent Generated Tags

Purpose:

* Retrieval filtering
* Search optimization
* Future analytics

No separate tags table required.

---

# Citation Requirements

Every retrieved chunk must support citations.

Store enough metadata to support:

```text
Medical Health Care Expenses
→ Eligibility
```

and provide a Confluence URL.

Minimum citation metadata:

```json
{
  "page_id": "...",
  "page_title": "...",
  "url": "...",
  "section_heading": "..."
}
```

---

# Vector Store Metadata

Store retrieval metadata alongside vectors.

Example:

```json
{
  "page_id": "...",
  "page_title": "...",
  "space_key": "CKB",
  "section_heading": "...",
  "tags": [
    "claims",
    "medical"
  ]
}
```

Metadata filtering should happen in the vector database whenever possible.

Example:

```text
Search
WHERE tags contains "claims"
```

---

# Synchronization Strategy

Near-real-time is sufficient.

Possible approaches:

```text
Webhook
```

or

```text
Scheduled Polling
```

Both are acceptable.

---

# Change Detection

Store:

```text
content_hash
```

for every page.

Workflow:

```text
Page Update
    ↓
Fetch Latest Content
    ↓
Generate New Hash
    ↓
Compare Hash
```

If unchanged:

```text
Skip Processing
```

If changed:

```text
Reprocess Page
```

---

# ✅ Deterministic Chunk IDs — IMPLEMENTED

## Approach Used

Content-based sha256:

```python
chunk_id = sha256(f"{page_id}:{chunk_text}")
```

This was chosen over index-based hashing (`page_id:chunk_index`) because:

* Inserting a new section does not shift other chunks' IDs
* Same content across page versions produces the same ID
* Lyzr KB Train API upserts by `source` field — same ID = clean overwrite, no orphan vectors

## Behaviour on Re-sync

```text
Page Unchanged  → hash check skips → no chunk IDs generated at all
Page Changed    → full reprocess → content-identical chunks keep same ID
                                 → changed chunks get new ID (old vector becomes orphan)
```

Orphan vector cleanup (deleting old vectors whose chunk text changed) is a future enhancement.

---

# Current Chunking Strategy

Current implementation:

```text
Plain Text
    ↓
Parse API
    ↓
Size-Based Chunking
```

This is acceptable for V1.

No immediate change required.

---

# Known Limitation

Current chunking does NOT fully preserve document hierarchy.

Example:

```text
Eligibility

Employees qualify after 90 days.

Covered Expenses

Dental covered.
```

may become:

```text
Chunk

Employees qualify after 90 days.

Covered Expenses

Dental covered.
```

because chunking is based on size.

---

# Future Architecture

## Semantic Chunking

This is the preferred long-term direction.

Not required for V1.

---

# Future Goal

Instead of:

```text
XHTML
    ↓
Plain Text
    ↓
Size-Based Chunking
```

move toward:

```text
XHTML
    ↓
Structured Document
    ↓
Semantic Sections
    ↓
Semantic Chunk Builder
    ↓
Embeddings
```

---

# Future Structured Document Model

Example:

```json
{
  "page_id": "149881427",
  "title": "Medical Health Care Expenses",

  "sections": [
    {
      "heading": "Eligibility",

      "content": [
        {
          "type": "paragraph",
          "text": "Employees qualify after 90 days."
        }
      ]
    },

    {
      "heading": "Covered Expenses",

      "content": [
        {
          "type": "paragraph",
          "text": "Dental is covered."
        }
      ]
    }
  ]
}
```

---

# Future Semantic Chunk Builder

Goal:

Chunk by meaning.

Not by arbitrary character count.

---

# Example

Input:

```text
Eligibility

Employees qualify after 90 days.
```

Output:

```text
Title: Medical Health Care Expenses

Section: Eligibility

Employees qualify after 90 days.
```

---

Input:

```text
Covered Expenses

Dental is covered.
Vision is not covered.
```

Output:

```text
Title: Medical Health Care Expenses

Section: Covered Expenses

Dental is covered.
Vision is not covered.
```

---

# Semantic Chunking Rules

1. Chunk by section first.
2. Preserve heading context.
3. Preserve page title context.
4. Preserve subsection hierarchy.
5. Preserve table structure.
6. Do not combine unrelated sections.
7. Only split a section when it exceeds token limits.

---

# Table Handling

Bad:

```text
Dental Covered Vision Not Covered
```

Good:

```text
| Expense | Coverage |
|----------|----------|
| Dental | Covered |
| Vision | Not Covered |
```

Preserve table structure whenever possible.

---

# Hierarchy Preservation

Future chunks should contain:

```text
Page Title
Section Heading
Subsection Heading
Content
```

This improves:

* retrieval quality
* citations
* answer grounding
* explainability

---

# Design Principles

1. Preserve structure as long as possible.
2. Avoid flattening content prematurely.
3. Parse deterministically before using LLMs.
4. Use LLMs for enrichment, not primary parsing.
5. Preserve metadata for citations.
6. Keep schema simple.
7. Use content hashes for synchronization.
8. Use deterministic chunk IDs.
9. Store retrieval metadata with vectors.
10. Move toward semantic chunking in future versions.

---

# Current Implementation Status

Approved for V1:

✅ XHTML Parsing

✅ Plain Text Conversion

✅ Lyzr Agent Enrichment

✅ Parse API Chunking

✅ Train API Embeddings

✅ Single Pages Table

✅ Metadata-Based Filtering

✅ Citations

✅ Content Hash Synchronization

Implemented:

✅ Deterministic Chunk IDs (content-based sha256)

Future Enhancement:

🟡 Semantic Chunking
