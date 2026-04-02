"""
LangChain Agent — Austin Trip Assistant

Uses create_agent (LangChain v1) with Anthropic claude-haiku-4-5.
Answers questions about the Austin trip schedule from docs/austin_trip.md.

Memory:   InMemorySaver checkpointer (multi-turn, keyed by thread_id)
Tracing:  LangSmith — activated via LANGCHAIN_TRACING_V2 + LANGCHAIN_API_KEY env vars
Streaming: stream_mode="messages" yields tokens as AgentChunkEvents

Reference: https://github.com/langchain-ai/voice-sandwich-demo
"""

from pathlib import Path

from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

# ── Austin trip doc ────────────────────────────────────────────────────────────

_DOCS_PATH = Path(__file__).parent.parent / "docs" / "austin_trip.md"
_AUSTIN_TRIP_CONTENT = _DOCS_PATH.read_text()

# ── Tool ───────────────────────────────────────────────────────────────────────

@tool
def lookup_austin_trip(query: str) -> str:
    """
    Look up information about the Austin trip schedule.
    Use this tool to answer any question about activities, restaurants,
    hotels, times, locations, budget, or logistics for the Austin trip.
    """
    # Return the full doc — the LLM does the extraction.
    # For a larger doc this would be a vector search, but the schedule
    # fits comfortably in the context window.
    return _AUSTIN_TRIP_CONTENT


# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """
You are a helpful travel assistant for an Austin, TX trip in May 2025.
Answer questions about the trip schedule, activities, restaurants, hotels,
transport, budget, and logistics.

Always use the lookup_austin_trip tool to retrieve accurate schedule details
before answering. Keep responses short — 1 to 3 sentences maximum. Your
responses will be spoken aloud via text-to-speech, so avoid markdown, bullet
points, or lists. Speak in plain, natural sentences.

Always respond in Spanish, regardless of the language the user speaks in.
""".strip()

# ── Agent ──────────────────────────────────────────────────────────────────────

agent = create_agent(
    model="anthropic:claude-haiku-4-5",
    tools=[lookup_austin_trip],
    system_prompt=_SYSTEM_PROMPT,
    checkpointer=InMemorySaver(),
)
