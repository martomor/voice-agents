"""
Voice Agent Pipeline — STT > Agent > TTS (The Sandwich)

Wires AssemblyAI STT, LangChain agent, and ElevenLabs TTS into a single
composable RunnableGenerator pipeline.

Usage:
    output_stream = pipeline.atransform(audio_byte_stream)
    async for event in output_stream:
        # handle VoiceAgentEvent

Reference: https://github.com/langchain-ai/voice-sandwich-demo
"""

import asyncio
import contextlib
from typing import AsyncIterator, Optional
from uuid import uuid4

from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableGenerator

from agent import agent
from assemblyai_stt import AssemblyAISTT
from elevenlabs_tts import ElevenLabsTTS
from events import (
    AgentChunkEvent,
    AgentEndEvent,
    ToolCallEvent,
    ToolResultEvent,
    VoiceAgentEvent,
)
from utils import merge_async_iters


# ── Stage 1: STT ───────────────────────────────────────────────────────────────

async def _stt_stream(
    audio_stream: AsyncIterator[bytes],
) -> AsyncIterator[VoiceAgentEvent]:
    """
    Audio bytes → STT events.

    Launches audio sending as a background task (producer) while
    receiving transcript events in the foreground (consumer).
    """
    stt = AssemblyAISTT(sample_rate=16000)

    async def send_audio():
        try:
            async for chunk in audio_stream:
                await stt.send_audio(chunk)
        finally:
            await stt.close()

    send_task = asyncio.create_task(send_audio())

    try:
        async for event in stt.receive_events():
            yield event
    finally:
        with contextlib.suppress(asyncio.CancelledError):
            send_task.cancel()
            await send_task
        await stt.close()


# ── Stage 2: Agent ─────────────────────────────────────────────────────────────

_AGENT_STOP = object()  # sentinel to terminate the agent-events consumer


async def _agent_stream(
    event_stream: AsyncIterator[VoiceAgentEvent],
) -> AsyncIterator[VoiceAgentEvent]:
    """
    STT events → STT events + agent events.

    Upstream events are forwarded immediately (never blocked by agent work).
    Agent invocations run as background tasks so that STT partial events
    (e.g. the user saying "stop" mid-response) reach downstream stages
    without waiting for the current agent turn to finish.

    When a new stt_output arrives, any in-flight agent task is cancelled
    before the new one starts.
    """
    thread_id = str(uuid4())
    agent_q: asyncio.Queue = asyncio.Queue()
    current_task: Optional[asyncio.Task] = None

    async def run_agent(transcript: str) -> None:
        # Use a turn-specific thread_id so cancelled turns don't pollute memory
        turn_id = f"{thread_id}_{uuid4()}"
        print(f"[agent] running: {transcript!r}", flush=True)
        try:
            stream = agent.astream(
                {"messages": [HumanMessage(content=transcript)]},
                {"configurable": {"thread_id": turn_id}},
                stream_mode="messages",
            )
            async for message, _ in stream:
                if isinstance(message, AIMessage):
                    if message.text:
                        await agent_q.put(AgentChunkEvent.create(message.text))
                    if hasattr(message, "tool_calls") and message.tool_calls:
                        for tc in message.tool_calls:
                            await agent_q.put(ToolCallEvent.create(
                                id=tc.get("id", str(uuid4())),
                                name=tc.get("name", "unknown"),
                                args=tc.get("args", {}),
                            ))
                if isinstance(message, ToolMessage):
                    await agent_q.put(ToolResultEvent.create(
                        tool_call_id=getattr(message, "tool_call_id", ""),
                        name=getattr(message, "name", "unknown"),
                        result=str(message.content) if message.content else "",
                    ))
            print(f"[agent] done", flush=True)
        except asyncio.CancelledError:
            print(f"[agent] cancelled", flush=True)
        except Exception as e:
            print(f"[agent] ERROR: {e}", flush=True)
        finally:
            await agent_q.put(AgentEndEvent.create())

    async def upstream_events() -> AsyncIterator[VoiceAgentEvent]:
        nonlocal current_task
        async for event in event_stream:
            yield event
            if event.type == "stt_output":
                if current_task and not current_task.done():
                    print(f"[interrupt] cancelling in-flight agent task", flush=True)
                    current_task.cancel()
                current_task = asyncio.create_task(run_agent(event.transcript))
        # Upstream exhausted — signal agent consumer to stop
        await agent_q.put(_AGENT_STOP)

    async def agent_events() -> AsyncIterator[VoiceAgentEvent]:
        while True:
            item = await agent_q.get()
            if item is _AGENT_STOP:
                return
            yield item

    async for event in merge_async_iters(upstream_events(), agent_events()):
        yield event


# ── Stage 3: TTS ───────────────────────────────────────────────────────────────

async def _tts_stream(
    event_stream: AsyncIterator[VoiceAgentEvent],
) -> AsyncIterator[VoiceAgentEvent]:
    """
    STT + agent events → all events + TTS audio chunks.

    Merges two concurrent streams:
    - process_upstream(): passes through events and buffers agent tokens,
      sending the full response to ElevenLabs on AgentEndEvent.
    - tts.receive_events(): yields TTSChunkEvents as audio arrives.

    The merge means audio playback starts before the agent finishes.
    When stt_output arrives mid-response, the ElevenLabs connection is
    interrupted and the partial buffer is discarded.
    """
    tts = ElevenLabsTTS()

    async def process_upstream() -> AsyncIterator[VoiceAgentEvent]:
        buffer: list[str] = []
        interrupted = False
        async for event in event_stream:
            yield event
            if event.type == "stt_output":
                if tts._ws is not None:
                    print(f"[interrupt] cancelling in-flight TTS", flush=True)
                    interrupted = True
                await tts.interrupt()
                buffer = []
            elif event.type == "agent_chunk":
                if not interrupted:
                    buffer.append(event.text)
            elif event.type == "agent_end":
                print(f"[tts] agent_end — buffer={len(buffer)} chars, interrupted={interrupted}", flush=True)
                if buffer and not interrupted:
                    print(f"[tts] sending to ElevenLabs: {(''.join(buffer))[:80]!r}", flush=True)
                    await tts.send_text("".join(buffer))
                    await tts.send_text("")  # EOS — tells ElevenLabs input is complete
                buffer = []
                interrupted = False

    try:
        async for event in merge_async_iters(process_upstream(), tts.receive_events()):
            yield event
    finally:
        await tts.close()


# ── Pipeline composition ───────────────────────────────────────────────────────

pipeline = (
    RunnableGenerator(_stt_stream)
    | RunnableGenerator(_agent_stream)
    | RunnableGenerator(_tts_stream)
)
