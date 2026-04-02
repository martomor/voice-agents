"""
AssemblyAI Real-Time Streaming STT

Connects to AssemblyAI's v3 WebSocket API for streaming speech-to-text.
Uses a producer-consumer pattern: audio chunks are sent concurrently
while transcript events arrive asynchronously.

Input:  PCM 16-bit audio bytes
Output: STTChunkEvent (partials) and STTOutputEvent (final per turn)

Reference: https://github.com/langchain-ai/voice-sandwich-demo
"""

import asyncio
import contextlib
import json
import os
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

import websockets
from websockets.client import WebSocketClientProtocol

from events import STTChunkEvent, STTEvent, STTOutputEvent


class AssemblyAISTT:
    def __init__(
        self,
        api_key: Optional[str] = None,
        sample_rate: int = 16000,
    ):
        self.api_key = api_key or os.getenv("ASSEMBLYAI_API_KEY")
        if not self.api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is required")

        self.sample_rate = sample_rate
        self._ws: Optional[WebSocketClientProtocol] = None
        self._connection_signal = asyncio.Event()
        self._session_ready = asyncio.Event()   # set after Begin is received
        self._close_signal = asyncio.Event()
        # Buffer audio until we have ≥200ms per chunk (safe above AssemblyAI's 50ms minimum)
        # At 16kHz int16: 200ms = 3200 samples = 6400 bytes
        self._min_chunk_bytes = int(sample_rate * 0.2) * 2
        self._audio_buffer = b""

    async def receive_events(self) -> AsyncIterator[STTEvent]:
        """Yield STT events as they arrive from AssemblyAI."""
        while not self._close_signal.is_set():
            _, pending = await asyncio.wait(
                [
                    asyncio.create_task(self._close_signal.wait()),
                    asyncio.create_task(self._connection_signal.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            with contextlib.suppress(asyncio.CancelledError):
                for task in pending:
                    task.cancel()

            if self._close_signal.is_set():
                break

            if self._ws and self._ws.close_code is None:
                self._connection_signal.clear()
                try:
                    async for raw_message in self._ws:
                        try:
                            message = json.loads(raw_message)
                            message_type = message.get("type")

                            if message_type == "Begin":
                                self._session_ready.set()  # now safe to send audio
                            elif message_type == "Turn":
                                transcript = message.get("transcript", "")
                                end_of_turn = message.get("end_of_turn", False)
                                if not transcript:
                                    pass  # empty partial, skip
                                elif end_of_turn:
                                    yield STTOutputEvent.create(transcript)
                                else:
                                    yield STTChunkEvent.create(transcript)
                            elif message_type == "Termination":
                                pass
                            else:
                                if "error" in message:
                                    print(f"[AssemblyAISTT] error: {message}", flush=True)
                                    break
                        except json.JSONDecodeError as e:
                            print(f"[AssemblyAISTT] JSON decode error: {e}", flush=True)
                            continue
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"[AssemblyAISTT] closed: {e.rcvd.code if e.rcvd else '?'} — {e.rcvd.reason if e.rcvd else '?'}", flush=True)

    async def send_audio(self, audio_chunk: bytes) -> None:
        """Buffer and send PCM audio — waits for Begin before sending."""
        await self._ensure_connection()
        # Wait for AssemblyAI to signal session is ready (Begin received)
        await self._session_ready.wait()
        self._audio_buffer += audio_chunk
        if len(self._audio_buffer) >= self._min_chunk_bytes:
            if self._ws and self._ws.close_code is None:
                await self._ws.send(self._audio_buffer)
            self._audio_buffer = b""

    async def close(self) -> None:
        """Close the WebSocket and signal all waiters to stop."""
        if self._ws and self._ws.close_code is None:
            await self._ws.close()
        self._ws = None
        self._close_signal.set()

    async def _ensure_connection(self) -> WebSocketClientProtocol:
        if self._close_signal.is_set():
            raise RuntimeError("AssemblyAISTT: connection attempted after close()")
        if self._ws and self._ws.close_code is None:
            return self._ws

        params = urlencode(
            {
                "sample_rate": self.sample_rate,
                "speech_model": "universal-streaming-multilingual",
            }
        )
        url = f"wss://streaming.assemblyai.com/v3/ws?{params}"
        self._ws = await websockets.connect(
            url, additional_headers={"Authorization": self.api_key}
        )
        self._connection_signal.set()
        return self._ws
