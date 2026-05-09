# Chat Stream — Progressive Finalisation Protocol

**Status:** Active. Replaces the previous single-`done` contract on `POST /chat/stream`.
**Audience:** Frontend engineer integrating the chat SSE stream.
**Scope:** This is a protocol-level change. The non-streaming `POST /chat` endpoint is unaffected.

---

## 1. Why this changed

Previously, `done` was the terminal SSE frame and carried the final answer text, sources, citations, verification report, and followup suggestions all in one payload. The backend blocked emitting `done` until the verifier and the followup generator had both finished — a flat 10–15 s tax (often 20 s on verifier timeout) after the last token streamed.

Now the answer is finalised the moment streaming tokens stop. Followups land asynchronously via a dedicated update frame. User-perceived latency drops by ~10–20 s on every successful turn; the answer is fully usable while followups are still generating.

The LLM-based citation verifier has been **removed entirely** — it was the single largest latency cost on the chat path and added negligible quality. Source attribution now relies on:
- The model citing source documents inline by filename (e.g. "…(Sajaa_6_EOWR.pdf, p.14)").
- A new `documents_used` array in the `done` frame: a deduped list of source documents straight from the qdrant payloads, with no LLM call.

---

## 2. New frame sequence (canonical)

For a normal answered turn the FE will see, in order:

```
status   phase=received       (instant — flushes headers + first byte)
status   phase=guard
status   phase=...             (planning / searching / synthesizing / etc.)
thought  text="..."           (zero or more — model's reasoning summary)
thought  text="..."
token    text="..."           (one or more — actual answer text)
token    text="..."
done                            (answer is locked; followups marked pending)
verification_update             (always {skipped: true} — verifier is retired; emitted immediately)
followups_update                (~3-6 s after done)
complete                        (terminal — close the EventSource here)
```

`done` is **no longer terminal**. `complete` is the only frame that signals end-of-stream.

`thought` frames arrive **before** the first `token` frame (they are the model's pre-answer reasoning). Once `token` frames start, no further `thought` frames will arrive for that turn.

`verification_update` is now informational only (always `{skipped: true}`); the FE can use it as an immediate signal to hide any "verifying" affordance. It is kept in the wire protocol so future re-enablement of automated grounding checks does not require another protocol bump.

---

## 3. Frame schemas (only what changed)

All frames continue to be `data: <json>\n\n` lines on a `text/event-stream` response. Every frame still carries `server_ts` (epoch ms, set by `sse_pack`). All non-status frames carry `request_id`.

### 3.1 `done` (shape changed)

```jsonc
{
  "event": "done",
  "session_id": "uuid",
  "request_id": "uuid",
  "status": "answered" | "no_evidence" | "clarification_requested",
  "answer": "Full answer text — same content already streamed via tokens.",
  "sources": [ /* same shape as before — chunk-level hits */ ],
  "documents_used": [                          // ← NEW: deduped source documents
    {
      "document_id": "uuid",
      "filename": "Sajaa_6_EOWR.pdf",
      "doc_name": "Sajaa_6_EOWR_<job-prefix>",
      "pages": [3, 14, 22],                    // 1-based page numbers that contributed hits
      "best_score": 0.81,
      "hit_count": 4
    }
  ],
  "attached_pages": [ /* same */ ],
  "tool_calls_made": 4,
  "followups": { "pending": true },           // ← marker (was: string[])
  "meta": {
    "rag_calls": 3,
    "include_images": false,
    "total_hits": 12,
    "verification": { "skipped": true },     // ← always skipped now
    "empty_response": false,
    "path": "guard_then_synth",
    "rag_invoked": true,
    "guard_input_tokens": 312,
    "guard_output_tokens": 47,
    "request_id": "uuid"
  }
}
```

**Render rules:**
- Treat `done` as "answer is final, render it."
- The model now cites sources inline by filename (e.g. "…NPT was 9.8% (Sajaa_6_EOWR.pdf, p.14)."). There are NO `[Sn]` markers to patch.
- Use `documents_used` to render a "Sources used" panel: one chip per document, click → deep-link to `/documents/{document_id}`. The `pages` array is suitable for a sub-list of page jumps.
- `sources` (chunk-level hits) is still emitted for FE features that need granular evidence (snippet preview, score badges).

### 3.2 `verification_update` (informational, always skipped)

```jsonc
{
  "event": "verification_update",
  "request_id": "uuid",
  "verification": { "skipped": true }
}
```

**Render rules:**
- The LLM-based citation verifier is permanently retired. This frame is kept in the wire protocol for forward compatibility and as a useful "hide any verifying spinner now" signal — it lands within milliseconds of `done`.
- No further action required. The FE can ignore the body or use it to advance internal state.

### 3.3 `followups_update` (NEW)

```jsonc
{
  "event": "followups_update",
  "request_id": "uuid",
  "followups": ["...", "...", "..."]
}
```

**Render rules:**
- Replace the followups skeleton with these chips.
- Empty array is valid → just hide the skeleton.

### 3.4 `complete` (NEW, terminal)

```jsonc
{
  "event": "complete",
  "request_id": "uuid",
  "session_id": "uuid",
  "meta": {
    "wall_ms_done":     14820,        // ms from receipt to the `done` frame
    "wall_ms_complete": 26410,        // ms from receipt to this frame
    "path": "error"                   // OPTIONAL — present only on error/edge paths
  }
}
```

**Render rules:**
- Close the `EventSource`.
- Mark the turn complete.
- Optional: log `wall_ms_done` / `wall_ms_complete` to client telemetry.

### 3.5 `thought` (NEW — model reasoning, engagement-only)

```jsonc
{
  "event": "thought",
  "request_id": "uuid",
  "text": "I should look up NPT in the SAJAA 6 EOWR. The user is asking about non-productive time, so I'll focus on the time-distribution table on p.14..."
}
```

`thought` frames are streamed by the synthesizer **before** the first `token` frame. They are Gemini 3's abridged reasoning summaries (NOT raw chain-of-thought — Google deliberately doesn't expose that). One synth turn typically produces 1–3 thought chunks totalling 100–400 chars.

