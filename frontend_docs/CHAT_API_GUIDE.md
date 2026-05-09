# Chat API — Frontend Integration Guide

> **Owner:** Backend → Frontend.
> **Last updated:** 2026-05-09 — v1.1.
> **Scope:** `POST /chat` (single-shot JSON) and `POST /chat/stream` (Server-Sent Events).
> **Read this first if:** the FE is showing a half answer, the spinner never resolves, the bubble buffers and dumps everything at the end, or the title/sidebar isn't updating.
>
> **v1.1 changes:** §6 adds `meta.request_id` (now stamped on every response/`done`); §4.5 token-frame ordering & post-processing contract added; §7.6 markdown-table cutoff diagnosis added.

The non-streaming endpoint is the canonical contract. The streaming endpoint is **additive** — same inputs, same final payload, just delivered token-by-token. Anywhere the two diverge, the streaming endpoint is the bug.

---

## 1. The two endpoints at a glance

| | `POST /chat` | `POST /chat/stream` |
|---|---|---|
| Response | `application/json` (`ChatResponse`) | `text/event-stream` (SSE frames) |
| Latency | One blocking call; returns when the model is fully done | First byte in <1 s typically; tokens flow as the model generates |
| Use when | Bots, eval scripts, anything that doesn't render incrementally | The chat UI |
| Final payload | The `ChatResponse` body | The `done` SSE frame's payload |

**Both** require a Supabase access-token bearer. **Both** accept the exact same `ChatRequest` body. **Both** persist the conversation to `chat_sessions` + `chat_messages` (unless `temp: true`).

---

## 2. Request body — `ChatRequest`

```json
{
  "message": "What was the kick-off depth on Sajaa-9?",
  "session_id": "9f0a…uuid",      // optional; backend generates one if omitted
  "project_id": "…uuid",          // optional scope
  "document_id": "…uuid",         // optional scope
  "temp": false                    // optional; true = incognito, no persistence
}
```

Rules:

- `message` is required and non-empty.
- `session_id` — pass it back on every turn after the first `done` frame. If you omit it, the backend mints a new UUID and every turn becomes a brand-new conversation.
- `project_id` / `document_id` are validated against the caller's ownership. 404 if either does not belong to the user.
- `temp: true` skips chat-history persistence and uses a 1-hour Redis TTL — used for the incognito toggle. Do not flip this on by default.

---

## 3. `POST /chat` — non-streaming response

The full schema is `ChatResponse` in [openapi.json](openapi.json) (search "ChatResponse"). Field-by-field:

| Field | Type | Always present? | Notes |
|---|---|---|---|
| `session_id` | string (uuid) | yes | Echoed back from request, or freshly minted. Persist this on the FE. |
| `status` | enum: `answered \| partial \| no_evidence \| clarification_requested` | yes | Drives the bubble's visual state. |
| `answer` | string | yes when `status ∈ {answered, clarification_requested, no_evidence}` | This is the **canonical** answer string. Render this. |
| `claims` | `ClaimItem[]` | optional | Reserved for the explicit-claim path; usually empty. |
| `unanswered` | `UnansweredItem[]` | optional | Facets the model couldn't ground; usually empty. |
| `followup` | string \| null | optional | **Deprecated** — read `followups` instead. Kept for back-compat. |
| `followups` | string[] | yes (possibly empty) | Suggested next questions; render as chips. |
| `sources` | `SourceInfo[]` | yes (possibly empty) | The citation list. Each item has a stable `source_id` like `S1`, `S2`. |
| `evidence` | `EvidenceItem[]` | yes (possibly empty) | Tables, page-image crops, full page images. Render as cards next to the answer. |
| `attached_pages` | object[] | yes (possibly empty) | Page PNGs the agent attached as visual context. |
| `tool_calls_made` | int | yes | Number of agent tool invocations; useful telemetry. |
| `plan`, `assessment` | object \| null | optional | Reserved diagnostics. |
| `meta` | object | yes | Path tag, token counts, verifier report, **`empty_response`** flag. See §6. |

