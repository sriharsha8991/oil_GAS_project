"""RAG agent orchestrator — chains planner → retriever → assess → recover → synthesize.

Replaces the old ReAct loop with a deterministic 2-call pipeline plus a
recovery ladder for weak retrievals.
"""

from __future__ import annotations

from google import genai

from app.agents.rag.planner import plan as plan_query
from app.agents.rag.planner import rewrite_for_gaps
from app.agents.rag.retriever import (
    assess,
    assessment_to_dict,
    broaden_retry,
    expand_neighbors,
    is_hard_floor,
    mmr_diversify_chunks,
    retrieve,
    truncate_for_synth,
)
from app.agents.rag.synthesizer import synthesize, synthesize_no_evidence
from app.agents.rag.types import AgentResponse, Bundle, get_session
from app.config import Settings
from app.storage.file_storage import FileStorage
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)


async def run_agent(
    user_message: str,
    *,
    session_id: str,
    settings: Settings,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    job_id: str | None = None,
    doc_name: str | None = None,
) -> AgentResponse:
    """Single-turn pipeline:

      1. Planner emits a QueryPlan.
      2. If planner asks for clarification → return early.
      3. Fan-out retrieval across the 3 tools in parallel.
      4. Assess facet coverage + top score.
      5. If insufficient: rung 1 (broaden) → rung 2 (gap-aware rewrite).
      6. If still under hard floor: return no_evidence.
      7. Otherwise synthesize a JSON envelope answer (with multimodal grounding)
         and validate citations.
    """
    session = get_session(session_id)
    client = genai.Client(api_key=settings.gemini_api_key)
    storage = FileStorage(settings.output_dir)

    model_calls = 0  # counts LLM calls (planner + rewrite + synth)

    # Snapshot history BEFORE this turn so the synthesizer sees the same
    # context the planner did (and so we can pass it to soft-landing too).
    history_text = "\n".join(
        f"{m.role}: {m.content}" for m in session.messages[-6:]
    )

    # ------------------------------------------------------------------
    # 1. Plan
    # ------------------------------------------------------------------
    plan = await plan_query(
        client=client, settings=settings,
        user_message=user_message, session=session,
    )
    model_calls += 1

    # 2. Early exit if the planner wants clarification.
    if plan.needs_followup and plan.followup_question:
        logger.info("rag_clarification_requested", question=plan.followup_question)
        # Persist this turn so the next turn's planner sees the full thread
        # (the user's original question + our clarification ask). Without
        # this, follow-up answers like "the deeper one" lose context.
        preamble = (
            "Before I search, I need a quick clarification so I can give you a "
            "precise answer rather than a generic one."
        )
        session.add("user", user_message)
        session.add("assistant", f"{preamble} {plan.followup_question}")
        return AgentResponse(
            status="clarification_requested",
            answer=preamble,
            followup=plan.followup_question,
            tool_calls_made=model_calls,
            plan=plan.raw or None,
        )

    # ------------------------------------------------------------------
    # 3. Retrieve
    # ------------------------------------------------------------------
    bundle = await retrieve(
        plan,
        embedder=embedder, vector_store=vector_store,
        job_id=job_id, doc_name=doc_name,
    )

    # 3b. Coreferent-followup seeding — when the planner had to rewrite
    # the user's message ("the deeper one", "more details", "why?"), the
    # user is implicitly asking about evidence the previous answer cited.
    # Seed the new bundle with that evidence so the synthesizer can keep
    # the same subject thread instead of hallucinating a pivot.
    resolved = (plan.resolved_question or "").strip()
    is_followup = bool(
        resolved
        and resolved.lower() != user_message.strip().lower()
        and (session.last_chunks or session.last_entities or session.last_images)
    )
    if is_followup:
        seed = Bundle(
            chunks=list(session.last_chunks),
            entities=list(session.last_entities),
            images=list(session.last_images),
        )
        bundle.merge(seed)
        logger.info(
            "rag_followup_seeded",
            seeded_chunks=len(seed.chunks),
            seeded_entities=len(seed.entities),
            seeded_images=len(seed.images),
        )

    # 4. Assess
    a = assess(plan, bundle)
    logger.info("rag_assessment", **assessment_to_dict(a))

    # ------------------------------------------------------------------
    # 5. Recovery ladder
    # ------------------------------------------------------------------
    if not a.sufficient:
        # Rung 1 — broaden filters/threshold and merge hits.
        logger.info("rag_recovery_rung1_broaden")
        b1 = await broaden_retry(
            plan,
            embedder=embedder, vector_store=vector_store,
            job_id=job_id, doc_name=doc_name,
        )
        bundle.merge(b1)
        a = assess(plan, bundle)
        logger.info("rag_assessment_after_broaden", **assessment_to_dict(a))

    if not a.sufficient and a.missing_facets:
        # Rung 2 — gap-aware query rewrite.
        logger.info("rag_recovery_rung2_rewrite", missing=a.missing_facets)
        rewritten = await rewrite_for_gaps(
            client=client, settings=settings,
            user_message=user_message, original=plan,
            missing_facets=a.missing_facets,
        )
        model_calls += 1
        b2 = await retrieve(
            rewritten,
            embedder=embedder, vector_store=vector_store,
            job_id=job_id, doc_name=doc_name,
            score_threshold=0.2,
        )
        bundle.merge(b2)
        a = assess(plan, bundle)
        logger.info("rag_assessment_after_rewrite", **assessment_to_dict(a))

    # ------------------------------------------------------------------
    # 6. Hard floor — soft-land the user instead of a flat refusal.
    # ------------------------------------------------------------------
    if is_hard_floor(a):
        logger.info("rag_hard_floor_soft_land", **assessment_to_dict(a))
        response = await synthesize_no_evidence(
            client=client, settings=settings,
            user_message=user_message, plan=plan, bundle=bundle,
            history_text=history_text,
        )
        model_calls += 1
        response.tool_calls_made = model_calls
        response.plan = plan.raw or None
        response.assessment = assessment_to_dict(a)
        # Persist the soft-refusal too, so the user can ask "what about X
        # then?" and the planner will see what we already declined.
        if response.answer:
            session.add("user", user_message)
            session.add("assistant", response.answer)
        return response

    # ------------------------------------------------------------------
    # 7. Synthesize
    # ------------------------------------------------------------------
    # MMR over chunks first to drop near-duplicate paragraphs that bloat
    # the synthesizer prompt without adding information.
    mmr_diversify_chunks(bundle, k=10, lambda_=0.7)
    trimmed = truncate_for_synth(bundle)
    # Same-page neighbor expansion of the top-3 chunks to preserve
    # cross-boundary information — but only when the top hit isn't already
    # a high-confidence direct match. If top_score > 0.90 the answer is
    # almost certainly inside that single chunk and adding noise hurts.
    if a.top_score <= 0.90:
        await expand_neighbors(trimmed, vector_store=vector_store, top_k=3,
                               job_id=job_id)
    else:
        logger.info("rag_neighbors_skipped_high_score", top_score=a.top_score)
    response = await synthesize(
        client=client, settings=settings, storage=storage,
        user_message=user_message, plan=plan, bundle=trimmed,
        assessment=a, history_text=history_text,
    )
    model_calls += 1
    response.tool_calls_made = model_calls
    response.plan = plan.raw or None
    response.assessment = assessment_to_dict(a)

    # Persist conversation tail.
    if response.answer:
        session.add("user", user_message)
        session.add("assistant", response.answer)
        # Cache top-K of THIS turn's evidence so the next turn can seed
        # from it on coreferent follow-ups (see step 3b).
        session.last_chunks = list(trimmed.chunks[:6])
        session.last_entities = list(trimmed.entities[:8])
        session.last_images = list(trimmed.images[:4])

    logger.info(
        "rag_done",
        status=response.status,
        sources=len(response.sources),
        evidence=len(response.evidence),
        claims=len(response.claims),
        unanswered=len(response.unanswered),
        model_calls=model_calls,
    )
    return response
