---
description: "Run a focused redundancy/dead-code audit on a specific area of the Wellsynthai pipeline using the RAG Pipeline Auditor agent."
argument-hint: "<area> — e.g. 'ingest route', 'page_streamer', 'extractor+validator+escalator', 'rag_agent + rag_tools', 'vectorstore', 'all'"
---
You are running a targeted audit on the Wellsynthai codebase. The area to audit is: **${input:area}**.

Delegate to the **RAG Pipeline Auditor** subagent. Do not perform the audit yourself in the main thread.

The subagent must follow its own SKILL contract and return findings in this exact order:

1. **Findings Table** — `File | Symbol / Lines | Bucket (DEAD / DUPLICATE / ILLOGICAL / OUT-OF-SCOPE / KEEP) | Evidence | Recommendation`
2. **Deletion / Merge Plan** — ordered checklist, each item independently safe to apply
3. **Risks / Asks** — only items where the user must choose between two viable canonicals
4. **Applied Changes** — only for unambiguous DEAD / OUT-OF-SCOPE; everything else waits for confirmation

Constraints to remind the subagent of:

- North Star is "answer from ingested document content" — nothing else.
- No new code, no new deps, no renames, no docstring/comment additions.
- Do not touch `tests/` unless a deletion forces it.
- Trace the live request path before deleting anything: `POST /ingest → orchestrator → page workers → postprocessor → indexers` and `POST /chat → rag_agent → rag_tools → answer`.

After the subagent reports back, summarize in 3–5 bullets:
- How many items in each bucket
- Which deletions were applied automatically
- Which decisions are blocked on the user

Do not expand scope. Do not start a second audit area in the same run.