Empty-corpus soft-land — when the user has zero documents under the relevant scope, the route short-circuits before the agent runs and returns:

```json
{
  "status": "no_evidence",
  "answer": "I don't have any documents indexed for you yet. Upload a PDF and I'll be able to answer questions grounded in it.",
  "meta": { "path": "empty_corpus_softland", "rag_invoked": false }
}
```

This is the only HTTP-200 path with a fixed answer string. Render it like any other bubble.

Slash commands (`/help`, `/forget`, `/restart`, `/sources`, `/exit`) short-circuit even earlier and return a `ChatResponse` with the slash reply in `answer`. No sources, no evidence.

---

## 4. `POST /chat/stream` — SSE wire format

### 4.1 Frame envelope

Every SSE frame on the wire looks like this — **plain `data:` only, no `event:` line, no `id:`**:

```
data: {"event":"token","text":"Hello"}\n\n
```

Backend uses `\n\n` to terminate each frame. There is **one JSON object per frame** and it always includes a top-level `event` discriminator. EventSource clients and fetch-based readers both work; we recommend fetch + manual parsing because EventSource cannot send POST bodies or auth headers easily.

### 4.2 Frame catalogue

| `event` | When | Payload shape | FE action |
|---|---|---|---|
| `status` | Phase boundary inside the agent | `{event:"status", phase:"<name>", ...}` | Update the spinner caption. See §5. |
| `token` | Each model-emitted text chunk during synthesis | `{event:"token", text:"…"}` | **Append** `text` to the bubble. Tokens are **not** newline-delimited; concatenate verbatim. |
| `done` | End of stream — exactly once | Same shape as `ChatResponse` (see §4.4) | Finalize the bubble; render sources/evidence/followups. |

That's the complete vocabulary today. Forward-compat: if a future `event` value lands that the FE doesn't recognize, ignore the frame. Same goes for unknown `phase` values inside `status`.

### 4.3 Reading the stream — minimum-viable client

```ts
const r = await fetch('/chat/stream', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${accessToken}`,
    'Accept': 'text/event-stream',
  },
  body: JSON.stringify({ message, session_id, project_id, document_id, temp }),
});

if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

const reader = r.body.getReader();
const decoder = new TextDecoder('utf-8');
let buf = '';
let answerBuf = '';
let done: any = null;

