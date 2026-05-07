# 05 — Storage & Vectors

> _Owns: where bytes physically live (Supabase Storage) and how vectors
> are organised in Qdrant._

## Supabase Storage buckets

| Bucket | Holds | Path layout |
|---|---|---|
| `pdfs` | Original uploaded PDFs | `<user_id>/<project_id>/<document_id>/<filename>.pdf` |
| `pages` | Rendered page PNGs (200 dpi) | `<user_id>/<project_id>/<document_id>/page_NNN.png` |
| `artifacts` | Markdown, tables JSON/MD, entities JSON | `<user_id>/<project_id>/<document_id>/<filename>` |

Public access is **off** for all three. Image serving is via signed
URLs minted by `GET /documents/{id}/pages/{n}/image` (handler returns
a 302 redirect; the URL expires in minutes).

### Storage RLS

RLS is on, with owner-scoped read/write/delete policies that match
`path[1] = auth.uid()`. The backend, when it operates on behalf of a
user, uses the service-role key (which bypasses RLS) and enforces
ownership in application code by including `user_id` in the path
prefix on every write and verifying ownership on every read.

Super-admin scope-all reads do **not** rely on an RLS bypass clause —
they go through the service-role key with explicit application-level
auth checks.

## Qdrant collections

| Collection | Vector | One point per | Purpose |
|---|---|---|---|
| `wellsynthai_chunks` | text-embed (1536-d) | ~512-token chunk | Text retrieval |
| `wellsynthai_entities` | text-embed | resolved entity | Entity search (wells, formations, units) |
| `wellsynthai_images` | image-embed | rendered page | Image grounding for chat answerer |

### Required payload on every point

```json
{
  "user_id": "uuid",
  "role": "drilling_engineer | ...",
  "organization": "Acme Drilling",
  "project_id": "uuid",
  "document_id": "uuid",
  "page_num": 4,

  "text": "...",           // chunks/entities only
  "section": "...",        // chunks
  "table_id": "...",       // when chunk is from a table
  "entity_kind": "...",    // entities collection
  "image_path": "..."      // images collection (storage key)
}
```

`user_id`, `project_id`, `document_id` are indexed payload fields
(scalar payload index) so filters are O(log n).

### Mandatory filter

Every read goes through:

```python
must = [FieldCondition(key="user_id", match=MatchValue(value=user.id))]
```

Optional adds: `project_id`, `document_id`, `page_num`. **There is no
code path where a Qdrant query runs without `user_id`.** If you find
one, that's a security bug — fix and add a smoke assertion.

## Tenant payload backfill

When Qdrant was wiped and recreated for PR-7, the `user_id` payload
was added to every existing point. New ingestions write the full
payload at insert time.

## Why Qdrant, not pgvector

- We need image vectors and text vectors with different dimensionality.
- We need ~100 concurrent inserts during ingest, which Postgres can
  handle but Qdrant handles with much less tuning effort.
- Hybrid search is on the roadmap; Qdrant's filter + score combination
  is well-suited.

## Resetting Qdrant

[app/tools/qdrant_reset.py](../app/tools/qdrant_reset.py) — wipes all
three collections and recreates them with the correct payload indices.
Run **only** if you also re-ingest every document; otherwise users
will see "no documents found" for previously ingested PDFs.

## Things this page does not own

- The pipeline that produces these vectors → [03-ingestion-pipeline.md](03-ingestion-pipeline.md).
- How retrieval consumes them → [04-retrieval-and-chat.md](04-retrieval-and-chat.md).
- Postgres tables → [06-database-schema.md](06-database-schema.md).