**Render rules:**
- Render in a separate "Thinking…" panel above the streaming answer (collapsible). Append text as chunks arrive.
- Use this to keep the user engaged during the 4–10 s synth wait — do NOT mix into the answer body.
- Once the first `token` frame arrives, freeze the thought panel (no further thoughts will arrive). You may auto-collapse it at that point.
- `thought.text` is human-readable Markdown-safe text; render it as plain text or light prose styling. Do not parse for citations.
- Empty `text` shouldn't happen but treat as a no-op if it does.
- Thoughts are **transient** — they are NOT included in `done.answer`, NOT persisted to STM, and NOT replayed on session reload. Treat them as ephemeral UI candy.

---

## 4. Edge cases the FE must handle

### 4.1 Slash commands
Slash commands emit `token` → `done` → `complete` immediately, with no `verification_update` or `followups_update` (the `done` frame on slash paths still carries `pending` markers shape-wise but the updates simply will not arrive — `complete` lands directly after `done`). Treat absence of updates as fine: when `complete` arrives, finalise the UI state and remove any pending spinners.

### 4.2 Empty corpus soft-land
When the user has no documents indexed, the BE emits `token` (the soft-land message) → `done` → `complete`. Same handling as slash commands.

### 4.3 Agent error
On internal failure the BE emits a `done` frame with `status: "no_evidence"`, `meta.error: "agent_failed"`, then `complete` with `meta.path: "error"`. The FE should render the empty-state UI and not wait for verification/followups.

### 4.4 Verifier was retired

Verification is permanently disabled. `verification_update` is always `{skipped: true}` and arrives within milliseconds of `done`. There is no timeout to handle. Source attribution comes from (a) inline filename citations in `answer`, and (b) the `documents_used` array in `done`.

### 4.5 Client disconnect
If the FE closes the `EventSource` mid-stream the BE detects `CancelledError` and stops cleanly. The FE should not see a `complete` frame in this case (it disconnected first).

### 4.6 Stale updates after a new turn started
Each frame carries `request_id`. The FE **must** scope `verification_update` / `followups_update` handling to the matching turn. A late update from turn N must not patch turn N+1.

```ts
if (frame.request_id !== currentTurn.request_id) return;  // drop stale
```