while (true) {
  const { value, done: streamDone } = await reader.read();
  if (streamDone) break;
  buf += decoder.decode(value, { stream: true });

  // Frames are separated by a blank line (\n\n).
  let sep: number;
  while ((sep = buf.indexOf('\n\n')) !== -1) {
    const raw = buf.slice(0, sep);
    buf = buf.slice(sep + 2);
    if (!raw.startsWith('data:')) continue;
    const json = raw.slice(5).trim();
    if (!json) continue;
    const ev = JSON.parse(json);
    switch (ev.event) {
      case 'status':
        onPhase(ev.phase, ev);
        break;
      case 'token':
        answerBuf += ev.text;
        onToken(answerBuf);
        break;
      case 'done':
        done = ev;
        // Reconcile: trust done.answer over the token concat.
        onFinal({ ...ev, answer: ev.answer || answerBuf });
        break;
    }
  }
}
```

Three things this client gets right that ad-hoc clients tend to miss:

1. **It re-buffers across `read()` boundaries** (`buf` survives between iterations). TCP/HTTP-2 will split frames anywhere; never assume one `read()` = one frame.
2. **It calls `decoder.decode(value, { stream: true })`** so multi-byte UTF-8 characters that straddle a chunk boundary aren't mojibaked. Skip the `{ stream: true }` and you'll see "half answers" that are actually corrupted bytes.
3. **It treats `done.answer` as the source of truth for the final string** and falls back to the token concat only if `done.answer` is empty/missing. See §7.

### 4.4 The `done` frame

Same fields as `ChatResponse` (§3) plus `event: "done"` and `session_id`. Always echoed; **always emitted exactly once** (even on error — see §8).

### 4.5 Token ordering, interleaving, and post-processing

Three guarantees worth pinning down:

1. **`token` frames arrive strictly in order.** A single `async for` over Gemini's final-answer stream produces them; backend does no re-ordering, batching, or buffering. Concatenating `text` from each `token` in arrival order is correct.
2. **No interleaving.** Tool-call narration, plan steps, and verifier output are **never** emitted as `token` frames. Tool-call boundaries surface as `status` frames only (`rag_search`, `fetch_document_outline`, `fetch_table`). Once `synthesizing` fires, every subsequent `token` is part of the user-visible answer.
3. **`done.answer` may legitimately differ from the token concat — and it's still canonical.** The two known divergence cases:
   - **Verifier rewrite.** Between the last `token` and `done`, the verifier strips `[Sn]` markers for any source it judged unsupported (or any source the model hallucinated that wasn't in the returned list). So `done.answer` can be **shorter** than the concat by exactly the dropped `[Sn]` substrings. `meta.verification.unsupported_ids` lists what was dropped.
   - **Empty-answer fallback.** If the model returns nothing usable, the synthesizer substitutes a placeholder string and emits it as both the final `token` and `done.answer` — concat and answer match in this case.
   - **Final `.strip()`.** Whitespace trimmed off both ends of the concat.

   `done.answer` is **never longer** than the token concat from any other cause. If the FE sees `concat > done.answer` and the diff is anything other than `[Sn]` markers, that's a backend bug — please file with the `meta.request_id` (see §6).

   **Rule:** trust `done.answer`. If `concat < done.answer`, a token frame was dropped client-side (see §7).

Backend chunk granularity is **opaque** — a single `token.text` may be a partial word, a partial markdown table cell, even a single character. Never assume a `token` frame ends at a sentence, line, or markdown-row boundary. See §7.6 for what this means when the FE sees a markdown table cut off mid-row.

---

## 5. `status.phase` enum — what the spinner should say

| `phase` | Meaning | Suggested label |
|---|---|---|
| `guard` | The agent is deciding whether to invoke RAG, ask for clarification, or answer directly | "Understanding your question…" |
| `rag_search` | About to call the `rag_search` tool. May include `query: "<rewritten>"` for transparency. | "Searching the knowledge base…" + dim secondary line for the rewritten query |
| `fetch_document_outline` | About to call the `fetch_document_outline` tool | "Reading document outline…" |
| `fetch_table` | About to call the `fetch_table` tool | "Pulling a table from the document…" |
| `synthesizing` | Retrieval is done; final-answer streaming starts immediately after this | "Composing the answer…" |
| `verifying` | Final answer is generated; the source-verifier is rewriting/dropping bad citations | "Verifying citations…" |

Tokens may begin arriving any time after `synthesizing`. The spinner should hide the moment the first `token` lands.

Backend will not introduce new phases without updating this table. The FE's defensive "render unknown phase as 'Working…'" fallback is correct and should stay.

---

## 6. `meta` field reference

`meta` ships on every non-streaming response and inside every streaming `done` frame.

| Key | Type | When | Meaning |
|---|---|---|---|
| `path` | string | always | Which agent path served the request: `prefilter`, `guard_reply`, `guard_clarify`, `rag`, `empty_corpus_softland`, `error`. |
| `rag_invoked` | bool | always | Whether the RAG tool path actually ran. |
| `guard_input_tokens` / `guard_output_tokens` | int | guard paths | Token cost of the guard call. |
| `rag_calls` | int | rag path | How many tool calls the agent made. |
| `include_images` | bool | rag path | Whether image hits were attached. |
| `total_hits` | int | rag path | Total retrieval hits across collections. |
| `verification` | object | rag answered path | Verifier report. Fields: `cited_ids`, `unknown_ids`, `unsupported_ids` (citations the verifier stripped from `done.answer`), `checked` (count actually verified), `skipped` (true if the answer had no citations to check), `error` (set on verifier failure; `null` on success). |
| `empty_response` | bool | always (streaming `done`) | **`true` means the backend genuinely produced an empty answer string** — not a proxy drop. Render the placeholder authoritatively. |
| `error` | string | error path | `agent_failed` if the agent crashed mid-stream. |
| `request_id` | string (uuid) | always | **NEW in v1.1.** Per-turn correlation id minted at the route. The same id appears in every backend log line for this turn. **Include this in any bug report** — it lets us pull the full server-side trace with a single grep. |

Use `meta.path` for telemetry — every chat turn lands in exactly one of those buckets.

---

## 7. "Only half the answer" — diagnosis

If the bubble shows partial text, work the list **in this order**:

1. **Compare `done.answer` with what you concatenated from `token` frames.** If `done.answer` is the full text and your concat is short, **a `token` frame was dropped client-side.** Almost always one of: missing UTF-8 stream-mode decoder, frame splitting on `\n` instead of `\n\n`, or losing `buf` between `read()` calls. Fix by always trusting `done.answer` when it's longer than the concat.
2. **Inspect `meta.empty_response`.** If `true`, the backend genuinely got nothing useful from the model. Render the bubble's empty-state UI (e.g. "The model returned no content; please retry") instead of a partial string.
3. **Check `meta.path`.** If it's `error` and `meta.error == "agent_failed"`, the agent crashed mid-stream and the answer is intentionally empty. Show a retry affordance.
4. **`curl -i -N` the backend directly.** If the curl output shows the full stream and the browser doesn't, a proxy is in the way. The backend sets `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive` and emits **no** `Content-Encoding: gzip`. Anything that compresses, buffers, or rewrites the SSE body is the proxy, not us. (See §8.)
5. **Confirm you're calling `/chat/stream`, not `/chat`.** `/chat` is non-streaming and returns one big JSON; if the FE tries to read it as SSE you'll see exactly one "frame" arrive at the end, which looks identical to "buffering."

### 7.6 Markdown table cut off mid-row (e.g. stops at `| :`)

Backend emits **one `token` frame per Gemini chunk**, and Gemini chunks are not aligned to markdown structure — a chunk can split inside a word, inside a `|`, or right after a `\n`. There is no server-side aggregation. So:

- A clean cutoff at a table separator (`| :`, `| --`, etc.) is almost certainly **a single SSE frame dropped in transit**, not the model output.
- Cross-check with `done.answer` — if the table is complete in `done.answer` but truncated in the rendered bubble, the proxy/SW dropped a frame. Render from `done.answer`.
- If `done.answer` is **also** truncated at the same spot, the model itself stopped. Check `meta.empty_response` (false = legit short answer) and `meta.verification.error` (rare but possible if a verifier failure cascaded).

The verifier itself never edits the body of the answer — it only strips `[Sn]` citation markers. It cannot truncate a table mid-row.

---

## 8. "Buffering / spinner-of-death" — diagnosis

When tokens don't appear until the request completes, the network path is the suspect, not the backend. Backend already does the right thing:

- Yields each token as Gemini emits it (`generate_content_stream` async loop, no batching).
- Uses `media_type="text/event-stream"`.
- Sends `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`.
- Registers **no `GZipMiddleware`** — the SSE body is never compressed by the app.

Common culprits, in order of likelihood:

1. **A reverse proxy (nginx, Cloudflare, an ingress controller) buffering responses.** The `X-Accel-Buffering: no` header handles nginx specifically; for others, disable response buffering for `/chat/stream` explicitly.
2. **A service worker or browser fetch interceptor that reads the whole body before forwarding it** to your code. SW caching of POST is unusual but possible — exclude `/chat/stream` from any SW caching strategy.
3. **`fetch` polyfill on legacy browsers that doesn't expose `body` as a readable stream.** Modern Chromium/Firefox/Safari are fine; check the polyfill.
4. **`Content-Encoding: gzip` injected by a proxy.** Browsers usually decompress streams transparently, but if the proxy buffers internally before flushing, the FE sees one giant chunk. Strip gzip on this route.
5. **Reading `await r.text()` instead of streaming the body.** That's a one-line bug — guard it in code review.

Quick local sanity check (PowerShell):

```powershell
curl.exe -i -N -X POST http://localhost:8080/chat/stream `
  -H "Authorization: Bearer $env:ACCESS_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"message":"hello","session_id":null}'
