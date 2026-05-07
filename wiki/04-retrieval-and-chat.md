# 04 — Retrieval & Chat

> _Owns: how `/chat` and `/search` turn a query into a grounded
> answer, including reranking, tenant filtering, and image evidence._

## Live request path

```
POST /chat {message, project_id?, document_id?}
  └─ middleware → AuthUser
  └─ rag_agent.run(message, AuthUser, project_id?, document_id?)
        ├─ planner (Gemini Flash, decides tool calls)
        ├─ rag_tools.search_chunks(...)   ─┐
        ├─ rag_tools.search_entities(...) ─┼─ all calls carry user_id filter
        ├─ rag_tools.search_images(...)   ─┘
        ├─ retrieval/reranker.py          (cross-encoder rerank)
        └─ answerer (Gemini Pro, composes answer + cites sources)
  └─ {answer, sources[], evidence[], attached_pages[]}
```

## RAG agent

[app/agents/rag_agent.py](../app/agents/rag_agent.py),
[app/agents/rag_tools.py](../app/agents/rag_tools.py),
[app/agents/prompts.py](../app/agents/prompts.py)

- The agent is a planner/executor loop, not LangGraph in production
  (the file [app/agents/graph.py](../app/agents/graph.py) is an
  experimental sketch — do not extend it).
- The planner decides which tools to call. Tools are deliberately few:
  - `search_chunks` (text)
  - `search_entities` (formations / units / wells / equipment)
  - `search_images` (per-page renderings)
- Each tool **must** accept `user_id` and pass it as a Qdrant filter.
  Cross-user leakage is a security bug, not a polish item.

## Reranker

[app/retrieval/reranker.py](../app/retrieval/reranker.py)

- Takes the union of tool results, dedupes, and reranks via a cross
  encoder (`bge-reranker-v2`).
- Top-K chunks (default 6) plus image evidence are passed to the
  answerer prompt.

## Image evidence and `attached_pages`

The answerer is multimodal — when an image point is reranked into the
top set, the agent attaches the page PNG as inline image input to the
Pro model. The response surfaces these in `attached_pages[]` with
`{document_id, page_num, url}` so FE can render thumbnails.

URLs are `/documents/{id}/pages/{n}/image` (302 → signed Supabase URL),
not raw bucket paths.

## Tenant filtering (mandatory)

Every Qdrant query carries:

```python
filter = Filter(
    must=[
        FieldCondition(key="user_id", match=MatchValue(value=auth_user.id)),
        # plus optional project_id / document_id
    ]
)
```

This is enforced at the tool layer, not the agent layer, so an agent
prompt-injection attack cannot bypass it.

For super_admin support reads, we still **do not** disable the filter
silently. Either super_admin uses an explicit `?scope=all` parameter on
list endpoints (currently project list and admin/users list), or the
read goes through Postgres directly. Vector search is always scoped to
the calling user.

## Soft-land on empty corpus

When tool calls return zero hits (new account, fresh project,
mismatched scope), the agent does not hallucinate. It returns a
deterministic answer:

> "I don't have any documents indexed yet under your account. Once
> you ingest a PDF I can answer questions grounded in it."

This is what allows our smoke test to call `/chat` against an empty
super_admin corpus and still get a 200.

## Search endpoint

`GET /search?q=...&project_id=...&document_id=...&limit=10`

- Direct semantic search bypassing the agent. Returns top chunks with
  `{score, text, document_id, page_num, source_path}`.
- Same `user_id` filter applies.
- `q` is required. Bogus role values etc. → 422.

## Chat request body

`POST /chat`:

```json
{
  "message": "What is the spud date of well A?",
  "project_id": "optional uuid scope",
  "document_id": "optional uuid scope"
}
```

Note: the field is `message`, not `query`. Earlier doc revisions used
`query`; the live API has been `message` since PR-7.

## Chat response shape

```json
{
  "answer": "...",
  "sources": [
    {"document_id": "...", "page_num": 4, "score": 0.83, "snippet": "..."}
  ],
  "evidence": [
    {"document_id": "...", "page_num": 4, "kind": "table|chunk|entity"}
  ],
  "attached_pages": [
    {"document_id": "...", "page_num": 4, "url": "/documents/.../pages/4/image"}
  ]
}
```

Every `sources[i]` and `evidence[i]` carries `document_id`. FE uses
this for citation links and image rendering. Do not use `job_id` for
links — it is per-ingestion-run only.

## Things this page does not own

- The model identifiers and pricing → [app/config.py](../app/config.py) +
  [08-deployment-and-ops.md](08-deployment-and-ops.md).
- Where the chunks came from → [03-ingestion-pipeline.md](03-ingestion-pipeline.md).
- Qdrant collection schema → [05-storage-and-vectors.md](05-storage-and-vectors.md).
