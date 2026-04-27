"""RAG agent — ReAct-style agent that searches documents and synthesizes answers.

Uses a simple state-machine loop:
  1. LLM decides whether to search or answer
  2. If search: call the RAG tool, feed results back
  3. If answer: return the synthesized response

Max tool calls per turn: 3 (prevents infinite loops).
Conversation memory: last 10 turns kept in-memory per session.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

from app.agents.rag_tools import (
    SearchResult,
    format_search_results,
    search_documents,
)
from app.config import Settings
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)

MAX_TOOL_CALLS = 3
MAX_HISTORY_TURNS = 10

SYSTEM_PROMPT = """\
You are WellSynth AI, an expert assistant for well operations and oilfield document analysis.

You have access to a tool called `search_documents` that searches through ingested well operation documents (drilling reports, well logs, completion reports, etc.).

## Instructions
- When the user asks a question, decide whether you need to search documents first.
- If you need information from documents, call the search_documents tool with a clear, specific search query.
- You may refine your search query and search again (up to 3 searches per turn) if the initial results are insufficient.
- Once you have enough context, synthesize a clear, accurate answer.
- Always cite your sources using [Source N] references from the search results.
- If no relevant documents are found, say so honestly. Never fabricate information.
- Keep answers concise and focused on the user's question.

## Tool Usage
To search documents, output a JSON block in this exact format:
```tool_call
{"tool": "search_documents", "query": "your search query here"}
```

To provide your final answer, just write your response directly (no JSON block).
"""


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "tool"
    content: str


@dataclass
class ChatSession:
    messages: list[ChatMessage] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append(ChatMessage(role=role, content=content))
        # Trim to last N turns (1 turn = user + assistant)
        max_messages = MAX_HISTORY_TURNS * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]


# In-memory session store (keyed by session_id)
_sessions: dict[str, ChatSession] = defaultdict(ChatSession)


def get_session(session_id: str) -> ChatSession:
    return _sessions[session_id]


def _build_messages(
    session: ChatSession,
    user_message: str,
) -> list[types.Content]:
    """Build the Gemini-compatible message list from session history."""
    contents: list[types.Content] = []

    # Add conversation history
    for msg in session.messages:
        if msg.role == "user":
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=msg.content)]))
        elif msg.role == "assistant":
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=msg.content)]))

    # Add current user message
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))

    return contents


_TOOL_CALL_PATTERN = re.compile(
    r"```tool_call\s*\n(\{.*?\})\s*\n```",
    re.DOTALL,
)


def _parse_tool_call(text: str) -> dict[str, str] | None:
    """Extract a tool_call JSON block from the LLM response."""
    match = _TOOL_CALL_PATTERN.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        if data.get("tool") == "search_documents" and "query" in data:
            return data
    except (json.JSONDecodeError, AttributeError):
        pass
    return None


@dataclass
class AgentResponse:
    """Final response from the RAG agent."""

    answer: str
    sources: list[dict[str, Any]]
    tool_calls_made: int


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
    """Run the RAG agent for a single user turn.

    The agent loop:
    1. Send message to LLM
    2. If LLM outputs a tool_call → execute search → append results → goto 1
    3. If LLM outputs a final answer → return it
    """
    session = get_session(session_id)
    client = genai.Client(api_key=settings.gemini_api_key)

    all_sources: list[dict[str, Any]] = []
    tool_calls_made = 0

    # Build initial message list
    messages = _build_messages(session, user_message)

    for iteration in range(MAX_TOOL_CALLS + 1):
        logger.info(
            "rag_agent_iteration",
            session_id=session_id,
            iteration=iteration,
            tool_calls_made=tool_calls_made,
        )

        # Call Gemini
        response = await client.aio.models.generate_content(
            model=settings.extraction_model,  # Gemini Flash — fast + cheap
            contents=messages,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )

        llm_text = response.text or ""

        # Check for tool call
        tool_call = _parse_tool_call(llm_text)

        if tool_call is None or tool_calls_made >= MAX_TOOL_CALLS:
            # Final answer — strip any stray tool_call blocks
            final_answer = _TOOL_CALL_PATTERN.sub("", llm_text).strip()
            if not final_answer:
                final_answer = llm_text.strip()

            # Persist to session history
            session.add_message("user", user_message)
            session.add_message("assistant", final_answer)
            session.sources = all_sources

            logger.info(
                "rag_agent_done",
                session_id=session_id,
                tool_calls_made=tool_calls_made,
                sources=len(all_sources),
            )

            return AgentResponse(
                answer=final_answer,
                sources=all_sources,
                tool_calls_made=tool_calls_made,
            )

        # Execute tool call
        search_query = tool_call["query"]
        tool_calls_made += 1

        logger.info(
            "rag_agent_tool_call",
            session_id=session_id,
            query=search_query,
            call_num=tool_calls_made,
        )

        results = await search_documents(
            search_query,
            embedder=embedder,
            vector_store=vector_store,
            limit=10,
            job_id=job_id,
            doc_name=doc_name,
        )

        # Collect sources
        for r in results:
            all_sources.append({
                "text": r.text,
                "score": r.score,
                "doc_name": r.doc_name,
                "page_num": r.page_num,
                "chunk_index": r.chunk_index,
            })

        # Format results and feed back to LLM
        formatted = format_search_results(results)
        tool_response = f"Search results for '{search_query}':\n\n{formatted}"

        # Append the LLM's tool call message and the tool response
        messages.append(
            types.Content(role="model", parts=[types.Part.from_text(text=llm_text)])
        )
        messages.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(
                    text=f"[Tool Response]\n{tool_response}\n\n"
                    "Now synthesize a complete answer based on these search results. "
                    "Cite sources using [Source N] format. "
                    "If you need more information, you can search again."
                )],
            )
        )

    # Fallback — shouldn't reach here
    session.add_message("user", user_message)
    session.add_message("assistant", "I was unable to generate a complete answer.")
    return AgentResponse(
        answer="I was unable to generate a complete answer. Please try rephrasing your question.",
        sources=all_sources,
        tool_calls_made=tool_calls_made,
    )