```

You should see frames arriving **as the model generates**. If they all land at once, the problem is upstream of the browser; otherwise it's in the browser code.

---

## 9. Persistence behavior — what hits the DB

Both endpoints persist to `chat_sessions` + `chat_messages` **only when**:

- `temp: false` in the request, **and**
- the final `status` is `answered` or `clarification_requested`.

`no_evidence` and `partial` are not persisted (they're considered transient). The empty-corpus soft-land is therefore not persisted either.

On the first answered turn the backend auto-generates a session title from the user message + assistant message via a cheap Flash call. Subsequent turns leave the title alone unless the user renames via `PATCH /chat/sessions/{id}`.

If the FE wants to re-render the sidebar after a turn completes, listen for the `done` frame and re-fetch `GET /chat/sessions` (or just optimistically prepend the session locally — the title race-condition is a few hundred ms).

---

## 10. Error handling

| Scenario | HTTP status / SSE response | FE action |
|---|---|---|
| Missing/invalid bearer | 401 | Re-auth. |
| Scope not owned (`project_id` / `document_id` foreign) | 404 with `detail` | Surface the message; let user pick a different scope. |
| Empty `message` | 422 (validation) | Inline form error. |
| Agent crashed (`/chat`) | 500 with `detail: "Agent failed to generate a response"` | Toast + retry button. |
| Agent crashed (`/chat/stream`) | HTTP 200, but a `done` frame with `status: "no_evidence"`, `answer: ""`, `meta: {"path":"error","error":"agent_failed"}` | Render the empty-state bubble + retry affordance. **Do not** treat the 200 as success. |
| Client disconnect mid-stream | Logged server-side as `chat_stream_client_disconnect`; no further side effects | None — just don't reconnect with the same `session_id` mid-turn. |

Streaming intentionally never returns a non-200 *after* the headers go out — once the SSE body has started, the only way to signal failure is a `done` frame with the error-shaped payload above. The FE's correct check is therefore "did `done` arrive AND is `status` not `no_evidence`-with-`meta.error`?"

---

## 11. Versioning & forward-compat

Pinky-promises from the backend:

- `done` will always be the last frame.
- `done.answer` will always be a string (possibly empty when `meta.empty_response: true` or `meta.error` is set).
- `session_id` will always be in the `done` payload.
- `meta.request_id` will always be present on every `ChatResponse` and every `done` frame (added in v1.1).
- `token` frames arrive strictly in order; backend never re-orders or interleaves with non-answer text.
- New fields may appear in `meta` without notice — the FE should **not** validate `meta` against an exhaustive schema. Treat unknown keys as inert.
- New `event` values may appear; ignore unknown ones.
- New `status.phase` values will land **with a doc bump in this file** in the same PR.

Anything not on this list (renaming `event`/`status`/`answer`, removing `session_id`, breaking `done.answer`'s string-ness) requires a major version bump and a heads-up in `BACKEND_REPORT.md`.

---

## 12. Quick reference — minimal happy-path snippets

**Non-streaming:**

```ts
const r = await fetch('/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${tok}` },
  body: JSON.stringify({ message, session_id }),
});
const data = await r.json();
renderBubble({ text: data.answer, sources: data.sources, evidence: data.evidence });
setSessionId(data.session_id);
```

**Streaming (high-level):**

```ts
await streamChat({
  body: { message, session_id },
  onPhase: (phase) => setSpinnerLabel(PHASE_LABELS[phase] ?? 'Working…'),
  onToken: (full) => setBubbleText(full),
  onFinal: (done) => {
    setBubbleText(done.answer);                 // canonical
    setSources(done.sources);
    setEvidence(done.attached_pages);
    setFollowups(done.followups);
    setSessionId(done.session_id);
    if (done.meta?.empty_response) markEmpty();
    if (done.meta?.error) markFailed(done.meta.error);
  },
});
```

That's the entire integration surface. If anything in `BACKEND_REPORT.md` contradicts this guide, this guide wins for `/chat` and `/chat/stream` specifically.

---

*End of Chat API Guide v1.1 — 2026-05-09.*