### 4.7 No `complete` arrives within timeout
Recommended FE-side safety net: if `complete` has not arrived 30 s after `done`, give up on updates, hide pending UI affordances, and close the `EventSource`. Treat verification/followups as "unavailable for this turn."

---

## 5. Suggested FE state machine

```
idle
  ├─(open stream)→ connecting
  │                  └─(status: received)→ awaiting_first_token
  │                                          └─(token)→ streaming
  │                                                       └─(done)→ awaiting_updates
  │                                                                  ├─(verification_update + followups_update, any order)→ updates_received
  │                                                                  └─(complete)→ finalised
  └─(error / disconnect)→ failed
```

`awaiting_updates` UI state shows the answer + a small "verifying" + "followups loading" affordance. `complete` is the only transition into `finalised`.

---

## 6. FE migration checklist

- [ ] **Stop closing the `EventSource` on `done`.** Close on `complete` only.
- [ ] **Add a `verification_update` handler.** Patch the rendered answer + citation chips. Honour `corrected_answer` when present.
- [ ] **Add a `followups_update` handler.** Replace the followups skeleton.
- [ ] **Add a `complete` handler.** Close the `EventSource` and mark the turn done.
- [ ] **30 s post-`done` watchdog.** Self-heal if `complete` is delayed beyond reason.
- [ ] **`request_id` scoping.** Drop updates whose `request_id` does not match the current turn.
- [ ] **Render `done` immediately.** Do not gate the answer on verification.
- [ ] **Verifying spinner / followups skeleton.** Visual state for the brief gap between `done` and the updates.
- [ ] **Soft-fail on `verification.error`.** Do not block the user; surface as a non-blocking notice at most.

---

## 7. Test fixtures (recommended)

The FE should have unit tests covering at least these recorded streams:

1. **Happy path.** `received → guard → planning → searching → synthesizing → token× N → done → verification_update (clean) → followups_update → complete`.
2. **Verifier flags one citation.** Same as happy path but `verification_update.verification.unsupported_ids: ["S2"]`.
3. **Verifier returns `corrected_answer`.** Patch the rendered answer body.
4. **Verifier timeout.** `verification_update.verification.error: "timeout"`.
5. **Followups arrive before verification.** Order swapped; FE must handle both.
6. **Slash command.** `token → done → complete` only (no updates).
7. **Empty corpus.** Same as slash command.
8. **Agent error.** `done` with `meta.error: "agent_failed"` → `complete` with `meta.path: "error"`.

Capture these from `POST /chat/stream` against a dev backend or use the in-process `app.tools.probe_chat` harness as a reference.

---

## 8. Latency expectations

| Phase                       | Typical |
|-----------------------------|---------|
| `received`                  | ~10 ms  |
| First `token`               | 4–8 s   |
| Last `token` / `done`       | 10–16 s |
| `verification_update`       | +<100 ms after `done` (always skipped) |
| `followups_update`          | +3–6 s after `done` |
| `complete`                  | right after `followups_update` |

User-perceived "answer is ready" latency = `wall_ms_done` ≈ 10–16 s.
End-of-stream latency = `wall_ms_complete` ≈ 13–22 s.

---

## 9. Backwards compatibility

There is **no protocol toggle**. The progressive-finalisation protocol is the only protocol on `POST /chat/stream`. The previous monolithic-`done` shape is removed.

If you need to roll out the FE change behind a flag, do so on the FE side. The BE will not emit the legacy shape.

---

## 10. Quick reference — every event the stream can emit

| Event                  | Terminal? | Carries `request_id`? | Notes |
|------------------------|-----------|------------------------|-------|
| `status`               | no        | yes (on `received`)    | Phase markers: `received`, `guard`, `planning`, `searching`, `synthesizing`. |
| `thought`              | no        | yes                    | Model reasoning summary, streamed before answer tokens. Engagement-only — do NOT mix into the answer. |
| `token`                | no        | no                     | Streamed answer fragments, in order. |
| `done`                 | no        | yes (in `meta`)        | Answer is final; verification + followups pending. |
| `verification_update`  | no        | yes                    | Always `{skipped: true}` — informational only. |
| `followups_update`     | no        | yes                    | Followup suggestion chips. |
| `complete`             | **yes**   | yes                    | Close the `EventSource` here. |
